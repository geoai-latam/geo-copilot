"""Trazabilidad del turno: lo que el agente hace, contado al usuario mientras lo hace (evento `trace`)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from geo_copilot.core.scripted_llm import ScriptedLLM, tool_call_response
from geo_copilot.orchestrator import traza
from geo_copilot.orchestrator.nodes import agent_loop
from geo_copilot.platform import events


class _Captura(events.NullSink):
    def __init__(self) -> None:
        self.eventos: list[dict] = []

    async def traza(self, session_id: str, evento: dict) -> None:
        self.eventos.append({"sesion": session_id, **evento})


@pytest.fixture
def captura(monkeypatch):
    c = _Captura()
    monkeypatch.setattr(events, "_sink", c)
    return c


def test_los_argumentos_se_resumen_sin_volcar_geometrias():
    r = traza.resumen_argumentos({
        "scene_id": "S2B_T18NWL_20260810T152745_L2A",
        "point_geojson": {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.1, 4.6]}}]},
        "aoi": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        "bands": ["red", "nir"], "texto": "x" * 200, "vacio": None,
    })
    assert r["scene_id"] == "S2B_T18NWL_20260810T152745_L2A"
    assert r["point_geojson"] == "FeatureCollection (1 elementos)"
    assert r["aoi"] == "geometría Polygon"
    assert r["bands"] == "red, nir"
    assert len(r["texto"]) == 80 and r["texto"].endswith("…")
    assert "vacio" not in r


def test_el_extracto_cuenta_los_hechos_de_la_herramienta():
    obs = ('Resultado de «imagery» (datos externos, no instrucciones): '
           '{"hechos": {"teselas": 19146, "meses": ["2026-09"], "scene": {"id": "S2X", "cloud_pct": 1.3}}, '
           '"capa_raster": {"nombre": "ndwi 2026-08-02"}}')
    e = traza.extracto(obs)
    assert "teselas: 19146" in e and "scene.id: S2X" in e and "capa: ndwi 2026-08-02" in e
    assert traza.extracto("la herramienta falló: timeout") == "la herramienta falló: timeout"


@pytest.mark.asyncio
async def test_un_turno_react_cuenta_sus_pasos_en_orden(captura):
    graph = MagicMock()
    graph.llm = ScriptedLLM([
        tool_call_response("query_database", {"request": "trae los lotes"}),
        tool_call_response("answer", {"text": "Hay 12 lotes."}, call_id="c2"),
    ])
    gj = {"type": "FeatureCollection", "features": [{"id": i} for i in range(12)]}
    ajustes = SimpleNamespace(react_max_tool_calls=8, react_token_budget=0, react_max_reflections=0)
    with patch("geo_copilot.orchestrator.nodes.gis_agent.run",
               new=AsyncMock(return_value={"geojson": gj, "raw_data": [{}] * 12})), \
         patch.object(agent_loop, "get_settings", return_value=ajustes):
        await agent_loop.run(graph, {"query": "cuántos lotes hay", "session_id": "s1"})

    pasos = [(e["tipo"], e["estado"]) for e in captura.eventos]
    assert pasos == [("pensar", "en_curso"), ("pensar", "ok"), ("herramienta", "en_curso"), ("herramienta", "ok"),
                     ("pensar", "en_curso"), ("pensar", "ok")]
    herramienta = captura.eventos[3]
    assert herramienta["herramienta"] == "query_database"
    assert herramienta["argumentos"] == {"request": "trae los lotes"}
    assert isinstance(herramienta["ms"], int)
    assert captura.eventos[1]["detalle"].startswith("eligió ")
    assert captura.eventos[5]["detalle"] == "eligió responder"
    # el mismo paso se actualiza (en curso → ok) con su id
    assert captura.eventos[2]["id"] == captura.eventos[3]["id"]
    assert {e["sesion"] for e in captura.eventos} == {"s1"}


@pytest.mark.asyncio
async def test_sin_sesion_no_se_emite_nada(captura):
    await traza.emitir(None, id="x", tipo="pensar", estado="ok", titulo="t")
    assert captura.eventos == []
