import json

"""Validación CRÍTICA con LLM REAL (@pytest.mark.llm — excluidos por default).

Estos tests llaman a un LLM de verdad para verificar que los PROMPTS de los
JUICIOS agénticos provocan la decisión correcta en un modelo real — lo que un
mock NO puede validar. Solo los más críticos (costo): el juez de resultados
vacíos (F2.3, falso positivo = fabricar datos), el juez composicional (F5), el
juicio de densidad de viz (Fix #2) y el corrector (NO_CORRECTION).

Correr: ``pytest -m llm``. Las aserciones son sobre la DIRECCIÓN del juicio (no
texto exacto), eligiendo casos claros donde el sesgo conservador del prompt hace
estable el resultado pese al no-determinismo.
"""

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm


# ===========================================================================
# F2.3 — ResultJudge: 0-por-bug vs 0-por-realidad (el más crítico)
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_judge_catches_case_mismatch_bug():
    from geo_copilot.orchestrator.result_judge import judge_empty_result
    llm = await get_real_llm_or_skip()
    v = await judge_empty_result(
        query="cuántas escuelas hay",
        sql="SELECT * FROM poi WHERE tipo = 'ESCUELA'",
        schema_info="poi(tipo text — valores reales: 'Escuela','Hospital','Parque'; geom geometry)",
        llm_client=llm)
    print(f"\n[JUDGE case-mismatch] verdict={v.verdict} conf={v.confidence} reason={v.reason} fix={v.suggested_fix}")
    # Igualdad en MAYÚSCULAS contra valores capitalizados → debe oler a bug.
    assert v.verdict == "likely_bug"


@pytest.mark.asyncio
async def test_llm_judge_real_empty_is_not_bug():
    from geo_copilot.orchestrator.result_judge import judge_empty_result
    llm = await get_real_llm_or_skip()
    v = await judge_empty_result(
        query="cuántos volcanes activos hay en el centro de Bogotá",
        sql="SELECT COUNT(*) FROM volcanes WHERE activo = true AND ST_Within(geom, :bbox_centro)",
        schema_info="volcanes(activo boolean, geom geometry)",
        llm_client=llm)
    print(f"\n[JUDGE real-empty/aggregate] verdict={v.verdict} conf={v.confidence} reason={v.reason}")
    # Agregado + 0 plausible (no hay volcanes en Bogotá) → NO debe pedir reintento.
    assert v.is_bug is False


@pytest.mark.asyncio
async def test_llm_judge_anti_join_zero_is_real():
    from geo_copilot.orchestrator.result_judge import judge_empty_result
    llm = await get_real_llm_or_skip()
    v = await judge_empty_result(
        query="escuelas que NO tengan un hospital a 500 m",
        sql=("SELECT * FROM escuelas e WHERE NOT EXISTS ("
             "SELECT 1 FROM hospitales h WHERE ST_DWithin(e.geom::geography, h.geom::geography, 500))"),
        schema_info="escuelas(geom geometry); hospitales(geom geometry)",
        llm_client=llm)
    print(f"\n[JUDGE anti-join] verdict={v.verdict} conf={v.confidence} reason={v.reason}")
    # NOT EXISTS + ::geography (ya métrico) → 0 es respuesta completa, no bug.
    assert v.is_bug is False


# ===========================================================================
# F5 — composition_judge: ¿la respuesta cubre la consulta?
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_composition_detects_partial_answer():
    from geo_copilot.core.composition_judge import judge_answer
    llm = await get_real_llm_or_skip()
    v = await judge_answer(
        query="muéstrame las escuelas Y los hospitales del barrio centro",
        answer="Encontré 12 escuelas en el barrio centro.",
        tools_used=["query_database"], llm_client=llm)
    print(f"\n[COMP partial] addressed={v.addressed} conf={v.confidence} missing={v.missing} reason={v.reason}")
    # Pidió escuelas Y hospitales; solo trajo escuelas → falta algo concreto.
    assert v.needs_more_work is True


