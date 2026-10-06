"""Validación CRÍTICA con LLM REAL del DISEÑO DE VISUALIZACIÓN del InsightsAgent.

Superficie: ``InsightsAgent._llm_design_visualizations(query, analysis_type,
geojson_data, analysis_result, stats)`` → plan con ``map_type`` + ``charts``
(cada chart con ``chart_type`` / ``x_key`` / ``y_key`` / ``value_field``).

Lo que un mock NO puede validar: que el PROMPT (``_DESIGN_VIZ_SYSTEM`` +
``user_msg``) provoque en un modelo real las decisiones correctas de diseño:
- ranking numérico + "compara la población por barrio" → bar(x=barrio,
  y=población) con los ejes correctos (no invertidos, no inventados).
- puntos simples sin atributo agregable → point_map sin charts fabricados.
- DISCRIMINANTE: la query pide EXPLÍCITAMENTE tabla/gráfico → el diseño debe
  respetarlo y producir ese chart, NO defaultear a "solo mapa".

Aserciones sobre la DIRECCIÓN/estructura del juicio (no texto exacto). Se
imprime el reasoning del modelo para auditarlo (validación crítica: leer el
reasoning real, no conformarse con verde).

Correr: ``pytest -m llm -s tests/test_llm_insights_viz_design.py``.
"""

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm


def _insights(llm):
    """InsightsAgent sin __init__ (igual que en test_llm_critical.py)."""
    from geo_copilot.agents.insights_agent.agent import InsightsAgent
    a = InsightsAgent.__new__(InsightsAgent)
    a.llm_client = llm
    return a


def _bar_charts(plan: dict) -> list[dict]:
    return [c for c in (plan.get("charts") or []) if c.get("chart_type") == "bar"]


def _all_keys(ch: dict) -> set[str]:
    return {ch.get("x_key"), ch.get("y_key"), ch.get("value_field")}


# ---------------------------------------------------------------------------
# Fixtures de datos: ranking tabular por barrio (sin geojson, solo `data`).
# ---------------------------------------------------------------------------
def _ranking_analysis():
    """Agregación: población por barrio (categórico + numérico). Sin geometría."""
    data = [
        {"barrio": "Chapinero", "poblacion": 138000, "viviendas": 52000},
        {"barrio": "Suba", "poblacion": 1250000, "viviendas": 380000},
        {"barrio": "Kennedy", "poblacion": 1180000, "viviendas": 340000},
        {"barrio": "Usaquen", "poblacion": 475000, "viviendas": 160000},
        {"barrio": "Bosa", "poblacion": 720000, "viviendas": 195000},
    ]
    return {"data": data, "stats": {"feature_count": 5}}


def _simple_points_geojson():
    """20 puntos 'tienda' sin ningún atributo numérico/categórico agregable."""
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-74.0 + i * 0.01, 4.6 + i * 0.01]},
            "properties": {"id": i, "nombre": f"Tienda {i}"},
        }
        for i in range(20)
    ]
    return {"type": "FeatureCollection", "features": feats}


# ===========================================================================
# CASO CLARO 1 — ranking numérico + "compara la población por barrio"
#   → debe incluir un bar chart con x=barrio (categoría), y=poblacion (numérico).
# ===========================================================================
@pytest.mark.asyncio
async def test_compare_population_by_barrio_yields_bar_with_correct_axes():
    llm = await get_real_llm_or_skip()
    ar = _ranking_analysis()
    plan = await _insights(llm)._llm_design_visualizations(
        query="compara la población por barrio",
        analysis_type="aggregation",
        geojson_data=None,
        analysis_result=ar,
        stats=ar["stats"],
    )
    print(f"\n[BAR compare-poblacion] map={plan.get('map_type')} "
          f"charts={plan.get('charts')} reasoning={plan.get('reasoning')}")

    bars = _bar_charts(plan)
    assert bars, f"comparación por categoría debe producir un bar chart; got {plan.get('charts')}"
    ch = bars[0]
    # Ejes correctos: categoría en x, magnitud numérica en y.
    assert ch.get("x_key") == "barrio", f"x_key debería ser 'barrio', got {ch.get('x_key')!r}"
    assert ch.get("y_key") == "poblacion", f"y_key debería ser 'poblacion', got {ch.get('y_key')!r}"


# ===========================================================================
# CASO CLARO 2 — puntos simples sin atributo agregable
#   → point_map y SIN charts inventados (no x_key/y_key sobre campos no-numéricos).
# ===========================================================================
@pytest.mark.asyncio
async def test_simple_points_give_point_map_no_invented_charts():
    llm = await get_real_llm_or_skip()
    gj = _simple_points_geojson()
    ar = {"geojson": gj, "stats": {"feature_count": 20}}
    plan = await _insights(llm)._llm_design_visualizations(
        query="muéstrame las tiendas en el mapa",
        analysis_type="generic",
        geojson_data=gj,
        analysis_result=ar,
        stats=ar["stats"],
    )
    print(f"\n[POINT simple-tiendas] map={plan.get('map_type')} "
          f"charts={plan.get('charts')} reasoning={plan.get('reasoning')}")

    assert plan.get("map_type") == "point_map", \
        f"puntos simples → point_map; got {plan.get('map_type')!r}"
    # No debe inventar un chart cuantitativo: los únicos campos son id/nombre,
    # ninguno es una magnitud agregable. Un bar(x=nombre, y=id) sería inventado.
    bars = _bar_charts(plan)
    for ch in bars:
        # 'id' como eje Y es contar/numerar, no un dato significativo → inválido.
        assert ch.get("y_key") != "id", \
            f"chart inventado sobre 'id' como magnitud: {ch}"


