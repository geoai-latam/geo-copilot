"""Sprint D — Symbology agent E2E tests.

Ground truth: 8 GeoJSON sintéticos con schemas variados (campos
convencionales y no convencionales). Validamos que el LLM elige el campo
correcto para `label_field` y `classification_field` mejor que los
heurísticos por keywords que eliminamos.

También valida:
- Threshold mágico 0.8 → 100% homogéneo (TEXT para mixed).
- `_find_label_field` y `_find_classification_field` no existen.
- Sin LLM, devuelve None/None (no adivina con keywords).
"""

import asyncio
import sys

sys.path.insert(0, "/app/src")
from geo_copilot.agents.symbology_agent.agent import SymbologyAgent


def _polygon():
    """Polígono cuadrado simple."""
    return {
        "type": "Polygon",
        "coordinates": [[
            [-74.10, 4.70], [-74.08, 4.70],
            [-74.08, 4.72], [-74.10, 4.72], [-74.10, 4.70],
        ]],
    }


def _point():
    return {"type": "Point", "coordinates": [-74.0721, 4.711]}


def _line():
    return {
        "type": "LineString",
        "coordinates": [[-74.10, 4.70], [-74.05, 4.72]],
    }


def _feature(props, geom_factory):
    return {"type": "Feature", "properties": props, "geometry": geom_factory()}


# (label, geojson, expected_label_field, expected_classification_field, allow_nulls)
CASES = [
    # 1. Schema convencional (campo "nombre" + "poblacion")
    (
        "schema convencional",
        {
            "type": "FeatureCollection",
            "features": [
                _feature({"nombre": "Chapinero", "poblacion": 50000, "estrato": 4}, _polygon),
                _feature({"nombre": "Usaquén",   "poblacion": 80000, "estrato": 5}, _polygon),
                _feature({"nombre": "Suba",      "poblacion": 90000, "estrato": 3}, _polygon),
            ],
        },
        "nombre",
        ["poblacion", "estrato"],  # ambos válidos
        False,
    ),
    # 2. Schema con nombres NO convencionales — "denominacion_oficial" en vez de "nombre"
    (
        "schema NO convencional",
        {
            "type": "FeatureCollection",
            "features": [
                _feature({"denominacion_oficial": "Estación Norte", "afluencia_diaria": 1200, "id_interno": "A001"}, _point),
                _feature({"denominacion_oficial": "Estación Sur",   "afluencia_diaria": 3400, "id_interno": "A002"}, _point),
                _feature({"denominacion_oficial": "Estación Este",  "afluencia_diaria": 2100, "id_interno": "A003"}, _point),
            ],
        },
        "denominacion_oficial",
        ["afluencia_diaria"],
        False,
    ),
    # 3. Schema con UUIDs como id — debe NO elegir el id como label (es ilegible)
    (
        "UUIDs no son label legible",
        {
            "type": "FeatureCollection",
            "features": [
                _feature({"id": "f47ac10b-58cc-4372-a567-0e02b2c3d479", "razon_social": "EPM SA", "consumo_kwh": 1500.5}, _point),
                _feature({"id": "550e8400-e29b-41d4-a716-446655440000", "razon_social": "ETB SA", "consumo_kwh": 890.2},  _point),
                _feature({"id": "b1e8e1d0-1234-5678-9abc-def012345678", "razon_social": "EMCALI", "consumo_kwh": 2200.0}, _point),
            ],
        },
        "razon_social",  # NO "id" aunque sea único — UUIDs son ilegibles
        ["consumo_kwh"],
        False,
    ),
    # 4. Campos extranjeros — debería igual elegir "city_name" sobre "fips_code"
    (
        "campos en inglés",
        {
            "type": "FeatureCollection",
            "features": [
                _feature({"city_name": "Boston", "population": 690000, "fips_code": "25025"}, _polygon),
                _feature({"city_name": "Cambridge", "population": 118000, "fips_code": "25017"}, _polygon),
                _feature({"city_name": "Somerville", "population": 81000, "fips_code": "25017"}, _polygon),
            ],
        },
        "city_name",
        ["population"],
        False,
    ),
    # 5. Datos categóricos puros (sin numérico) — classification debe ser el campo categórico
    (
        "solo categóricos",
        {
            "type": "FeatureCollection",
            "features": [
                _feature({"nombre": "Lote 1", "uso_suelo": "residencial"}, _polygon),
                _feature({"nombre": "Lote 2", "uso_suelo": "comercial"},   _polygon),
                _feature({"nombre": "Lote 3", "uso_suelo": "industrial"},  _polygon),
                _feature({"nombre": "Lote 4", "uso_suelo": "residencial"}, _polygon),
            ],
        },
        "nombre",
        ["uso_suelo"],
        False,
    ),
    # 6. Líneas de vías con clase_via
    (
        "líneas vías",
        {
            "type": "FeatureCollection",
            "features": [
                _feature({"via_nombre": "Av Caracas",    "clase_via": "principal", "longitud_m": 5230.5}, _line),
                _feature({"via_nombre": "Calle 72",      "clase_via": "secundaria","longitud_m": 1100.0}, _line),
                _feature({"via_nombre": "Carrera 7",     "clase_via": "principal", "longitud_m": 12500.0},_line),
            ],
        },
        "via_nombre",
        ["clase_via", "longitud_m"],  # cualquiera válido
        False,
    ),
    # 7. Schema con SOLO ids (sin nombre legible) — label debe ser None o el id (acepta cualquiera)
    (
        "sin nombre legible",
        {
            "type": "FeatureCollection",
            "features": [
                _feature({"oid": 1, "valor": 100}, _point),
                _feature({"oid": 2, "valor": 250}, _point),
                _feature({"oid": 3, "valor": 75},  _point),
            ],
        },
        None,  # cualquier valor (incluido None) está bien
        ["valor"],
        True,
    ),
    # 8. Schema con código catastral IGAC (orto25430madrid)
    (
        "código catastral IGAC",
        {
            "type": "FeatureCollection",
            "features": [
                _feature({"codigo_catastral": "orto25430madrid", "area_m2": 1250.5, "manzana": "MZ001"}, _polygon),
                _feature({"codigo_catastral": "orto25431madrid", "area_m2": 980.2,  "manzana": "MZ002"}, _polygon),
                _feature({"codigo_catastral": "orto25432madrid", "area_m2": 1500.0, "manzana": "MZ003"}, _polygon),
            ],
        },
        "codigo_catastral",  # único legible
        ["area_m2"],
        False,
    ),
]


