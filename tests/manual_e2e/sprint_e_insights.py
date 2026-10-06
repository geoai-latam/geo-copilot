"""Sprint E — InsightsAgent + Chart format fix.

Valida:
1. Chart format: cada chart emitido es `{chart_type, x_key, y_key, data:[{record}]}`
   (consumible por `Chart.tsx` del frontend, sin warning amarillo).
2. LLM decide map_type apropiado (point/choropleth/heatmap/cluster) según
   datos + query, no por if/elif fijo por analysis_type.
3. LLM elige x_key/y_key correctos por chart, no por keyword del PRIMER campo.
4. Helpers `_find_*_field` y `_has_*` están eliminados (legacy cleanup).
"""

import asyncio
import sys

sys.path.insert(0, "/app/src")

from geo_copilot.agents.insights_agent.agent import InsightsAgent, OutputFormat

# Datos sintéticos compartidos
SAMPLE_AGG_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"barrio": f"Barrio {i}", "predios": p, "area_ha": a},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-74.10 + i*0.02, 4.70], [-74.08 + i*0.02, 4.70],
                    [-74.08 + i*0.02, 4.72], [-74.10 + i*0.02, 4.72],
                    [-74.10 + i*0.02, 4.70],
                ]],
            },
        }
        for i, (p, a) in enumerate([(150, 12.5), (320, 28.1), (90, 8.3), (210, 18.7), (450, 35.2)])
    ],
}

SAMPLE_PROXIMITY_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"nombre": f"Punto {i}", "distance_m": d},
            "geometry": {"type": "Point", "coordinates": [-74.07 + i*0.001, 4.71]},
        }
        for i, d in enumerate([120.5, 350.0, 80.2, 540.7, 220.3, 950.1, 1100.0, 75.0])
    ],
}

SAMPLE_HOTSPOT_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"oid": i, "tipo": "incidente"},
            "geometry": {"type": "Point", "coordinates": [-74.07 + (i % 10)*0.001, 4.71 + (i // 10)*0.001]},
        }
        for i in range(40)
    ],
}