@pytest.mark.asyncio
async def test_llm_composition_honest_negative_is_addressed():
    from geo_copilot.core.composition_judge import judge_answer
    llm = await get_real_llm_or_skip()
    v = await judge_answer(
        query="trae los datos de tránsito vehicular en tiempo real",
        answer="No tengo acceso a datos de tránsito en tiempo real en esta plataforma; "
               "puedo trabajar con capas geoespaciales cargadas o de la base de datos.",
        tools_used=[], llm_client=llm)
    print(f"\n[COMP honest-negative] addressed={v.addressed} conf={v.confidence} reason={v.reason}")
    # Una negativa honesta SÍ atiende la consulta → no re-trabajo.
    assert v.addressed is True and v.needs_more_work is False


# ===========================================================================
# Fix #2 — densidad de viz juzgada por el LLM (con extent)
# ===========================================================================
def _insights(llm):
    from geo_copilot.agents.insights_agent.agent import InsightsAgent
    a = InsightsAgent.__new__(InsightsAgent)
    a.llm_client = llm
    return a


@pytest.mark.asyncio
async def test_llm_density_dense_points_ok_for_heatmap():
    llm = await get_real_llm_or_skip()
    res = await _insights(llm).evaluate_visualization_fit(
        feature_count=300, geometry_type="Point", viz_type="heatmap", extent_km2=0.1)
    print(f"\n[VIZ dense] {res}")
    # 300 pts en 0.1 km² (3000 pts/km²) → densísimo → heatmap apropiado.
    assert res["appropriate"] is True


@pytest.mark.asyncio
async def test_llm_density_sparse_points_not_heatmap():
    llm = await get_real_llm_or_skip()
    res = await _insights(llm).evaluate_visualization_fit(
        feature_count=25, geometry_type="Point", viz_type="heatmap", extent_km2=8000.0)
    print(f"\n[VIZ sparse] {res}")
    # 25 pts en 8000 km² (0.003 pts/km²) → dispersísimo → NO heatmap.
    assert res["appropriate"] is False


@pytest.mark.asyncio
async def test_llm_density_overrides_count_threshold():
    # DISCRIMINANTE (validación crítica): 300 puntos (> umbral viejo de 50) pero
    # DISPERSOS en 50.000 km² (0.006 pts/km²). El umbral fc<50 los APROBARÍA por
    # conteo; el LLM debe RECHAZAR por densidad real → prueba que el juicio del
    # LLM manda sobre el umbral, que es el punto del Fix #2.
    llm = await get_real_llm_or_skip()
    res = await _insights(llm).evaluate_visualization_fit(
        feature_count=300, geometry_type="Point", viz_type="heatmap", extent_km2=50000.0)
    print(f"\n[VIZ many-but-sparse] {res}")
    assert res["appropriate"] is False  # densidad manda sobre el conteo


# ===========================================================================
# Corrector: un error de columna corregible produce SQL corregido (real)
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_corrector_fixes_column_error():
    from geo_copilot.agents.gis_agent.sql_corrector import SQLCorrector
    llm = await get_real_llm_or_skip()
    corrected = await SQLCorrector(llm_client=llm).correct_sql(
        sql='SELECT nombre, area FROM lotes',
        error='column "area" does not exist',
        schema='lotes(nombre text, area_m2 numeric, geom geometry)',
        query='nombres y áreas de los lotes')
    print(f"\n[CORRECTOR] corrected={corrected!r}")
    assert corrected is not None and "SELECT" in corrected.upper()
    # El LLM debe usar la columna real (area_m2), no la inexistente.
    assert "area_m2" in corrected.lower()


# ---------------------------------------------------------------------------
# V5 F4 (T4.9): la respuesta final afirmó «coloreé los lotes por área en 5 clases»
# cuando la simbología había reportado UN SOLO COLOR. El juez solo veía los nombres
# de las herramientas; ahora ve lo que cada una reportó.
# ---------------------------------------------------------------------------
_CONSULTA_T49 = ("trae los lotes de la manzana 004503004, hazme un gráfico de barras con el área "
                 "de cada lote y colorea los lotes por área")