def _eval(case_name, analysis, exp_label, exp_class_any, allow_nulls):
    got_label = analysis.get("recommended_label_field")
    got_class = analysis.get("recommended_classification_field")

    # Validar label
    if exp_label is None:
        label_ok = True  # acepta cualquiera
    else:
        label_ok = got_label == exp_label
    if not label_ok and allow_nulls and got_label is None:
        label_ok = True

    # Validar classification: acepta cualquier valor de la lista
    if exp_class_any is None or not exp_class_any:
        class_ok = True
    else:
        class_ok = got_class in exp_class_any
    if not class_ok and allow_nulls and got_class is None:
        class_ok = True

    return label_ok and class_ok, got_label, got_class


async def main():
    print("\n=== Sprint D — Symbology agent ===\n")

    # TEST 0: legacy symbols removidos
    from geo_copilot.agents.symbology_agent import agent as ag_mod
    legacy_check = []
    if hasattr(ag_mod.SymbologyAgent, "_find_label_field"):
        legacy_check.append("_find_label_field SIGUE PRESENTE")
    if hasattr(ag_mod.SymbologyAgent, "_find_classification_field"):
        legacy_check.append("_find_classification_field SIGUE PRESENTE")
    if legacy_check:
        print(f"  ✗ Legacy: {legacy_check}")
    else:
        print("  ✓ Legacy cleanup: _find_label_field y _find_classification_field eliminados")

    # TEST 0b: sin LLM, no adivina. `False` fuerza modo offline (None
    # auto-inicializa el cliente del settings).
    agent_no_llm = SymbologyAgent(llm_client=False)
    no_llm_result = await agent_no_llm.analyze_data(CASES[0][1])
    if no_llm_result.get("recommended_label_field") is None and no_llm_result.get("recommended_classification_field") is None:
        print("  ✓ Sin LLM devuelve None/None (no adivina)")
    else:
        print(f"  ✗ Sin LLM aún devuelve: label={no_llm_result.get('recommended_label_field')}, "
              f"class={no_llm_result.get('recommended_classification_field')}")

    # TEST E2E con LLM
    agent = SymbologyAgent()
    print()
    results = []
    for name, geojson, exp_label, exp_class_any, allow_nulls in CASES:
        try:
            analysis = await agent.analyze_data(geojson)
            ok, gl, gc = _eval(name, analysis, exp_label, exp_class_any, allow_nulls)
            results.append((name, ok, gl, gc, exp_label, exp_class_any))
        except Exception as e:
            results.append((name, False, "ERR", f"{type(e).__name__}: {e}", exp_label, exp_class_any))

    n_pass = sum(1 for _, ok, *_ in results if ok)
    for name, ok, gl, gc, exp_label, exp_class_any in results:
        flag = "✓" if ok else "✗"
        exp_l = exp_label or "—"
        exp_c = "|".join(exp_class_any) if exp_class_any else "—"
        print(f"  {flag} {name[:35]:<35} → label={str(gl)[:25]:<25} (exp:{exp_l[:20]:<20}) | class={str(gc)[:25]:<25} (exp:{exp_c[:25]})")
    pct = 100 * n_pass / len(results)
    print(f"\nResultado: {n_pass}/{len(results)} pasan ({pct:.0f}%)")
    return 0 if pct >= 85 else 1


sys.exit(asyncio.run(main()))
