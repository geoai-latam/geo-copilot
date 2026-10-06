"""Sprint B — Router agent E2E tests.

Golden set de 18 queries vs intent esperado. El RouterAgent debe devolver
el intent correcto sin pasar por el `_map_action_to_intent` que eliminamos.

Cubre los 7 intents:
  direct_response, query_data, follow_up, search_external,
  select_service, load_external, spatial_operation
"""

import asyncio
import sys

sys.path.insert(0, "/app/src")
from geo_copilot.agents.router_agent.agent import RouterAgent

# (query, intent_esperado, contexto_extra)
GOLDEN = [
    # direct_response
    ("hola", "direct_response", {}),
    ("qué puedes hacer?", "direct_response", {}),
    ("gracias", "direct_response", {}),
    # query_data (BD interna)
    ("muéstrame los lotes del barrio chapinero", "query_data", {}),
    ("cuántos predios hay en bogotá", "query_data", {}),
    ("dame las construcciones mayores a 500 m2", "query_data", {}),
    # search_external
    ("busca ortofotos de medellín", "search_external", {}),
    ("encuentra datos del IGAC sobre catastro", "search_external", {}),
    ("tienes algo de hidrología en Colombia", "search_external", {}),
    # load_external
    (
        "carga la URL https://example.com/featureserver/0",
        "load_external",
        {},
    ),
    # select_service (requiere found_services en contexto)
    (
        "1",
        "select_service",
        {"found_services": [{"id": 1, "name": "A", "url": "http://a.com"}]},
    ),
    (
        "carga el 2",
        "select_service",
        {
            "found_services": [
                {"id": 1, "name": "A", "url": "http://a.com"},
                {"id": 2, "name": "B", "url": "http://b.com"},
            ]
        },
    ),
    # select_service fuera de rango → debe degradar a direct_response con
    # error específico (no "no hay servicios" engañoso)
    (
        "99",
        "direct_response",
        {"found_services": [{"id": 1, "name": "A", "url": "http://a.com"}]},
    ),
    # spatial_operation (requiere has_external_data=True)
    (
        "buffer de 500m sobre los datos cargados",
        "spatial_operation",
        {
            "has_external_data": True,
            "external_source_name": "FeatureServer X",
            "active_data_source": "external",
        },
    ),
    (
        "calcúlame el centroide",
        "spatial_operation",
        {
            "has_external_data": True,
            "external_source_name": "FeatureServer X",
            "active_data_source": "external",
        },
    ),
    # follow_up (resultados previos en memoria)
    (
        "cuántos resultados fueron?",
        "follow_up",
        {
            "previous_sql": "SELECT * FROM lotes",
            "previous_results": [{"id": 1}, {"id": 2}, {"id": 3}],
        },
    ),
    (
        "qué SQL ejecutaste?",
        "follow_up",
        {
            "previous_sql": "SELECT count(*) FROM construcciones WHERE barrio='Chapinero'",
            "previous_results": [{"count": 42}],
        },
    ),
    # ambigua: query con tema + lugar pero sin "busca" — debe ser query_data
    # si el schema sugiere BD interna, o search_external si no
    (
        "muéstrame los hospitales",
        "query_data",
        {},
    ),
]


async def main():
    router = RouterAgent()
    results = []
    for query, expected, ctx in GOLDEN:
        try:
            resp = await router.process(query=query, context=ctx)
            if resp.success and resp.data:
                got = resp.data.get("intent")
                ok = got == expected
                results.append((query, expected, got, ok, resp.data.get("reasoning", "")[:80]))
            else:
                results.append((query, expected, f"ERR: {resp.message[:60]}", False, ""))
        except Exception as e:
            results.append((query, expected, f"EXC: {type(e).__name__}: {str(e)[:60]}", False, ""))

    print("\n=== Sprint B — Router golden set ===\n")
    print(f"{'query':<55} {'exp':<18} {'got':<22} {'OK':<4} reason")
    print("-" * 120)
    n_pass = 0
    for q, exp, got, ok, why in results:
        flag = "✓" if ok else "✗"
        if ok:
            n_pass += 1
        print(f"{q[:54]:<55} {exp:<18} {str(got)[:21]:<22} {flag:<4} {why[:40]}")
    pct = 100 * n_pass / len(results)
    print(f"\nResultado: {n_pass}/{len(results)} pasan ({pct:.0f}%)")
    return 0 if pct >= 85 else 1


sys.exit(asyncio.run(main()))
