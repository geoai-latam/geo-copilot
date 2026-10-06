"""Banco de DIAGNÓSTICO: una consulta en lenguaje natural por el agente COMPLETO (grafo + LLM real +
MCP reales), dentro del contenedor de la app con su propia configuración. Imprime lo que vería el
usuario y por dónde pasó: intent, traza de decisiones, capa, SQL, imagen y respuesta.

Para MEDIR (tasas, verdades) está tests/agentic_bench/runner.py --app; esto es para MIRAR un caso.

    docker exec -i geo_copilot_app python - "consulta 1" "consulta 2" < scripts/banco_agente.py

Las consultas de una misma ejecución comparten sesión (conversación de varios turnos). El banco
aprueba solo lo que en la interfaz aprobaría el usuario (HITL apagado) e imita la ruta HTTP: la
capa activa del mapa llega como datos externos (A4a).
    BANCO_CAPAS='[{"id": "...", "name": "...", "is_active": true, "data": {GeoJSON}}]'  capas del mapa
    BANCO_TRAZA=3000                                                                   largo de la traza
"""
import asyncio
import json
import os
import sys
import time
import uuid


def _resumen_geo(g):
    if not isinstance(g, dict):
        return None
    feats = g.get("features") or []
    tipos = sorted({(f.get("geometry") or {}).get("type") for f in feats if isinstance(f, dict)} - {None})
    campos = sorted((feats[0].get("properties") or {}).keys())[:8] if feats else []
    return f"{len(feats)} elementos {tipos} campos={campos}"


async def main():
    from geo_copilot.core.config import get_settings

    get_settings().hitl_enabled = False  # el banco aprueba solo (lo que en la UI aprobaría el usuario)
    from geo_copilot.api.dependencies import get_app_state

    st = get_app_state()
    await st.initialize()
    gen = st.agent_graph.gis_agent.generate_sql_from_query

    async def espia(*a, **k):
        sql = await gen(*a, **k)
        print("SQL_GENERADO:", " ".join(str(sql).split())[:900], flush=True)
        return sql

    st.agent_graph.gis_agent.generate_sql_from_query = espia
    sid = f"banco-{uuid.uuid4().hex[:8]}"
    ctx = st.conversation_manager.get_or_create_session(sid) if hasattr(st.conversation_manager, "get_or_create_session") \
        else st.conversation_manager.get_or_create(sid)
    historial = []
    capas = json.loads(os.environ["BANCO_CAPAS"]) if os.environ.get("BANCO_CAPAS") else []
    mc = {"layers": capas}
    if capas:
        mc["viewport"] = {"bbox": capas[0].get("bbox") or [-74.2, 4.8, -73.9, 4.95], "zoom": 13}
    ml = {c["id"]: {"data": c.get("data"), "name": c.get("name", "")} for c in capas if c.get("data")} or None
    for q in sys.argv[1:]:
        t = time.time()
        try:
            activa = next((c for c in capas if c.get("is_active") and c.get("data")), None)  # A4a, como la ruta
            extra = ({"external_geojson": activa["data"], "external_source_name": activa.get("name"),
                      "has_external_data": True, "active_data_source": "external",
                      "active_source_name": activa.get("name")} if activa else {})
            r = await st.agent_graph.process(query=q, session_id=sid, conversation_history=historial,
                                             map_context=mc, map_layers=ml, **extra)
        except Exception as exc:  # noqa: BLE001
            print(f"\n### {q}\nERROR {type(exc).__name__}: {exc}")
            continue
        pasos = [m.get("agent") for m in r.get("messages") or [] if isinstance(m, dict)]
        print(f"\n### {q}   ({time.time() - t:.1f} s)")
        print("intent:", r.get("intent"), "| pasos:", pasos[:14], "| claves:", sorted(k for k, v in r.items() if v)[:40])
        for k in ("decision_trace", "reasoning_trace", "agent_messages", "map_commands"):
            if r.get(k):
                print(f"{k}:", json.dumps(r[k], ensure_ascii=False, default=str)[:int(os.environ.get("BANCO_TRAZA", "900"))])
        print("geojson:", _resumen_geo(r.get("geojson")), "| externo:", _resumen_geo(r.get("external_geojson")))
        if r.get("external_imagery") or r.get("imagery"):
            print("imagen:", json.dumps(r.get("external_imagery") or r.get("imagery"), ensure_ascii=False, default=str)[:400])
        if r.get("sql"):
            print("SQL:", " ".join(str(r["sql"]).split())[:700])
        if r.get("data"):
            print("data:", json.dumps(r["data"], ensure_ascii=False, default=str)[:500])
        if r.get("error"):
            print("ERROR del turno:", str(r["error"])[:400])
        print("RESPUESTA:", (r.get("final_response") or r.get("message") or "")[:900])
        historial += [{"role": "user", "content": q},
                      {"role": "assistant", "content": r.get("final_response") or r.get("message") or ""}]
        await asyncio.sleep(2)
    await st.shutdown() if hasattr(st, "shutdown") else None


asyncio.run(main())
