"""FH.11 contra PostGIS REAL: un proyecto se guarda con su estado, sus datasets dejan de vencer y
al reabrirlo la sesión de conversación se reconstruye con su historial (el agente recuerda).

Ejecutar: pytest -m postgis tests/test_fh11_proyectos_postgis.py
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from geo_copilot.platform.workspace import DatasetStore
from tests.test_fh5_filtros_postgis import PROC, _lotes

pytestmark = [pytest.mark.integration, pytest.mark.postgis, pytest.mark.asyncio(loop_scope="session")]


async def test_guardar_y_reabrir_un_proyecto_reconstruye_la_conversacion(workspace_pool, monkeypatch):
    import httpx

    from geo_copilot.api.app import create_app
    from geo_copilot.orchestrator.conversation import ConversationManager

    store, ws = DatasetStore(workspace_pool), f"sesion-{uuid.uuid4().hex[:12]}"
    ref = await store.ingest_features(ws, "Lotes", _lotes(), crs="EPSG:4326", provenance=PROC)
    estado = {"capas": [{"tipo": "dataset", "datasetId": ref.id, "name": "Lotes", "visible": True,
                         "filtro": [{"field": "estrato", "op": "=", "value": 3}]}],
              "vistas": [{"id": "v1", "nombre": "Centro", "bbox": [-74.1, 4.6, -74.0, 4.7]}],
              "chat": [{"role": "user", "content": "trae los lotes"},
                       {"role": "assistant", "content": "Cargué 5 lotes en «Lotes»."}],
              "camara": {"center": [-74.05, 4.65], "zoom": 15}}
    gestor = ConversationManager()
    monkeypatch.setattr("geo_copilot.api.dependencies.get_app_state", lambda: SimpleNamespace(dataset_store=store))
    app = create_app()
    from geo_copilot.api.dependencies import get_conversation_manager

    app.dependency_overrides[get_conversation_manager] = lambda: gestor
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        g = await http.post("/api/v1/proyectos", json={"session_id": ws, "nombre": "Finca", "estado": estado})
        assert g.status_code == 201, g.text
        pid = g.json()["id"]
        # guardar otra vez con el mismo nombre REEMPLAZA (mismo id)
        g2 = await http.post("/api/v1/proyectos", json={"session_id": ws, "nombre": "Finca", "estado": estado})
        assert g2.json()["id"] == pid
        lista = (await http.get("/api/v1/proyectos")).json()["proyectos"]
        assert any(p["id"] == pid and p["capas"] == 1 for p in lista)

        # la sesión ya no existe (se cerró el navegador y venció): reabrir la reconstruye
        assert gestor.get_session(ws) is None
        a = (await http.post(f"/api/v1/proyectos/{pid}/abrir")).json()
        assert a["session_id"] == ws and a["sesion_viva"] is False and a["estado"]["vistas"][0]["nombre"] == "Centro"
        historia = gestor.get_session(ws).history
        assert [(m.role, m.content) for m in historia] == [("user", "trae los lotes"),
                                                          ("assistant", "Cargué 5 lotes en «Lotes».")]
        # reabrir con la sesión viva NO la vacía ni duplica el historial (R0.11)
        again = (await http.post(f"/api/v1/proyectos/{pid}/abrir")).json()
        assert again["sesion_viva"] is True and len(gestor.get_session(ws).history) == 2
        assert (await http.post("/api/v1/proyectos/pr_0000000000000000/abrir")).status_code == 404
        assert (await http.post("/api/v1/proyectos/../abrir")).status_code in (400, 404)

    # los datasets del proyecto ya no vencen
    fila = await store.hechos(ws, "SELECT expires_at = 'infinity'::timestamptz AS eterno FROM ws_meta.datasets WHERE id = $1",
                              (ref.id,))
    assert fila["eterno"] is True
