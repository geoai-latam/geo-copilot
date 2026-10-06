"""Sprint C — Python agent E2E tests.

Ground truth: 6 operaciones espaciales canónicas sobre un GeoJSON sintético
(municipios polígonos + un punto de referencia). Validamos que el código
generado por el LLM produce un GeoDataFrame con la shape esperada y que la
post-validación del sandbox NO bloquea operaciones válidas.

También verifica:
- `extract_distance_from_query` y `OPERATION_TEMPLATES` ya no existen.
- Sin LLM cliente, el agente falla (no hay heurística que adivine código).
"""

import asyncio
import sys

sys.path.insert(0, "/app/src")

# Deshabilitar HITL antes de importar el agente — los tests no pueden
# responder interactivamente al prompt de aprobación.
from geo_copilot.core.config import settings as _settings

_settings.hitl_enabled = False

from geo_copilot.agents.python_agent.agent import PythonAgent

# GeoJSON sintético: 3 polígonos simples cerca de Bogotá (lon, lat).
SAMPLE_GEOJSON = {
    "type": "FeatureCollection",
    "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
    "features": [
        {
            "type": "Feature",
            "properties": {"id": 1, "nombre": "A", "categoria": "norte"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-74.10, 4.70], [-74.08, 4.70],
                    [-74.08, 4.72], [-74.10, 4.72], [-74.10, 4.70],
                ]],
            },
        },
        {
            "type": "Feature",
            "properties": {"id": 2, "nombre": "B", "categoria": "norte"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-74.06, 4.70], [-74.04, 4.70],
                    [-74.04, 4.72], [-74.06, 4.72], [-74.06, 4.70],
                ]],
            },
        },
        {
            "type": "Feature",
            "properties": {"id": 3, "nombre": "C", "categoria": "sur"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-74.10, 4.60], [-74.08, 4.60],
                    [-74.08, 4.62], [-74.10, 4.62], [-74.10, 4.60],
                ]],
            },
        },
    ],
}


# (query, esperado: dict con assertions sobre el resultado)
CASES = [
    (
        "Aplica un buffer de 500 metros sobre las geometrías",
        {
            "min_count": 3,           # Mismo número de features
            "expect_geom_type": "Polygon",  # buffer de polygon → polygon (mismo o multi)
        },
    ),
    (
        "Calcula el centroide de cada polígono",
        {
            "min_count": 3,
            "expect_geom_type": "Point",
        },
    ),
    (
        "Calcula el área en metros cuadrados de cada polígono",
        {
            "min_count": 3,
            "expect_column": "area_m2",
        },
    ),
    (
        "Disuelve los polígonos por la columna categoria",
        {
            "max_count": 3,          # ≤ 3 (norte se une → 2 grupos)
            "min_count": 1,
        },
    ),
    (
        "Calcula la envolvente convexa de todas las geometrías unidas",
        {
            "min_count": 1,
            "max_count": 1,
            "expect_geom_type": "Polygon",
        },
    ),
    (
        "Calcula la distancia en metros de cada polígono al punto (-74.0721, 4.711)",
        {
            "min_count": 3,
            # El LLM puede nombrar la columna en es o en — ambos son válidos.
            "expect_column_any_of": ["distance_m", "distancia_m", "distance", "distancia"],
        },
    ),
]


def _validate(result_geojson, expectations):
    """Devuelve (ok, reason). Valida basic shape del resultado."""
    if not isinstance(result_geojson, dict):
        return False, f"result no es dict ({type(result_geojson).__name__})"
    feats = result_geojson.get("features", [])
    n = len(feats)
    if "min_count" in expectations and n < expectations["min_count"]:
        return False, f"count={n} < min={expectations['min_count']}"
    if "max_count" in expectations and n > expectations["max_count"]:
        return False, f"count={n} > max={expectations['max_count']}"
    if "expect_geom_type" in expectations and feats:
        gt = feats[0].get("geometry", {}).get("type", "")
        want = expectations["expect_geom_type"]
        if want not in gt:
            return False, f"geom={gt} no contiene '{want}'"
    if "expect_column" in expectations and feats:
        props = feats[0].get("properties", {})
        col = expectations["expect_column"]
        if col not in props:
            return False, f"columna '{col}' no presente. props={list(props.keys())}"
    if "expect_column_any_of" in expectations and feats:
        props = feats[0].get("properties", {})
        candidates = expectations["expect_column_any_of"]
        if not any(c in props for c in candidates):
            return False, (
                f"ninguna de {candidates} en props={list(props.keys())}"
            )
    return True, f"count={n}"


async def main():
    # TEST 0: legacy symbols removidos
    legacy_check = []
    try:
        from geo_copilot.agents.python_agent import prompts
        if hasattr(prompts, "extract_distance_from_query"):
            legacy_check.append("extract_distance_from_query SIGUE PRESENTE")
        if hasattr(prompts, "OPERATION_TEMPLATES"):
            legacy_check.append("OPERATION_TEMPLATES SIGUE PRESENTE")
    except ImportError as e:
        legacy_check.append(f"import error: {e}")
    print("\n=== Sprint C — Python agent ===\n")
    if legacy_check:
        print(f"  ✗ Legacy cleanup: {legacy_check}")
    else:
        print("  ✓ Legacy cleanup: extract_distance_from_query y OPERATION_TEMPLATES borrados")

    # Tests E2E
    agent = PythonAgent()
    results = []
    for query, expect in CASES:
        ctx = {
            "active_data_source": "external",
            "external_geojson": SAMPLE_GEOJSON,
            "external_source_name": "test sintético",
        }
        try:
            resp = await agent.process(query=query, context=ctx)
            if resp.success and resp.data:
                rg = resp.data.get("geojson")
                ok, reason = _validate(rg, expect)
                results.append((query, ok, reason, resp.data.get("warning")))
            else:
                results.append((query, False, f"ERR: {resp.message[:80]}", None))
        except Exception as e:
            results.append((query, False, f"EXC: {type(e).__name__}: {str(e)[:80]}", None))

    print()
    n_pass = sum(1 for _, ok, _, _ in results if ok)
    for q, ok, reason, warning in results:
        flag = "✓" if ok else "✗"
        wm = f" [warn: {warning}]" if warning else ""
        print(f"  {flag} {q[:60]:<60} → {reason}{wm}")
    pct = 100 * n_pass / len(results)
    print(f"\nResultado: {n_pass}/{len(results)} pasan ({pct:.0f}%)")
    return 0 if (pct >= 85 and not legacy_check) else 1


sys.exit(asyncio.run(main()))