_HECHOS_T49 = [
    "query_database: query_data: 30 elemento(s) (resultado COMPLETO). Ya es una capa con geometría en el mapa",
    # Como lo arma `_hechos`: primeras filas + total.
    "analyze_layer: analyze: análisis listo (GRÁFICO entregado al usuario, con su tabla; 30 fila(s)). Datos: "
    + json.dumps({"filas": [{"lotcodigo": f"0045030040{i:02d}", "area_m2": a} for i, a in
                            enumerate([247.89, 244.68, 150.09, 188.21, 332.73, 89.85, 210.01, 299.46], 1)],
                  "filas_totales": 30}),
    "apply_symbology: simbología aplicada a 30 elementos (single_symbol — UN SOLO COLOR, sin clasificación: "
    "no describas colores por categoría)",
]
_TOOLS_T49 = ["query_database", "analyze_layer", "apply_symbology"]


@pytest.mark.llm
@pytest.mark.asyncio
async def test_juez_rechaza_afirmar_un_coloreado_que_no_ocurrio():
    from geo_copilot.core.composition_judge import judge_answer

    v = await judge_answer(
        query=_CONSULTA_T49, tools_used=_TOOLS_T49, observaciones=_HECHOS_T49, llm_client=await get_real_llm_or_skip(),
        answer=("He cargado los 30 lotes de la manzana 004503004 y te hice un gráfico de barras con el área de cada "
                "lote. Además, he coloreado los lotes en el mapa según su área en 5 clases."),
    )
    print(f"\n[T4.9 juez falso] {v}")
    assert v.needs_more_work, v


@pytest.mark.llm
@pytest.mark.asyncio
async def test_juez_acepta_una_respuesta_veraz_y_completa():
    """Control: si los hechos respaldan cada afirmación, no hay re-trabajo.
    (Una negativa honesta «no pude colorear por área» NO es control: el agente sí
    podía añadir el campo con spatial_operation — gpt-4o lo señala, con razón.)"""
    from geo_copilot.core.composition_judge import judge_answer

    hechos = [*_HECHOS_T49[:2],
              "spatial_operation: capa con el campo area_m2 añadido (30 elementos)",
              "apply_symbology: simbología aplicada a 30 elementos (graduated_colors por «area_m2», 5 clase(s); "
              "colores aplicados: 39.0 – 90.0=#fee5d9 (6), 90.0 – 150.0=#fcae91 (6), 150.0 – 200.0=#fb6a4a (6), "
              "200.0 – 250.0=#de2d26 (6), 250.0 – 355.3=#a50f15 (6))"]
    v = await judge_answer(
        query=_CONSULTA_T49, tools_used=[*_TOOLS_T49[:2], "spatial_operation", "apply_symbology"],
        observaciones=hechos, llm_client=await get_real_llm_or_skip(),
        answer=("Cargué los 30 lotes de la manzana 004503004, te hice un gráfico de barras con el área de cada "
                "lote y coloreé los lotes por área (area_m2) en 5 clases, de 39 a 355 m²."),
    )
    print(f"\n[T4.9 juez veraz] {v}")
    assert not v.needs_more_work, v


@pytest.mark.llm
@pytest.mark.asyncio
async def test_juez_rechaza_decir_por_area_si_se_clasifico_por_un_identificador():
    """V5 F4: la simbología clasificó «por área» usando `objectid` y la respuesta
    dijo «coropleto graduado por área»."""
    from geo_copilot.core.composition_judge import judge_answer

    hechos = [*_HECHOS_T49[:2],
              "apply_symbology: simbología aplicada a 30 elementos (graduated_colors por «objectid», 5 clase(s); "
              "colores aplicados: 254612.00 – 254630.00=#fee5d9 (6), 254630.00 – 254650.00=#fcae91 (6))"]
    v = await judge_answer(
        query=_CONSULTA_T49, tools_used=_TOOLS_T49, observaciones=hechos, llm_client=await get_real_llm_or_skip(),
        answer=("He cargado los 30 lotes, generé un gráfico de barras con el área de cada lote y coloreé los lotes "
                "con un coropleto graduado por área, de menor a mayor."),
    )
    print(f"\n[T4.9 juez objectid] {v}")
    assert v.needs_more_work, v
