"""V5 acumulada F0–F6: una capa cargada con FILTRO no es la tabla del catastro.

Con «Lotes Manzana 004503009» (4) y «Construcciones Manzana 004503009» (9) cargadas y un buffer
de 500 m, a «¿cuántas construcciones del catastro caen dentro de ese buffer?» el agente cruzó la
capa de la manzana y respondió «9 construcciones del catastro» (son ~8.200). El bloque del
workspace ahora dice, de su procedencia, que esas capas son SUBCONJUNTOS de su tabla.

Se mide la TASA con el LLM real (en serie). Acierta si cuenta en la BD (query_database) o si,
contando la capa, deja claro que es la de la manzana; falla si da el 9 como el del catastro.

Ejecutar: pytest -m llm tests/test_llm_subconjunto.py -s
"""
from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

CORRIDAS = 5
SQL_LOTES = ("SELECT l.lotcodigo, l.shape FROM catastro.lotes l WHERE l.manzcodigo = '004503009'")
SQL_CONSTR = ("SELECT c.objectid, c.connpisos, c.shape FROM catastro.construcciones c "
              "JOIN catastro.lotes l ON l.lotcodigo = c.lotecodigo WHERE l.manzcodigo = '004503009'")
MAPA = {"layers": [
    {"id": "layer-1", "name": "Lotes Manzana 004503009", "kind": "vector-geojson", "geometry_type": "Polygon",
     "feature_count": 4, "fields": ["lotcodigo"], "visible": True, "dataset_id": "ds_1111111111111111"},
    {"id": "layer-2", "name": "Construcciones Manzana 004503009", "kind": "vector-geojson",
     "geometry_type": "Polygon", "feature_count": 9, "fields": ["objectid", "connpisos"], "visible": True,
     "dataset_id": "ds_2222222222222222"},
    {"id": "layer-3", "name": "Buffer 500 m de Lotes Manzana 004503009", "kind": "vector-geojson",
     "geometry_type": "MultiPolygon", "feature_count": 1, "fields": [], "visible": True, "is_active": True,
     "dataset_id": "ds_3333333333333333"},
]}


def _veredicto(llamadas: list[tuple[str, dict]], respuesta: str) -> str:
    """bd = contó en la tabla; subconjunto_dicho = dio el 9 diciendo que NO es el total; FALSO = dio
    el 9 como el del catastro. (Una primera versión aceptaba «manzana» en el texto y daba por
    buena «en el buffer de la manzana caen 9 construcciones del catastro»: mal.)"""
    if any(n == "query_database" for n, _ in llamadas):
        return "bd"
    t = respuesta.lower()
    if re.search(r"\b9\b", t):
        aclara = re.search(r"no (es|son|corresponde)[^.]*(total|tabla|catastro completo|todo el catastro)|"
                           r"solo (las|a las)[^.]*(cargad|de la capa|de la manzana)|subconjunto", t)
        return "subconjunto_dicho" if aclara else "FALSO"
    return "otro"


async def _correr(monkeypatch, *, con_hecho: bool) -> list[str]:
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.orchestrator.nodes import agent_loop
    from geo_copilot.platform.capabilities import ToolOutcome
    from geo_copilot.platform.contracts import FieldInfo, LayerRef, Provenance, WorkspaceTable

    def ref(ds, nombre, geom, n, cap, sql=None, campos=("nombre",)):
        return LayerRef(id=ds, name=nombre, kind="vector", provider="core", crs="EPSG:4326", geometry_type=geom,
                        feature_count=n, fields=[FieldInfo(name=c, type="string") for c in campos],
                        storage=WorkspaceTable(schema_name="ws_0123456789abcdef", table="d_" + ds[3:]),
                        provenance=Provenance(capability=cap, produced_at=datetime.now(UTC), sql=sql))

    refs = [
        ref("ds_1111111111111111", "Lotes Manzana 004503009", "Polygon", 4, "core.query_data", SQL_LOTES,
            ("lotcodigo",)),
        ref("ds_2222222222222222", "Construcciones Manzana 004503009", "Polygon", 9, "core.query_data", SQL_CONSTR,
            ("objectid", "connpisos")),
        ref("ds_3333333333333333", "Buffer 500 m de Lotes Manzana 004503009", "MultiPolygon", 1, "core.buffer"),
    ]
    por_id = {r.id: r for r in refs}

    async def get(_ws, ds):
        return por_id.get(ds)

    store = SimpleNamespace(list_datasets=AsyncMock(return_value=refs), get=get)
    monkeypatch.setattr(ce, "store_actual", lambda: store)
    from geo_copilot.platform.workspace import ops

    async def union_espacial(_s, _ws, a, b, **_k):
        n = por_id[a].feature_count if a in por_id else 0
        return SimpleNamespace(ref=None, hechos={
            "operacion": "union_espacial", "a": por_id.get(a).name if a in por_id else a,
            "b": por_id.get(b).name if b in por_id else b, "parejas": n, "elementos_de_a_con_pareja": n,
            "elementos_de_a": n})

    monkeypatch.setattr(ops, "union_espacial", union_espacial)
    if not con_hecho:
        monkeypatch.setattr(ce, "_subconjunto_de_la_bd", lambda sql: None)
    llm = await get_real_llm_or_skip()
    veredictos = []
    for _ in range(CORRIDAS):
        llamadas: list[tuple[str, dict]] = []

        async def espia(g, w, name, args, _l=llamadas):
            _l.append((name, dict(args or {})))
            if name == "ws_spatial_join":
                # la herramienta REAL (su observación, con o sin el hecho), sobre la operación simulada
                return await ce._join(None, {"session_id": "x"}, args)
            if name == "query_database":
                return ToolOutcome("query_data: 8230 elemento(s) (resultado COMPLETO: no llegó al LIMIT; es "
                                   "exacto). Ya es una capa con geometría en el mapa.", success=True)
            return ToolOutcome(f"{name}: hecho", success=True)

        monkeypatch.setattr(agent_loop, "dispatch_tool", espia)
        g = MagicMock()
        g.llm = llm
        g.agent_metrics = None
        estado = await agent_loop.run(g, {
            "query": "¿cuántas construcciones del catastro caen dentro de ese buffer?", "session_id": "x",
            "map_context": MAPA, "messages": [{"agent": "router", "data": {"reasoning": (
                "El usuario quiere contar las construcciones del catastro que caen dentro del buffer de 500 m "
                "ya creado.")}}]})
        respuesta = str((estado or {}).get("final_response") or "")
        v = _veredicto(llamadas, respuesta)
        print(f"[subconjunto con_hecho={con_hecho}] {v} <- {[n for n, _ in llamadas]} | {respuesta[:600]}")
        veredictos.append(v)
    return veredictos


@pytest.mark.asyncio
async def test_una_capa_filtrada_no_se_cuenta_como_la_tabla_del_catastro(monkeypatch):
    veredictos = await _correr(monkeypatch, con_hecho=True)
    assert "FALSO" not in veredictos, veredictos
    assert sum(v in ("bd", "subconjunto_dicho") for v in veredictos) >= CORRIDAS - 1, veredictos


@pytest.mark.asyncio
async def test_linea_base_sin_el_hecho(monkeypatch):
    """A/B: el mismo caso SIN el hecho de procedencia (para medir que el arreglo es la causa).
    Solo con MEDIR_LINEA_BASE=1; no aserta (es una medición)."""
    import os

    if os.environ.get("MEDIR_LINEA_BASE") != "1":
        pytest.skip("medición manual: MEDIR_LINEA_BASE=1")
    print(await _correr(monkeypatch, con_hecho=False))