# ===========================================================================
# CASO CLARO 3 — proximity con distancias → histograma del campo de distancia.
#   El system prompt lo marca OBLIGATORIO; valida que el LLM lo respete.
# ===========================================================================
@pytest.mark.asyncio
async def test_proximity_yields_distance_histogram():
    llm = await get_real_llm_or_skip()
    data = [
        {"nombre": f"Escuela {i}", "distancia_m": d}
        for i, d in enumerate([120, 340, 560, 780, 210, 990, 1500, 430, 650, 880])
    ]
    ar = {"data": data, "stats": {"feature_count": 10}}
    plan = await _insights(llm)._llm_design_visualizations(
        query="qué tan lejos están las escuelas del hospital más cercano",
        analysis_type="proximity",
        geojson_data=None,
        analysis_result=ar,
        stats=ar["stats"],
    )
    print(f"\n[PROXIMITY histogram] charts={plan.get('charts')} "
          f"reasoning={plan.get('reasoning')}")

    hist = [c for c in (plan.get("charts") or []) if c.get("chart_type") == "histogram"]
    assert hist, f"proximity → histograma de distancia obligatorio; got {plan.get('charts')}"
    assert hist[0].get("value_field") == "distancia_m", \
        f"histograma sobre el campo de distancia; got {hist[0].get('value_field')!r}"


# ===========================================================================
# DISCRIMINANTE — la query pide EXPLÍCITAMENTE un gráfico de barras.
#   Hay geometría (puntos) → una heurística "tiene geojson ⇒ mapa" defaultearía
#   a solo-mapa sin charts. El LLM debe RESPETAR la intención y producir el bar.
#   Este caso falla con la heurística pero el LLM debe acertar.
# ===========================================================================
@pytest.mark.asyncio
async def test_discriminant_explicit_chart_request_overrides_map_default():
    llm = await get_real_llm_or_skip()
    # Puntos CON un atributo numérico agregable (ventas) y categórico (zona).
    feats = []
    rows = [
        ("Norte", 4200), ("Norte", 3900), ("Sur", 1500),
        ("Sur", 1800), ("Centro", 6700), ("Centro", 7100),
        ("Occidente", 2300), ("Occidente", 2600),
    ]
    for i, (zona, ventas) in enumerate(rows):
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-74.0 + i * 0.02, 4.6]},
            "properties": {"id": i, "zona": zona, "ventas": ventas},
        })
    gj = {"type": "FeatureCollection", "features": feats}
    ar = {"geojson": gj, "stats": {"feature_count": len(feats)}}

    plan = await _insights(llm)._llm_design_visualizations(
        query="hazme un gráfico de barras de las ventas por zona, no quiero el mapa",
        analysis_type="aggregation",
        geojson_data=gj,
        analysis_result=ar,
        stats=ar["stats"],
    )
    print(f"\n[DISCRIMINANT explicit-bar] map={plan.get('map_type')} "
          f"charts={plan.get('charts')} reasoning={plan.get('reasoning')}")

    bars = _bar_charts(plan)
    assert bars, (
        "el usuario pidió EXPLÍCITAMENTE un gráfico de barras; el diseño no debe "
        f"defaultear a solo-mapa. charts={plan.get('charts')}"
    )
    ch = bars[0]
    # Ejes coherentes con 'ventas por zona': categoría=zona, magnitud=ventas.
    assert ch.get("x_key") == "zona", f"x_key=zona esperado, got {ch.get('x_key')!r}"
    assert ch.get("y_key") == "ventas", f"y_key=ventas esperado, got {ch.get('y_key')!r}"


# ===========================================================================
# DISCRIMINANTE 2 — query pide "calor"/"densidad" sobre puntos
#   → map_type debe ser heatmap (el prompt lo fuerza con esas palabras).
#   Una heurística por feature_count podría elegir point_map.
# ===========================================================================
@pytest.mark.asyncio
async def test_discriminant_heat_keyword_forces_heatmap():
    llm = await get_real_llm_or_skip()
    feats = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-74.0 + (i % 10) * 0.001, 4.6 + (i // 10) * 0.001]},
            "properties": {"id": i},
        }
        for i in range(40)
    ]
    gj = {"type": "FeatureCollection", "features": feats}
    ar = {"geojson": gj, "stats": {"feature_count": 40}}

    plan = await _insights(llm)._llm_design_visualizations(
        query="muéstrame un mapa de calor de la densidad de incidentes",
        analysis_type="hotspot",
        geojson_data=gj,
        analysis_result=ar,
        stats=ar["stats"],
    )
    print(f"\n[DISCRIMINANT heatmap-keyword] map={plan.get('map_type')} "
          f"reasoning={plan.get('reasoning')}")

    assert plan.get("map_type") == "heatmap", \
        f"'calor'/'densidad' → heatmap; got {plan.get('map_type')!r}"
