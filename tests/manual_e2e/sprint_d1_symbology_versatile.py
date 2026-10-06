"""Sprint D.1 — Symbology versátil end-to-end.

Valida que el LLM elige el `symbology_type` apropiado según la query +
datos, que los breaks se calculan correctamente para cada método de
clasificación, y que el config resultante tiene la shape consumible por
el frontend (LayerSymbology TS).

Casos:
1. Polígonos con `uso_suelo` categórico + query genérica → unique_values + Set2
2. Polígonos con `poblacion` numérica + query "muestra población" → graduated_colors + natural_breaks
3. Puntos con `afluencia_diaria` + query "puntos por afluencia" → graduated_symbols
4. Many points sin atributo claro + query "densidad" → heatmap
5. Query "todo en rojo" → single_symbol (override de color)
"""

import asyncio
import sys

sys.path.insert(0, "/app/src")

from geo_copilot.agents.symbology_agent.agent import SymbologyAgent
from geo_copilot.agents.symbology_agent.styles import (
    ClassificationMethod,
    SymbologyType,
)


def _poly(props, x_off=0.0):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-74.10 + x_off, 4.70], [-74.08 + x_off, 4.70],
                [-74.08 + x_off, 4.72], [-74.10 + x_off, 4.72],
                [-74.10 + x_off, 4.70],
            ]],
        },
    }


def _point(props, x_off=0.0):
    return {
        "type": "Feature",
        "properties": props,
        "geometry": {"type": "Point", "coordinates": [-74.07 + x_off, 4.71]},
    }


# Caso 1: uso_suelo categórico
CASE_1 = {
    "name": "categórico → unique_values",
    "query": "muéstrame los lotes",
    "geojson": {
        "type": "FeatureCollection",
        "features": [
            _poly({"nombre": f"Lote {i}", "uso_suelo": uso}, x_off=i*0.01)
            for i, uso in enumerate(["residencial", "comercial", "industrial",
                                       "residencial", "comercial", "residencial"])
        ],
    },
    "expect_types": [SymbologyType.UNIQUE_VALUES],
    "expect_field": "uso_suelo",
    "expect_breaks_min": 3,  # 3 categorías únicas
}

# Caso 2: poblacion numérica
CASE_2 = {
    "name": "numérico cont → graduated_colors",
    "query": "muestra los barrios por población",
    "geojson": {
        "type": "FeatureCollection",
        "features": [
            _poly({"nombre": f"Barrio {i}", "poblacion": p}, x_off=i*0.01)
            for i, p in enumerate([12000, 25000, 8000, 50000, 35000, 18000, 90000, 60000])
        ],
    },
    "expect_types": [SymbologyType.GRADUATED_COLORS],
    "expect_field": "poblacion",
    "expect_breaks_min": 3,
}

# Caso 3: graduated_symbols
CASE_3 = {
    "name": "puntos por valor → graduated_symbols (o graduated_colors)",
    "query": "muestra las estaciones con símbolo proporcional a la afluencia",
    "geojson": {
        "type": "FeatureCollection",
        "features": [
            _point({"nombre": f"E{i}", "afluencia_diaria": a}, x_off=i*0.01)
            for i, a in enumerate([1200, 800, 3500, 2100, 1500, 950])
        ],
    },
    # Aceptamos ambos — el LLM puede elegir graduated_symbols o graduated_colors
    "expect_types": [SymbologyType.GRADUATED_SYMBOLS, SymbologyType.GRADUATED_COLORS],
    "expect_field": "afluencia_diaria",
    "expect_breaks_min": 3,
}

# Caso 4: heatmap
CASE_4 = {
    "name": "muchos puntos sin atributo claro → heatmap",
    "query": "mapa de calor de incidentes",
    "geojson": {
        "type": "FeatureCollection",
        "features": [
            _point({"oid": i, "timestamp": "2024-01-01"}, x_off=i*0.001)
            for i in range(30)
        ],
    },
    "expect_types": [SymbologyType.HEATMAP, SymbologyType.CLUSTER, SymbologyType.SINGLE_SYMBOL],
    "expect_field": None,  # no requiere campo
    "expect_breaks_min": 0,
}

# Caso 5: single_symbol con override de color
CASE_5 = {
    "name": "todo en rojo → single_symbol + color usuario",
    "query": "muéstrame los lotes en color rojo",
    "geojson": {
        "type": "FeatureCollection",
        "features": [
            _poly({"id": i}, x_off=i*0.01)
            for i in range(3)
        ],
    },
    "expect_types": [SymbologyType.SINGLE_SYMBOL],
    "expect_field": None,
    "expect_breaks_min": 0,
    "expect_color_rojizo": True,  # fill.color en rango rojo
}


CASES = [CASE_1, CASE_2, CASE_3, CASE_4, CASE_5]


def _is_reddish(hex_color: str) -> bool:
    """Heurística: hex con R > G y R > B (es rojizo)."""
    try:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        return r > g + 30 and r > b + 30
    except Exception:
        return False


def _eval(case, response_data):
    """Devuelve (ok, reason). Valida el SymbologyConfig dict."""
    if not response_data:
        return False, "response.data vacío"

    got_type_str = response_data.get("symbology_type")
    expected_types = case["expect_types"]
    expected_type_values = {t.value for t in expected_types}
    if got_type_str not in expected_type_values:
        return False, (
            f"symbology_type={got_type_str!r} no en esperados "
            f"{sorted(expected_type_values)}"
        )

    if case["expect_field"]:
        got_field = response_data.get("classification_field")
        if got_field != case["expect_field"]:
            return False, f"classification_field={got_field!r} != {case['expect_field']!r}"

    if case["expect_breaks_min"] > 0:
        breaks = response_data.get("class_breaks", [])
        if len(breaks) < case["expect_breaks_min"]:
            return False, f"class_breaks count={len(breaks)} < min {case['expect_breaks_min']}"

    if case.get("expect_color_rojizo"):
        fill = (response_data.get("fill") or {}).get("color", "")
        if not _is_reddish(fill):
            return False, f"fill.color={fill!r} no es rojizo"

    return True, (
        f"type={got_type_str}, "
        f"field={response_data.get('classification_field')}, "
        f"breaks={len(response_data.get('class_breaks', []))}, "
        f"scheme={response_data.get('color_scheme')}"
    )


async def main():
    print("\n=== Sprint D.1 — Symbology versátil end-to-end ===\n")

    agent = SymbologyAgent()
    results = []
    for case in CASES:
        try:
            resp = await agent.process(
                query=case["query"],
                context={"geojson": case["geojson"]},
            )
            if resp.success and resp.data:
                ok, reason = _eval(case, resp.data)
                results.append((case["name"], ok, reason, resp.data.get("reasoning", "")[:80]))
            else:
                results.append((case["name"], False, f"ERR: {resp.message[:80]}", ""))
        except Exception as e:
            results.append((case["name"], False, f"EXC: {type(e).__name__}: {str(e)[:80]}", ""))

    n_pass = sum(1 for _, ok, *_ in results if ok)
    for name, ok, reason, llm_reason in results:
        flag = "✓" if ok else "✗"
        print(f"  {flag} {name[:50]:<50}")
        print(f"      → {reason}")
        if llm_reason:
            print(f"      LLM: {llm_reason}")
    pct = 100 * n_pass / len(results)
    print(f"\nResultado: {n_pass}/{len(results)} pasan ({pct:.0f}%)")
    return 0 if pct >= 80 else 1


sys.exit(asyncio.run(main()))
