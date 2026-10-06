"""
GISAgent node — extraído de ``graph.py`` en Fase 6 #6 parte 2.

Genera y ejecuta SQL espacial:

1. **Generación**: si no hay SQL corregido previo, pide al ``GISAgent``
   que produzca uno a partir de la query del usuario y el schema.
2. **HITL**: si está habilitado, pide aprobación al usuario antes de
   ejecutar. Estados terminales (REJECTED/EXPIRED) salen sin reintento.
3. **Ejecución**: corre el SQL via ``GISAgent._execute_sql``. En éxito,
   devuelve los datos al estado del grafo.
4. **Auto-corrección**: si la ejecución falla y estamos en modo
   autónomo, ``SQLCorrector`` genera un SQL alternativo y se reintenta.
   El bucle (intento + corrección + notificación) se delega a
   ``RetryExecutor`` (orchestrator/retry.py — Fase 6 #4) en vez de la
   re-implementación inline que tenía graph.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from geo_copilot.agents.gis_agent.sql_corrector import (
    SQLCorrector,  # noqa: F401 — lo usa IntentoSQL vía el nodo
)
from geo_copilot.core.colors import log_agent
from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.nodes.gis_intento import IntentoSQL
from geo_copilot.orchestrator.retry import RetryExecutor

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

logger = get_logger(__name__)


def identify_sql_risks(sql: str) -> list[str]:
    """Heurística rápida de riesgos para enseñar al revisor HITL."""
    risks: list[str] = []
    sql_upper = sql.upper()
    if "DELETE" in sql_upper or "DROP" in sql_upper:
        risks.append("Contiene operaciones destructivas (DELETE/DROP)")
    if "UPDATE" in sql_upper or "INSERT" in sql_upper:
        risks.append("Contiene operaciones de escritura (UPDATE/INSERT)")
    if "LIMIT" not in sql_upper:
        risks.append("Sin cláusula LIMIT - puede retornar muchos registros")
    if sql_upper.count("JOIN") > 3:
        risks.append("Múltiples JOINs pueden afectar rendimiento")
    return risks


def bloque_zona_visible(bbox: list[float]) -> str:
    """La zona visible del mapa para el generador de SQL (Fase B / P1-C).

    En las dos direcciones (V5 F3): filtrar cuando el usuario la pide, y NO
    filtrar cuando no la pide — «los lotes de la manzana X» con el mapa en otra
    parte devolvía 0 filas y la respuesta decía que la manzana no existía.
    """
    x0, y0, x1, y1 = bbox
    return (
        f"\n\nZONA VISIBLE ACTUAL (bbox EPSG:4326): [{x0}, {y0}, {x1}, {y1}].\n"
        "- Si el usuario pide la zona visible ('en esta zona' / 'aquí' / 'lo que se "
        "ve' / 'lo visible' / 'en la pantalla'), DEBES filtrar en el WHERE con "
        f"ST_Intersects(<columna_geometria>, ST_MakeEnvelope({x0}, {y0}, {x1}, {y1}, 4326)); "
        "nunca traigas la tabla completa en ese caso.\n"
        "- Si NO la pide (p. ej. identifica lo que quiere por código, nombre o "
        "atributo), NO filtres por la zona visible: lo pedido puede estar fuera de "
        "la pantalla."
    )


def _msg(content: str, *, success: bool, data: dict | None = None) -> dict:
    return {"agent": "gis_agent", "content": content, "data": data, "success": success}


def _capas_en_memoria(state: Any) -> str:
    """Hecho para el bucle cuando no hay BD: qué capas del mapa tiene el turno EN MEMORIA.

    fh5 (2026-10-06): sin BD, el bucle respondía «no puedo acceder a la base de datos» aunque la
    capa filtrada (10 lotes) estaba en el turno; el desglose por uso que pedía el usuario se
    podía hacer sobre ella. Es un dato, no una orden: el modelo decide qué hacer con él.
    """
    capas = []
    for lid, capa in (state.get("map_layers") or {}).items():
        datos = (capa or {}).get("data") if isinstance(capa, dict) else None
        n = len(datos.get("features") or []) if isinstance(datos, dict) else 0
        if n:
            capas.append(f"«{(capa.get('name') or lid)}» ({n} elementos)")
    if not capas:
        return ""
    return ". Capas del mapa en memoria en este turno (se pueden analizar sin la BD): " + "; ".join(capas[:3])


def _otras_fuentes() -> str:
    """Las otras fuentes conectadas (resumen del hub), para el generador de SQL de la BD interna."""
    if not _hay_servicios_conectados():
        return ""
    from geo_copilot.platform.mcp.hub import hub_actual

    hub = hub_actual()
    resumen = hub.resumen_prompt() if hub is not None else ""
    return ("\n\nOTRAS FUENTES CONECTADAS (NO están en esta base de datos; se consultan con otras "
            "herramientas). Si lo pedido está en una de ellas y no en las tablas de arriba, NO lo "
            "aproximes con otra tabla: responde SOLO con un comentario SQL (-- …) que lo diga.\n"
            + resumen) if resumen else ""


def _hay_servicios_conectados() -> bool:
    """¿Hay algún servicio MCP disponible con herramientas? (un hecho del hub, no un juicio)"""
    from geo_copilot.platform.mcp.hub import hub_actual

    hub = hub_actual()
    # solo cuentan las que ve el AGENTE (T5.2: las del servidor de ArcGIS son del núcleo)
    return bool(hub is not None and any(c.estado == "disponible" for c in hub.conexiones.values())
                and any(e.habilitada and e.servidor in hub.conexiones
                        and hub.conexiones[e.servidor].cfg.tools.para_agente(e.tool)
                        for e in hub.tools.values()))


def _texto_de_comentarios(sql: str) -> str:
    """El texto de un «SQL» que son solo comentarios (`-- …` y `/* … */`), en una línea."""
    import re

    partes = [m.group(1) or m.group(2) for m in re.finditer(r"--([^\n]*)|/\*(.*?)\*/", sql, re.S)]
    return " ".join(" ".join((p or "").split()) for p in partes if p and p.strip()).strip()


async def _preflight_entities(
    graph: GeoAgentGraph,
    state: GraphState,
    a2a_calls: list[dict],
) -> str:
    """A2A pre-flight: validar entidades del state contra el DataAgent.

    Devuelve un bloque de texto listo para inyectar al prompt del SQL
    generator. ``""`` si no hay hub, no hay entidades, o todas son
    triviales (cadenas vacías).

    Cada llamada al hub se appendea a ``a2a_calls`` para que el caller la
    propague al state (telemetría).
    """
    hub = getattr(graph, "agent_hub", None)
    if hub is None:
        return ""

    entities = [e for e in (state.get("entities") or []) if isinstance(e, str) and e.strip()]
    if not entities:
        return ""

    # F3.2: memoria de entidades por sesión (control de confianza).
    memory = getattr(graph, "entity_memory", None)
    session_id = state.get("session_id") or ""

    lines: list[str] = []
    any_correction = False
    for ent in entities:
        # F3.2: ¿ya resolvimos esta entidad con ALTA confianza en esta sesión?
        # Reutilizamos sin re-llamar al DataAgent. Las fuzzy NO se cachean como
        # autoritativas (get() solo devuelve alta confianza) → se re-verifican.
        if memory is not None:
            recordada = _de_memoria(memory, session_id, ent, a2a_calls)
            if recordada is not None:
                lines.append(recordada)
                continue

        info = await _consultar(hub, memory, session_id, ent, a2a_calls)
        if info is None:
            continue

        linea, correccion = _linea_del_lookup(ent, info)
        lines.append(linea)
        any_correction = any_correction or correccion

    if not lines:
        return ""

    header = "\n\n## VALIDACIÓN A2A DE ENTIDADES (DataAgent ↔ GIS Agent)\n"
    footer = ""
    if any_correction:
        footer = (
            "\n⚠️ REGLA: cuando el DataAgent sugiere una corrección por typo, "
            "el SQL DEBE usar ese nombre canónico — no el texto literal del "
            "usuario. Esto evita SQL contra tablas inexistentes."
        )
    return header + "\n".join(lines) + footer


async def _consultar(hub: Any, memory: Any, session_id: str, ent: str,
                     a2a_calls: list[dict]) -> dict | None:
    """Lo que el DataAgent sabe de la entidad (vía A2A), recordado y anotado; None si falló."""
    ok, payload = await hub.call(
        caller="gis_agent_node",
        target="data_agent",
        method="lookup_entity",
        name=ent,
    )
    if not ok:
        logger.debug(f"[A2A] skip lookup for {ent!r}: {payload}")
        return None
    info = payload.to_dict() if hasattr(payload, "to_dict") else dict(payload)

    # F3.2: recordar la resolución (la confianza la deriva resolution_from_
    # lookup: exacto=1.0 reutilizable, fuzzy=0.5 no reutilizable como hecho).
    if memory is not None:
        from geo_copilot.orchestrator.entity_memory import resolution_from_lookup
        memory.remember(session_id, resolution_from_lookup(ent, info))

    a2a_calls.append({
        "from": "gis_agent_node",
        "to": "data_agent",
        "method": "lookup_entity",
        "query": ent,
        "exists": info.get("exists"),
        "canonical_name": info.get("canonical_name"),
        "suggestions": info.get("suggestions") or [],
    })
    return info


def _de_memoria(memory: Any, session_id: str, ent: str, a2a_calls: list[dict]) -> str | None:
    """F3.2: la línea de una entidad ya resuelta con ALTA confianza en la sesión (None si no lo está)."""
    cached = memory.get(session_id, ent)
    if cached is not None:
        a2a_calls.append({
            "from": "gis_agent_node",
            "to": "data_agent",
            "method": "lookup_entity",
            "query": ent,
            "exists": True,
            "canonical_name": cached.canonical,
            "suggestions": [],
            "cached": True,  # vino de EntityMemory, no de un A2A fresco
        })
        table = cached.table or "?"
        if (cached.canonical or "").lower() != ent.lower():
            return (
                f"- Usuario dijo '{ent}' → entidad '{cached.canonical}' "
                f"(tabla {table}) [memoria de sesión]. USA EL NOMBRE CANÓNICO."
            )
        else:
            return (
                f"- '{ent}' confirmada (tabla {table}) [memoria de sesión]."
            )
    return None


def _linea_del_lookup(ent: str, info: dict) -> tuple[str, bool]:
    """(línea para el prompt, si es una corrección por parecido) según lo que respondió el DataAgent."""
    correccion = False
    if info.get("exists"):
        canonical = info.get("canonical_name") or ent
        table = info.get("table") or "?"
        if canonical.lower() != ent.lower():
            linea = (
                f"- Usuario dijo '{ent}' → el DataAgent confirma la "
                f"entidad '{canonical}' (tabla {table}). USA EL NOMBRE "
                f"CANÓNICO en el SQL."
            )
        else:
            linea = (f"- '{ent}' confirmada por DataAgent (tabla {table}).")
    else:
        suggestions = info.get("suggestions") or []
        if suggestions:
            any_correction = True
            sug_str = ", ".join(f"'{s}'" for s in suggestions)
            # Hecho, no orden: las sugerencias salen de un parecido de LETRAS (difflib), no de
            # significado («edificaciones» no se parece a `construcciones`). Antes: «USA LA
            # SUGERENCIA MÁS CERCANA», que imponía al LLM un parecido textual.
            linea = (
                f"- Usuario dijo '{ent}' y no figura con ese nombre en el catálogo. Nombres "
                f"parecidos POR ESCRITURA (no necesariamente por significado): {sug_str}. "
                f"Decide con el schema qué tabla corresponde a lo que pide; no inventes tablas."
            )
        else:
            linea = (
                f"- '{ent}': el catálogo A2A no la confirmó y no hay "
                f"sugerencias, PERO el catálogo puede estar desincronizado "
                f"o la entidad puede llamarse distinto en el SCHEMA. Revisa "
                f"el schema provisto y genera SQL contra la tabla más "
                f"plausible. Solo si NINGUNA tabla del schema corresponde, "
                f"dilo honestamente — NO fabriques un conteo 0 ni datos."
            )
    return linea, correccion


async def _al_bucle(graph: GeoAgentGraph, state: GraphState, motivo: str, nota: str) -> dict | None:
    """El turno pasa al bucle ReAct (que ve las bases, archivos y servicios conectados) si la política
    lo permite; None si no. F5 (T5.1) / T5.6 (V5): la BD interna no tiene lo pedido."""
    from geo_copilot.core.config import get_settings as _ajustes
    from geo_copilot.orchestrator.graph import _resolve_react_policy

    if _resolve_react_policy(_ajustes()) not in ("hybrid", "always"):
        return None
    from geo_copilot.orchestrator.nodes import agent_loop

    logger.info(nota)
    return await agent_loop.run(graph, {**state, "intent": "analyze", "interpretacion_previa": motivo})


async def _tras_reintentos(graph: GeoAgentGraph, state: GraphState, result: Any, intento: IntentoSQL,
                           max_retries: int) -> dict:
    """Se agotaron los reintentos: o el turno pasa al bucle (tabla inexistente con servicios
    conectados) o se devuelve el error con su contexto."""
    session_id = state.get("session_id", "")
    final_error = result.outcome.error or "Unknown error"
    if ("tabla no disponible en el catálogo" in final_error and not state.get("_desde_bucle")
            and _hay_servicios_conectados()):
        # T5.6 (V5): «los predios del GeoParquet» → el generador de la BD interna inventó
        # `archivos.predios` (confundió un servicio conectado con una tabla) y la respuesta fue
        # «no encontré esa tabla». Igual que cuando dice que no lo tiene: decide el bucle.
        motivo = (f"la BD interna no tiene lo pedido (su SQL falló: «{final_error[:200]}»); mira si "
                  "está en una de las bases de datos, archivos o servicios conectados")
        salida = await _al_bucle(graph, state, motivo, "[GISAgent] tabla inexistente en la BD interna → agent_loop")
        if salida is not None:
            return salida
    if session_id and result.attempts > 1:
        from geo_copilot.platform import events
        await events.sink().retry_result(
            session_id=session_id, agent="gis_agent", success=False, attempts=result.attempts,
            message=f"No se pudo corregir el error: {final_error[:100]}",
        )
    return {
        "current_agent": "gis_agent",
        "error": final_error,
        "sql": intento.current,
        "retry_count": max(0, result.attempts - 1),
        "last_error": final_error,
        "error_context": {
            "all_errors": intento.errors_collected,
            "attempts": result.attempts,
            "max_retries": max_retries,
        },
        "messages": [_msg(
            f"Error después de {result.attempts} intento(s): {final_error[:100]}",
            success=False,
            data={"sql": intento.current, "errors": intento.errors_collected},
        )],
    }


async def run(graph: GeoAgentGraph, state: GraphState) -> dict:
    """Generar + (HITL) + ejecutar + auto-corregir SQL hasta ``max_retries``."""
    log_agent("GISAgent", "Generando SQL...", f"Query: {state['query'][:60]}...")
    logger.info("[GISAgent] Processing query...")

    settings = get_settings()
    session_id = state.get("session_id", "")
    autonomous_mode = state.get("autonomous_mode", settings.autonomous_mode)
    max_retries = state.get("max_retries", settings.max_retries)
    max_attempts = (max_retries + 1) if autonomous_mode else 1
    # F4.2 cutover: HITL por LangGraph interrupt (en vez del HITLManager bloqueante)
    # cuando hitl_mode='interrupt'. ``_pending_sql`` (en el grafo) da idempotencia
    # al resume: el SQL no se regenera al re-ejecutar el nodo.
    hitl_interrupt_mode = (
        settings.hitl_enabled
        and getattr(settings, "hitl_mode", "blocking") == "interrupt"
        and getattr(graph, "_pending_sql", None) is not None
    )

    if not graph.db_pool:
        return {
            "current_agent": "gis_agent",
            "error": "No database connection" + _capas_en_memoria(state),
            "messages": [_msg("No hay conexión a BD", success=False)],
        }

    schema_info = await graph.gis_agent.get_db_schema()
    # S2.2: los datasets del workspace de ESTA sesión entran al contexto del
    # LLM y a la allowlist del validador (solo los suyos).
    schema_info += await _contexto_workspace(state.get("session_id") or "", state.get("map_context"))

    # A2A (2026-05-31): pre-flight — consultar al DataAgent si las
    # entidades mencionadas por el usuario existen / tienen typos. El
    # resultado se inyecta al prompt del SQL generator como "hints" para
    # que el LLM use el nombre canónico en vez de alucinar tablas. Si no
    # hay hub o no hay entidades, el bloque queda vacío y el flujo es
    # idéntico al pre-A2A.
    a2a_calls: list[dict] = []
    a2a_hints = await _preflight_entities(graph, state, a2a_calls)

    intento = IntentoSQL(graph=graph, state=state, settings=settings, schema_info=schema_info,
                         a2a_hints=a2a_hints, a2a_calls=a2a_calls, max_attempts=max_attempts,
                         autonomous_mode=autonomous_mode, hitl_interrupt_mode=hitl_interrupt_mode)
    result = await RetryExecutor(max_attempts=max_attempts).run(
        attempt=intento.intento,
        correct=intento.corregir if autonomous_mode else None,
        on_retry=intento.notificar_reintento,
    )

    # F4.2: el nodo terminó (éxito/terminal/agotado) → ya no hay aprobación
    # pendiente para esta sesión; limpiar el cache de idempotencia. (Si el nodo
    # se pausó por interrupt, NO llega aquí — el cache persiste para el resume.)
    if hitl_interrupt_mode:
        graph._pending_sql.pop(session_id, None)

    # Éxito o terminación sin reintento → propagamos el payload completo.
    if result.outcome.success or result.outcome.no_retry:
        payload = result.outcome.payload
        sin_sql = bool(payload and payload.pop("_sin_sql", False))
        if sin_sql and not state.get("_desde_bucle") and _hay_servicios_conectados():
            # F5 (T5.1): la BD interna no tiene lo pedido (su generador lo explicó) y hay bases o
            # servicios conectados: el turno pasa al bucle, que los ve y decide (describirlos,
            # consultarlos o decir que no está). Antes «sedes educativas de Soacha» moría en
            # «no existe esa tabla» con una BD conectada que la tenía.
            motivo = (f"la BD interna no lo tiene (su generador de SQL dijo: «{payload['final_response'][:300]}»); "
                      "mira si está en una de las bases de datos o servicios conectados")
            salida = await _al_bucle(graph, state, motivo, f"[GISAgent] sin SQL en la BD interna → agent_loop: {motivo}")
            if salida is not None:
                return salida
        return payload
    return await _tras_reintentos(graph, state, result, intento, max_retries)


async def _contexto_workspace(session_id: str, map_context: dict | None = None) -> str:
    """Fija las tablas del workspace de la sesión y devuelve su bloque de esquema."""
    from geo_copilot.platform.workspace.context import (
        bloque_para_llm,
        fijar_datasets,
        store_actual,
    )

    store = store_actual()
    if not session_id or store is None:
        fijar_datasets([])
        return ""
    try:
        refs = await store.list_datasets(session_id)
    except Exception:  # el workspace es aditivo: sin él, el SQL sigue sobre la BD de dominio
        logger.warning("[GISAgent] no se pudo leer el workspace de la sesión", exc_info=True)
        fijar_datasets([])
        return ""
    fijar_datasets(refs)
    from geo_copilot.core.formatters import describir_filtro

    filtros = {c["dataset_id"]: describir_filtro(c["filtro"])
               for c in ((map_context or {}).get("layers") or []) if c.get("dataset_id") and c.get("filtro")}
    return bloque_para_llm(refs, filtros, await _valores_de_datasets_pequenos(store, session_id, refs))


#: Hasta cuántos elementos un dataset del workspace lleva sus valores al contexto del SQL.
MAX_ELEMENTOS_CON_VALORES = 20


async def _valores_de_datasets_pequenos(store, session_id: str, refs: list) -> dict[str, str]:
    """{ds_id: «nombre='UPZ Teusaquillo', nivel='quarter'»} de los datasets pequeños (hechos para filtrar)."""
    from geo_copilot.platform.contracts import WorkspaceTable
    from geo_copilot.platform.workspace.ops import _tabla
    from geo_copilot.platform.workspace.store import _qi

    out: dict[str, str] = {}
    for r in refs:
        if not isinstance(r.storage, WorkspaceTable) or not (0 < (r.feature_count or 0) <= MAX_ELEMENTOS_CON_VALORES):
            continue
        textos = [f.name for f in r.fields if f.type == "string"][:6]
        if not textos:
            continue
        try:
            filas = await store.filas(session_id, f"SELECT {', '.join(_qi(c) for c in textos)} FROM {_tabla(r)} LIMIT "
                                                  f"{MAX_ELEMENTOS_CON_VALORES}")
        except Exception:  # sin valores el SQL sigue con las columnas (como antes)
            logger.debug("[GISAgent] sin valores del dataset %s", r.id, exc_info=True)
            continue
        partes = []
        for c in textos:
            distintos = sorted({str(f[c]) for f in filas if f.get(c) is not None})[:10]
            if distintos:
                partes.append(f"{c} ∈ {{{', '.join(repr(v[:60]) for v in distintos)}}}")
        if partes:
            out[r.id] = "; ".join(partes)
    return out


def _describir_fallo(exc: BaseException) -> str:
    """El error de ejecución como lo necesita el corrector SQL.

    H17 (V5 de F2): un timeout llegaba como TimeoutError con mensaje VACÍO
    ("Unknown error"); el corrector no sabía por qué fallaba y tras tres
    intentos lentos el LLM bajó el LIMIT a 50: truncó el resultado en silencio.
    """
    import asyncio

    nombre = type(exc).__name__
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or nombre in (
        "QueryCanceledError", "QueryCanceled",
    ):
        return (
            "timeout: la consulta excedió el tiempo máximo. Casi siempre es un recorrido "
            "de la tabla entera sin índice espacial (p. ej. ST_DWithin/ST_Intersects "
            "sobre col::geography o ST_Transform(col)). Añade un PREFILTRO INDEXABLE sobre "
            "la columna cruda: t.\"geom\" && ST_Expand(ref, grados) antes de la medida "
            "exacta. NO bajes el LIMIT para esquivarlo: eso trunca el resultado."
        )
    return str(exc) or nombre
