"""Auditoría pre-producción: cortes silenciosos y reglas que decidían por el LLM.

Cada caso fijaba un comportamiento en el que el código recortaba o decidía sin decirlo; ahora
el corte se DICE (cuántos faltan) o el dato llega como hecho y decide el LLM.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _capa(i: int, **extra) -> dict:
    return {"id": f"layer-{i}", "name": f"Capa {i}", "kind": "vector-geojson", "geometry_type": "Polygon",
            "feature_count": 3, "fields": [f"c{j}" for j in range(30)], "visible": True, **extra}


def test_todas_las_capas_del_mapa_llegan_al_agente():
    """Antes `layers[:10]`: con 12 capas, la undécima no existía para el agente."""
    from geo_copilot.core import formatters

    texto = formatters.format_map_context({"layers": [_capa(i) for i in range(12)]})
    assert "[layer-11]" in texto
    assert "c24" in texto and "(+5)" in texto  # 25 campos nombrados y el resto contado

    muchas = formatters.format_map_context({"layers": [_capa(i) for i in range(45)]})
    assert '[layer-44] "Capa 44" — visible' in muchas  # más allá del detalle: en una línea


def test_las_acciones_que_no_se_listan_se_cuentan():
    from geo_copilot.core import formatters

    acciones = [{"tipo": "zoom", "t": i} for i in range(20)]
    assert "5 acciones anteriores no se listan" in formatters.format_map_context({"acciones": acciones})


@pytest.mark.parametrize("estado,esperado", [
    ({}, False),
    ({"geojson": {"type": "FeatureCollection", "features": [{}]}}, True),
    # capa vectorial del mapa SIN dataset (un dibujo, un GeoJSON subido): antes «no hay capa cargada»
    ({"map_context": {"layers": [{"id": "l1", "kind": "vector-geojson"}]}}, True),
    ({"map_context": {"layers": [{"id": "l1", "kind": "raster-xyz"}]}}, False),  # un raster no se simboliza
    ({"map_context": {"layers": [{"id": "l1", "kind": "raster-xyz", "dataset_id": "ds_1"}]}}, True),
    ({"map_layers": {"l1": {"data": {"features": [{}]}}}}, True),
])
def test_una_sola_regla_de_capa_operable(estado, esperado):
    from geo_copilot.orchestrator.layer_resolution import hay_capa_vectorial

    assert hay_capa_vectorial(estado) is esperado


def test_nodo_y_arista_usan_la_misma_regla(monkeypatch):
    """Estaban duplicadas: una capa vectorial sin dataset la ignoraban ambas."""
    import inspect

    from geo_copilot.orchestrator import graph
    from geo_copilot.orchestrator.nodes import router

    assert "hay_capa_vectorial(state)" in inspect.getsource(router._decision)  # la decisión del nodo
    assert "hay_capa_vectorial(state)" in inspect.getsource(graph.GeoAgentGraph._route_from_router)


@pytest.mark.parametrize("url", [
    "https://geo.ejemplo.org/geoserver/ows?service=WFS&request=GetFeature&typeName=predios",
    "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a",
    "https://datos.ejemplo.org/predios.fgb",
])
def test_una_url_que_el_usuario_escribio_tiene_procedencia_aunque_no_sea_arcgis(url):
    from geo_copilot.orchestrator.nodes.data_agent import _external_url_con_procedencia

    assert _external_url_con_procedencia({"external_url": url}, f"carga esto: {url}") == url
    # la que el LLM se inventa sigue sin procedencia
    assert _external_url_con_procedencia({"external_url": url}, "carga los predios") is None


@pytest.mark.asyncio
async def test_un_plan_mas_largo_que_el_maximo_se_re_pregunta_no_se_trunca():
    from geo_copilot.core.scripted_llm import tool_call_response
    from geo_copilot.orchestrator.planner import PlannerAgent

    def paso(i):
        return {"step_id": f"s{i}", "action_type": "query_database", "description": f"p{i}",
                "query_fragment": f"paso {i}"}

    largo = {"reasoning": "r", "steps": [paso(i) for i in range(7)]}
    corto = {"reasoning": "r", "steps": [paso(i) for i in range(3)]}
    llm = MagicMock()
    llm.chat = AsyncMock(side_effect=[tool_call_response("create_plan", largo),
                                      tool_call_response("create_plan", corto)])
    agente = PlannerAgent(llm_client=llm)
    agente.settings = SimpleNamespace(max_plan_steps=5)
    plan = await agente.generate_plan("q", {})
    assert len(plan.steps) == 3
    repregunta = llm.chat.call_args_list[1].args[0][-1].content
    assert "7 pasos y el máximo es 5" in repregunta


@pytest.mark.asyncio
async def test_el_router_ve_cuantas_tablas_no_se_listan():
    """Antes `LIMIT 20` sin aviso: la entidad 21 «no existía» y el router se iba a buscar fuera."""
    from geo_copilot.agents.gis_agent import agent as gis

    filas = [{"f_table_schema": "s", "f_table_name": f"t{i}", "type": "POLYGON", "srid": 4326, "total": 250}
             for i in range(200)]
    conn = SimpleNamespace(fetch=AsyncMock(return_value=filas))

    class _Pool:
        def acquire(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return conn

                async def __aexit__(self_inner, *a):
                    return False
            return _Ctx()

    agente = gis.GISAgent.__new__(gis.GISAgent)
    agente.db_pool = _Pool()
    texto = await agente.get_db_schema_summary()
    assert "s.t199" in texto and "+50 tablas geoespaciales más" in texto
    assert conn.fetch.call_args.args[1] == gis._TABLAS_EN_RESUMEN
    # en la BD real 380 de 382 tablas eran workspaces de sesión (de OTROS usuarios): fuera
    assert "NOT LIKE 'ws\\_%'" in conn.fetch.call_args.args[0]


async def test_el_websocket_espera_la_aprobacion_humana_como_rest(monkeypatch):
    """El WS (camino de la UI) cortaba a 120 s una aprobación HITL que tiene 300 s. Ahora espera la tarea
    con el MISMO plazo que REST (cómputo + espera humana). F4: comportamiento, no el texto del fuente."""
    from unittest.mock import AsyncMock

    from geo_copilot.api import websocket, ws_turno

    ajustes = SimpleNamespace(total_execution_timeout=120, hitl_enabled=True, hitl_timeout=300)
    monkeypatch.setattr(websocket, "get_settings", lambda: ajustes)
    monkeypatch.setattr(websocket, "_enviar", AsyncMock())
    monkeypatch.setattr("geo_copilot.platform.auditoria.registrar", AsyncMock())
    plazos: list[float] = []

    async def esperar(session_id, tarea, limite):
        plazos.append(limite)
        tarea.cancel()
        return {"success": True}

    monkeypatch.setattr(ws_turno, "_esperar", esperar)
    grafo = SimpleNamespace(process=AsyncMock(return_value={"success": True}))
    assert await ws_turno.procesar_query("s", {"query": "hola"}, SimpleNamespace(agent_graph=grafo))
    assert plazos == [420]


# --------------------------------------------------------------------------- simbología (V5)
AREAS = [2166.24, 1846.66, 1843.44, 1806.07]  # los 4 lotes de la manzana 004503009 (PostGIS)


def _agente_simbologia():
    from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

    return SymbologyAgent.__new__(SymbologyAgent)


def test_jenks_da_las_clases_pedidas_aunque_la_mayor_sea_de_un_solo_valor():
    """V5: «colorea por área en 3 clases» con 4 lotes salía en 2 (el borde de la clase {2166}
    coincidía con el máximo y se deduplicaba); el agente gastó el turno re-simbolizando."""
    from geo_copilot.agents.symbology_agent.styles import ClassificationMethod, ColorScheme

    ag = _agente_simbologia()
    fc = {"features": [{"properties": {"area_m2": a}} for a in AREAS]}
    breaks = ag._calculate_numeric_breaks(fc, "area_m2", ClassificationMethod.NATURAL_BREAKS,
                                          ColorScheme.VIRIDIS, num_classes=3)
    assert [b.count for b in breaks] == [1, 2, 1]
    assert breaks[0].min_value == 1806.07 and breaks[-1].max_value == 2166.24


def test_los_rangos_manuales_cuentan_sus_elementos():
    """Antes el conteo quedaba en 0 en todas las clases: el agente leía «(0)» y seguía."""
    ag = _agente_simbologia()
    spec = [{"min": 1926, "max": 2166.24, "color": "#21918c", "label": "c"},
            {"min": 1806, "max": 1834, "color": "#440154", "label": "a"},
            {"min": 1834, "max": 1926, "color": "#3b528b", "label": "b"}]
    assert [b.count for b in ag._build_manual_breaks(spec, AREAS)] == [1, 2, 1]


def test_un_rango_manual_degenerado_no_deja_elementos_sin_color():
    """V5 F7 (despliegue limpio): k-means de 4 lotes, clases «Cluster 0» = [0,0] y «Cluster 1» = [1,1].
    Con la regla [lo, hi) la primera no atrapaba nada: el lote del cluster 0 quedaba sin color."""
    ag = _agente_simbologia()
    spec = [{"min": 0, "max": 0, "color": "#66c2a5", "label": "Cluster 0"},
            {"min": 1, "max": 1, "color": "#fc8d62", "label": "Cluster 1"}]
    breaks = ag._build_manual_breaks(spec, [0, 1, 1, 1])
    assert [b.count for b in breaks] == [1, 3]
    # F7 (auditoría): min == max es igualdad, sin ensanchar la clase sobre el hueco: [0,0] y [1,1]
    assert [(b.min_value, b.max_value) for b in breaks] == [(0, 0), (1, 1)]
