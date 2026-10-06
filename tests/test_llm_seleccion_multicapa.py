"""V5 acumulada F0–F6: con la selección repartida en VARIAS capas (la caja selecciona en todas
las visibles) y el chip de alcance en «4 seleccionados de Lotes», «¿cuántas hectáreas suman?»
midió la capa ACTIVA (9 construcciones, 0,35 ha) en vez de los 4 lotes (0,77 ha).

Se mide la TASA con el LLM real (en serie): acierta si mide la selección de lotes (`seleccion`)
o la capa de lotes entera (aquí son los mismos 4); falla si mide otra capa.

Ejecutar: pytest -m llm tests/test_llm_seleccion_multicapa.py -s
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

CORRIDAS = 5


def _capa(i, nombre, geom, n, ds, sel, activa=False, campos=("objectid",)):
    return {"id": f"layer-{i}", "name": nombre, "kind": "vector-geojson", "geometry_type": geom, "feature_count": n,
            "fields": list(campos), "visible": True, "is_active": activa, "dataset_id": ds,
            "seleccion": {"ids": list(range(sel)), "count": sel, "origin": "box"}}


MAPA = {
    "layers": [
        _capa(1, "Lotes Manzana 004503009", "Polygon", 4, "ds_1111111111111111", 4, campos=("lotcodigo",)),
        _capa(2, "Construcciones Manzana 004503009", "Polygon", 9, "ds_2222222222222222", 9),
        _capa(3, "Buffer 500 m de Lotes Manzana 004503009", "MultiPolygon", 1, "ds_3333333333333333", 1),
        _capa(4, "Construcciones Manzana 004503009 × Buffer 500 m de Lotes Manzana 004503009 (within)", "Polygon", 9,
              "ds_4444444444444444", 9, activa=True),
    ],
    "viewport": {"bbox": [-74.14, 4.60, -74.13, 4.61], "zoom": 18},
    "alcance_seleccion": True,
}
BUENOS = {"seleccion", "ds_1111111111111111", "layer-1"}


def _veredicto(llamadas: list[tuple[str, dict]]) -> str:
    medidas = [a for n, a in llamadas if n in ("ws_measure", "ws_add_measure")]
    if not medidas:
        return f"sin medir ({[n for n, _ in llamadas]})"
    ds = str(medidas[0].get("dataset") or "").strip("[]")
    return "ok" if ds in BUENOS else f"MAL: midió {ds}"


@pytest.mark.asyncio
async def test_con_seleccion_en_varias_capas_suma_la_del_alcance(monkeypatch):
    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.orchestrator.nodes import agent_loop
    from geo_copilot.platform.capabilities import ToolOutcome

    monkeypatch.setattr(ce, "store_actual", lambda: object())  # como en la app: hay workspace
    llm = await get_real_llm_or_skip()
    veredictos = []
    for _ in range(CORRIDAS):
        llamadas: list[tuple[str, dict]] = []

        async def espia(g, w, name, args, _l=llamadas):
            _l.append((name, dict(args or {})))
            if name in ("ws_measure", "ws_add_measure"):
                return ToolOutcome(json.dumps({"hechos": {"elementos": 4, "area_total_m2": 7662.41,
                                                          "area_total_ha": 0.7662}}), success=True)
            return ToolOutcome(f"{name}: hecho", success=True)

        monkeypatch.setattr(agent_loop, "dispatch_tool", espia)
        g = MagicMock()
        g.llm = llm
        g.agent_metrics = None
        await agent_loop.run(g, {"query": "¿cuántas hectáreas suman?", "session_id": "x", "map_context": MAPA,
                                 "messages": [{"agent": "router", "data": {"reasoning": (
                                     "El usuario pregunta por la suma del área en hectáreas de la selección actual "
                                     "de lotes (4 seleccionados).")}}]})
        v = _veredicto(llamadas)
        print(f"[multicapa] {v} <- {llamadas}")
        veredictos.append(v)
    assert sum(v == "ok" for v in veredictos) >= CORRIDAS - 1, veredictos