async def main():
    print("\n=== Sprint E — InsightsAgent + Chart format ===\n")

    # TEST 0: legacy methods removed
    from geo_copilot.agents.insights_agent import agent as agent_module
    legacy = []
    for sym in ("_find_numeric_field", "_find_categorical_field",
                "_find_date_field", "_find_numeric_field_from_data",
                "_has_field", "_has_data_field"):
        if hasattr(agent_module.InsightsAgent, sym):
            legacy.append(sym)
    if legacy:
        print(f"  ✗ Legacy methods SIGUEN PRESENTES: {legacy}")
    else:
        print("  ✓ Legacy cleanup: _find_*_field y _has_*_field eliminados")

    agent = InsightsAgent()
    results = []

    # CASE 1: aggregation con polígonos → mapa choropleth + bar chart
    res1 = await agent.generate_insights(
        analysis_result={
            "geojson": SAMPLE_AGG_GEOJSON,
            "stats": {"feature_count": 5},
        },
        analysis_type="aggregation",
        title="Predios por barrio",
        query="muéstrame los barrios con más predios",
        include_recommendations=False,
        output_format=OutputFormat.DASHBOARD,
    )
    components = res1.get("components", {})
    design = res1.get("design", {})
    map_cfg = components.get("map", {})
    charts = components.get("charts", [])
    map_type_ok = map_cfg.get("type") in ("choropleth_map", "point_map")
    has_charts = len(charts) > 0
    # Validar shape de charts: deben tener x_key, y_key, data:[{...}]
    chart_shape_ok = all(
        isinstance(c.get("data"), list)
        and c.get("x_key") is not None
        and c.get("y_key") is not None
        for c in charts
    )
    print("\n  CASE 1 — aggregation polígonos:")
    print(f"    LLM design: map={design.get('map_type')} | charts={len(design.get('charts') or [])}")
    print(f"    Render: map_type={map_cfg.get('type')} | charts={len(charts)}")
    print(f"    Chart shape válido: {chart_shape_ok}")
    results.append(("aggregation choropleth+bar", map_type_ok and has_charts and chart_shape_ok))

    # CASE 2: proximity con puntos + distance_m → histogram
    res2 = await agent.generate_insights(
        analysis_result={
            "geojson": SAMPLE_PROXIMITY_GEOJSON,
            "stats": {"feature_count": 8, "avg_distance": 432.0},
        },
        analysis_type="proximity",
        title="Distancia al hospital",
        query="qué tan lejos están los puntos del hospital",
        include_recommendations=False,
        output_format=OutputFormat.DASHBOARD,
    )
    components = res2.get("components", {})
    design = res2.get("design", {})
    charts = components.get("charts", [])
    map_cfg = components.get("map", {})
    has_histogram = any(c.get("chart_type") == "histogram" for c in charts)
    map_type_ok = map_cfg.get("type") in ("point_map", "heatmap")
    chart_shape_ok = all(
        isinstance(c.get("data"), list) and c.get("x_key") is not None
        for c in charts
    )
    print("\n  CASE 2 — proximity puntos:")
    print(f"    LLM design: map={design.get('map_type')} | charts={len(design.get('charts') or [])}")
    print(f"    Render: map_type={map_cfg.get('type')} | charts={len(charts)} | tiene_histogram={has_histogram}")
    print(f"    Chart shape válido: {chart_shape_ok}")
    results.append(("proximity histogram", has_histogram and map_type_ok and chart_shape_ok))

    # CASE 3: hotspot con muchos puntos → heatmap o cluster
    res3 = await agent.generate_insights(
        analysis_result={
            "geojson": SAMPLE_HOTSPOT_GEOJSON,
            "stats": {"feature_count": 40},
        },
        analysis_type="hotspot",
        title="Densidad de incidentes",
        query="dame el mapa de densidad de incidentes",
        include_recommendations=False,
        output_format=OutputFormat.MAP_ONLY,
    )
    components = res3.get("components", {})
    design = res3.get("design", {})
    map_cfg = components.get("map", {})
    map_type_ok = map_cfg.get("type") in ("heatmap", "cluster", "point_map")
    print("\n  CASE 3 — hotspot 40 puntos:")
    print(f"    LLM design: map={design.get('map_type')}")
    print(f"    Render: map_type={map_cfg.get('type')}")
    results.append(("hotspot heatmap", map_type_ok))

    # CASE 4: chart format puro — un bar chart simple. Verificar x_key/y_key/data presentes.
    res4 = await agent.generate_insights(
        analysis_result={
            "data": [
                {"categoria": "A", "valor": 100},
                {"categoria": "B", "valor": 250},
                {"categoria": "C", "valor": 75},
            ],
            "stats": {},
        },
        analysis_type="aggregation",
        title="Datos planos",
        query="ranking de categorías",
        include_recommendations=False,
        output_format=OutputFormat.CHARTS_ONLY,
    )
    components = res4.get("components", {})
    charts = components.get("charts", [])
    bar_chart = next((c for c in charts if c.get("chart_type") == "bar"), None)
    bar_shape_ok = bar_chart is not None and (
        bar_chart.get("x_key") == "categoria"
        and bar_chart.get("y_key") == "valor"
        and isinstance(bar_chart.get("data"), list)
        and len(bar_chart["data"]) == 3
        and isinstance(bar_chart["data"][0], dict)
    )
    print("\n  CASE 4 — chart format compat con Chart.tsx:")
    print(f"    bar found: {bar_chart is not None}, x_key={bar_chart.get('x_key') if bar_chart else None}, "
          f"y_key={bar_chart.get('y_key') if bar_chart else None}, "
          f"data is list of dicts: {bar_shape_ok}")
    results.append(("chart format frontend-compat", bar_shape_ok))

    n_pass = sum(1 for _, ok in results if ok)
    print(f"\n{'─'*60}")
    for name, ok in results:
        flag = "✓" if ok else "✗"
        print(f"  {flag} {name}")
    pct = 100 * n_pass / len(results)
    print(f"\nResultado: {n_pass}/{len(results)} pasan ({pct:.0f}%)")
    return 0 if (pct >= 75 and not legacy) else 1


sys.exit(asyncio.run(main()))
