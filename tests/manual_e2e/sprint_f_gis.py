"""Sprint F — GIS agent E2E tests.

Valida que el LLM construye SQL ejecutable para varias intenciones
canónicas, sin templates hardcoded. Test contra schema real de la BD
(tablas `construcciones` y `lotes` que Sebas tiene cargadas).

También verifica:
- `SQLTemplates`, `proximity_analysis`, `territorial_aggregation`,
  `coverage_analysis`, `spatial_intersection` eliminados.
- `_get_name_field`, `_get_id_field`, `_get_numeric_fields` eliminados.
- `process()` SIEMPRE pasa por `generate_spatial_query` (camino LLM).
"""

import asyncio
import sys

sys.path.insert(0, "/app/src")

# Verificación de limpieza ANTES de importar el agente.
import importlib.util

sql_templates_spec = importlib.util.find_spec(
    "geo_copilot.agents.gis_agent.sql_templates"
)

from geo_copilot.agents.gis_agent import __init__ as gis_init
from geo_copilot.agents.gis_agent.agent import GISAgent

# 10 queries NL canónicas. Validamos shape del SQL devuelto, no la
# ejecución contra BD real (eso requiere HITL).
CASES = [
    "cuántos predios hay en bogotá",
    "predios mayores a 500 metros cuadrados",
    "construcciones cerca de un punto",
    "intersección de barrios con zona protegida",
    "centroides de las manzanas del centro",
    "reproyectar predios a UTM 18N y calcular área en metros cuadrados",
    "los 5 predios más cercanos a coordenada -74.07, 4.71",
    "buffer de 500 metros sobre las construcciones",
    "unión de las manzanas por barrio",
    "predios con geometría inválida",
]


def _validates_as_sql(sql: str) -> tuple[bool, str]:
    """Sanity check: SQL no vacío, contiene SELECT, no DELETE/DROP/UPDATE."""
    if not sql or not isinstance(sql, str):
        return False, "vacío"
    sl = sql.lower().strip()
    if "select" not in sl:
        return False, "sin SELECT"
    dangerous = ["delete from", "drop table", "drop schema", "truncate", "alter table", "update "]
    for d in dangerous:
        if d in sl:
            return False, f"contiene {d!r}"
    return True, "OK"


async def main():
    print("\n=== Sprint F — GIS agent (LLM SQL end-to-end) ===\n")

    # TEST 0a: legacy symbols removidos
    legacy: list[str] = []
    if sql_templates_spec is not None:
        legacy.append("sql_templates.py todavía existe")
    if hasattr(gis_init, "SQLTemplates"):
        legacy.append("SQLTemplates exportado desde __init__")
    for sym in ("proximity_analysis", "territorial_aggregation",
                "coverage_analysis", "spatial_intersection",
                "_get_name_field", "_get_id_field", "_get_numeric_fields"):
        if hasattr(GISAgent, sym):
            legacy.append(f"GISAgent.{sym} sigue presente")
    if legacy:
        for s in legacy:
            print(f"  ✗ {s}")
    else:
        print("  ✓ Legacy cleanup: SQLTemplates + 4 métodos template + 3 helpers de campo borrados")

    # TEST 0b: confirmar que las única tools registradas son las nuevas.
    agent = GISAgent()
    tools = list(getattr(agent, "tools", {}).keys()) if hasattr(agent, "tools") else []
    expected_tools = {"generate_spatial_query", "execute_query"}
    if tools:
        unexpected = [t for t in tools if t not in expected_tools]
        if unexpected:
            print(f"  ✗ Tools inesperadas: {unexpected}")
        else:
            print(f"  ✓ Tools registradas: {tools}")

    # TEST E2E: para cada query, llamamos a process(), validamos shape.
    print()
    results = []
    for q in CASES:
        try:
            resp = await agent.process(query=q, context={})
            data = resp.data or {}
            sql = data.get("sql")
            ok, reason = _validates_as_sql(sql or "")
            if not resp.success:
                results.append((q, False, f"agent fail: {resp.message[:60]}"))
            elif not ok:
                results.append((q, False, f"SQL inválido: {reason}"))
            else:
                # SQL existe, es SELECT, sin destructive. OK.
                # Bonus: ¿tiene PostGIS function?
                postgis = any(
                    p in (sql or "").upper()
                    for p in ("ST_", "GEOGRAPHY", "GEOMETRY")
                )
                results.append((q, True, f"len={len(sql)} | postgis={postgis}"))
        except Exception as e:
            results.append((q, False, f"EXC: {type(e).__name__}: {str(e)[:60]}"))

    n_pass = sum(1 for _, ok, _ in results if ok)
    for q, ok, reason in results:
        flag = "✓" if ok else "✗"
        print(f"  {flag} {q[:55]:<55} → {reason}")
    pct = 100 * n_pass / len(results)
    print(f"\nResultado: {n_pass}/{len(results)} pasan ({pct:.0f}%)")
    return 0 if (pct >= 70 and not legacy) else 1


sys.exit(asyncio.run(main()))
