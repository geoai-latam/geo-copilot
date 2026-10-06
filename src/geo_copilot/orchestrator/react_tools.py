"""Despacho de herramientas del bucle ReAct (Fase 4 / F4.4).

Traduce un ``tool_call`` del LLM (nombre + args) a la EJECUCIÓN de la capacidad
registrada (S1.3: `platform.capabilities`; las `core.*` en
`orchestrator/capabilities_core.py`). Aquí quedan el despacho y los helpers
que comparten los ejecutores, que REUSAN los nodos del grafo existentes
(gis_agent, python_agent, symbology_agent, data_agent). Así el bucle ReAct hereda gratis su HITL, su
auto-corrección (RetryExecutor) y el juez de resultados vacíos (F2.3) — sin
duplicar lógica.

Devuelve un ``ToolOutcome`` con:
  - ``observation``: texto corto que el bucle alimenta de vuelta al LLM (lo que
    "ve" el agente tras actuar);
  - ``delta``: las claves de estado a fusionar en el estado de trabajo del bucle
    (geojson, raw_data, symbology, …) para que las siguientes herramientas operen
    sobre la capa ya cargada;
  - ``success``: si la herramienta produjo un resultado útil.

Aislado del bucle (agent_loop) para poder testear cada pieza por separado.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from geo_copilot.core.logging import get_logger
from geo_copilot.orchestrator.capabilities_core import ensure_core
from geo_copilot.orchestrator.layer_resolution import _tiene_features
from geo_copilot.platform.capabilities import ANSWER_TOOL, ToolOutcome, ejecutar, registry

if TYPE_CHECKING:
    from geo_copilot.orchestrator.graph import GeoAgentGraph

logger = get_logger(__name__)

# Claves de estado que una herramienta puede producir y que las siguientes
# herramientas (o el responder) necesitan ver. ``data``/``visualization`` son
# el CANAL ANALÍTICO (R1.4): tabla/stats/gráfico del sandbox — sin ellas, un
# análisis exitoso dentro del bucle se perdía antes de llegar al responder.
_MERGE_KEYS = (
    "geojson", "raw_data", "sql", "symbology", "layer_name", "error",
    "external_geojson", "has_external_data", "external_source_name",
    "active_data_source", "found_services", "new_search_executed",
    "empty_result_verdict", "data", "visualization", "python_code",
    "external_imagery",  # C3-v1: capa de teselas NDVI
    "service_layers_of",  # rama arcgis-busqueda: de qué servicio ya vio el LLM sus capas
    "analiticos_extra",  # la tabla/estadísticas que acompañan a un gráfico (auditoría F4)
)


def _feature_count(delta: dict) -> int:
    gj = delta.get("geojson") or delta.get("external_geojson") or {}
    fc = len(gj.get("features", [])) if isinstance(gj, dict) else 0
    return fc or len(delta.get("raw_data") or [])


def _hechos(delta: dict, limite: int = 900) -> str:
    """Resumen compacto de los datos de un análisis para la observación."""
    import json

    viz = delta.get("visualization") or {}
    partes: dict[str, Any] = {}
    if viz.get("stats"):
        partes["estadisticas"] = viz["stats"]
    filas = (delta.get("data") or {}).get("results") or []
    if filas:
        partes["filas"] = filas[:8]
        if len(filas) > 8:
            partes["filas_totales"] = len(filas)
    # lo que acompaña al gráfico (la tabla completa, las estadísticas): también se entregó
    for i, extra in enumerate(delta.get("analiticos_extra") or []):
        extra_filas = ((extra or {}).get("data") or {}).get("results") or []
        if extra_filas:
            partes[f"tabla_entregada_{i + 1}"] = extra_filas[:8]
    if not partes:
        return ""
    texto = json.dumps(partes, ensure_ascii=False, default=str)
    if len(texto) > limite:
        texto = texto[:limite] + "…"
    return f"Datos: {texto}. Usa estos números en la respuesta."


def _observe(name: str, delta: dict, layer_fc: int = 0) -> tuple[str, bool]:
    """Resumen legible del resultado de un nodo + si fue útil.

    ``layer_fc``: nº de features de la CAPA de trabajo (del substate). La
    simbología no re-emite el geojson en su delta, así que contarlo del delta
    daba "aplicada a 0 elementos" — y el LLM narraba un fallo inexistente
    aunque el mapa quedara perfecto (hallazgo de la validación en vivo).
    """
    if delta.get("error"):
        if delta.get("requires_hitl") and delta.get("hitl_approved") is False:
            # V5 F5: tras rechazar un SQL, el bucle respondió «no tengo acceso a los datos del
            # catastro». No es falta de acceso: el usuario no aprobó ESA consulta.
            return (f"{name}: el USUARIO rechazó esta consulta (no la aprobó; {str(delta['error'])[:150]}). "
                    "No es falta de acceso a los datos: no la repitas igual; si hace falta, propón otra "
                    "o pregúntale qué quiere.", False)
        return f"{name} falló: {str(delta['error'])[:200]}", False
    # Canal analítico (R1.4): el resultado es tabla/stats/gráfico, no una capa.
    viz = delta.get("visualization")
    if isinstance(viz, dict) and delta.get("data"):
        rows = len((delta["data"] or {}).get("results") or [])
        kind = viz.get("type", "resultado")
        # H8 (acta F0): la observación solo decía "análisis listo (…, 13 filas)"
        # y el LLM del bucle —que es quien redacta la respuesta final en ReAct—
        # no tenía ningún número que narrar: "las estadísticas están listas, si
        # deseas te muestro los detalles". Ahora lleva los HECHOS (estadísticas
        # o primeras filas), acotados para no inflar el contexto.
        # Qué ve el usuario, en palabras: «chart» a secas se leyó como «no hubo
        # gráfico» (V5 F4, juez de la respuesta).
        entregado = {"chart": "GRÁFICO entregado al usuario, con su tabla",
                     "table": "TABLA entregada al usuario", "stats": "ESTADÍSTICAS entregadas al usuario"}
        que = entregado.get(kind, kind)
        return f"{name}: análisis listo ({que}; {rows} fila(s)). {_hechos(delta)}", True
    if delta.get("symbology"):
        fc = _feature_count(delta) or layer_fc
        detalle = f" a {fc} elementos" if fc else " a la capa cargada"
        # H18 (V5 F2): "simbología aplicada" a secas dejó narrar "HH en rojo, HL
        # en naranja…" sobre un mapa de UN solo color (la clasificación pedida
        # se degradó). La observación dice qué se aplicó de verdad.
        sym = delta["symbology"] if isinstance(delta["symbology"], dict) else {}
        tipo = sym.get("symbology_type") or "?"
        campo = sym.get("classification_field")
        clases = len(sym.get("class_breaks") or [])
        que = f"{tipo}" + (f" por «{campo}», {clases} clase(s)" if campo else "")
        if tipo == "single_symbol":
            que += " — UN SOLO COLOR, sin clasificación: no describas colores por categoría"
            # V5 (otra temática, puntos): «según su tipo» sobre un campo constante; el diseñador
            # sabía por qué («RuleID tiene un único valor») y el bucle, sin el motivo, especuló
            # y repitió lo mismo. El motivo es un hecho del resultado.
            porque = str(sym.get("reasoning") or "").replace("[DEGRADED]", "").strip()
            if porque:
                que += f"; motivo del diseñador: «{porque[:300]}»"
        # H21 (V5 F2): el LLM narró "rojo HH, azul LL…" (la convención que
        # conoce) sobre un mapa con otros colores. Van los colores REALES.
        breaks = [b for b in (sym.get("class_breaks") or []) if isinstance(b, dict)][:10]
        if breaks:
            que += "; colores aplicados: " + ", ".join(
                f"{b.get('label')}={b.get('color')}" + (f" ({b['count']})" if b.get("count") is not None else "")
                for b in breaks
            ) + " — si mencionas colores, SOLO estos"
        return f"{name}: simbología aplicada{detalle} ({que})", True
    if delta.get("found_services"):
        return f"{name}: {_candidatos(delta['found_services'])}", True
    if delta.get("service_layers"):
        # un servicio con varias capas: elige el LLM (antes se cargaba la 0, fuera la que fuera)
        de = delta.get("service_layers_of") or {}
        capas = "; ".join(f"id {c.get('id')}: «{c.get('nombre')}» ({c.get('tipo_geometria')})"
                          for c in delta["service_layers"][:40] if isinstance(c, dict))
        porque = f"{de['motivo']}; " if de.get("motivo") else ""
        return (f"{name}: {porque}«{de.get('name') or 'el servicio'}» tiene varias capas y no se cargó ninguna; "
                f"carga la que corresponde a lo pedido con select_service(number, layer=<id>). Capas: {capas}"), True
    n = _feature_count(delta)
    verdict = delta.get("empty_result_verdict")
    if n == 0 and isinstance(verdict, dict):
        return f"{name}: 0 resultados ({verdict.get('verdict')})", True
    # V3 F2: "1 elemento(s)" era TODO lo que el LLM veía de un COUNT(*), y de
    # una consulta cortada por su LIMIT, "1000 elemento(s)": narró 1000
    # construcciones donde había 8442. Los hechos van en la observación.
    return (f"{name}: {n} elemento(s){_capa_externa(delta)}{_tope_alcanzado(delta, n)}{_es_capa(delta)}"
            f"{_filas_tabulares(delta)}"), True


def _capa_externa(delta: dict) -> str:
    """Qué se cargó de verdad de un servicio externo, para comprobar que es lo pedido.

    Rama arcgis-busqueda (V5): con «462 elemento(s)» a secas, el LLM narró «las vías de Bogotá» sobre
    PUNTOS de la cartografía de Cota. Con la capa, su geometría y sus campos, lo puede notar y decirlo."""
    h = delta.get("external_layer_facts")
    if not isinstance(h, dict):
        return ""
    partes = []
    if delta.get("external_source_name"):
        partes.append(f"de «{delta['external_source_name']}»")
    if h.get("geometria"):
        partes.append(f"geometría {h['geometria']}")
    if h.get("campos"):
        partes.append("campos: " + ", ".join(str(c) for c in h["campos"]))
    return (" (" + "; ".join(partes) + ")") if partes else ""


def _candidatos(servicios: list) -> str:
    """Lo encontrado en los portales, con los HECHOS de cada candidato.

    Rama arcgis-busqueda (V5): el LLM solo leía «5 servicio(s) externo(s) encontrados» y elegía «el 1»
    a ciegas (cargó la cartografía de Cota pidiendo las vías de Bogotá). Con título, organización,
    vistas, fecha y tipo, decide él cuál es lo pedido, o busca otra vez con otras palabras."""
    filas = []
    for i, s in enumerate(servicios, 1):
        if not isinstance(s, dict):
            continue
        quien = s.get("credits") or s.get("org") or ""
        if s.get("owner") and s.get("owner") not in quien:
            quien = f"{quien} (cuenta {s['owner']})" if quien else f"cuenta {s['owner']}"
        hechos = [quien or "organización desconocida", str(s.get("type") or "")]
        if isinstance(s.get("views"), int):
            hechos.append(f"{s['views']:,} vistas".replace(",", "."))
        if isinstance(s.get("completeness"), int):
            hechos.append(f"metadatos {s['completeness']}/100")
        if s.get("modified"):
            hechos.append(f"actualizado {str(s['modified'])[:10]}")
        if s.get("single_layer") is True or s.get("layer_id") is not None:
            hechos.append("una capa")
        elif s.get("single_layer") is False:
            hechos.append("servicio con varias capas")
        desc = (s.get("description") or "").strip()
        filas.append(f"{i}. «{s.get('name') or 'sin nombre'}» — " + " · ".join(h for h in hechos if h)
                     + (f" — {desc[:140]}" if desc else ""))
    return (f"{len(filas)} candidato(s) en los portales (elige por sus hechos cuál es lo pedido; si ninguno "
            "lo es, busca otra vez con otras palabras):\n" + "\n".join(filas))


def _es_capa(delta: dict) -> str:
    """V5 F3: sin decirlo, el LLM repetía la consulta «con su geometría» y luego pedía
    permiso para «cargarla como capa» antes de pasarla a otra herramienta."""
    gj = delta.get("geojson")
    if (isinstance(gj, dict) and gj.get("features")) or delta.get("result_layer_ref"):
        return (". Ya es una capa con geometría en el mapa y queda como la capa activa: "
                "para usarla en otra herramienta refiérete a ella como `activa`")
    return ""


_GEOM_KEYS = frozenset({"geom", "geometry", "geom_geojson", "shape", "the_geom", "wkb_geometry"})


def _filas_tabulares(delta: dict, limite: int = 900) -> str:
    """Valores de un resultado SIN capa (agregados, conteos): el LLM los narra.

    FH.7 (LLM real): con capa y pocos elementos (todos caben), también sus atributos con su
    `id` en el mapa: «¿cuál es el lote más grande?» se narraba «1 registro» sin decir cuál.
    Con más, nada (una muestra se tomaría por el total).
    """
    import json

    gj = delta.get("geojson")
    if isinstance(gj, dict) and gj.get("features"):
        feats = gj["features"]
        if len(feats) > 8:
            return ""
        filas_capa = [{"id": f.get("id", i), **{k: v for k, v in (f.get("properties") or {}).items()
                                                  if k not in _GEOM_KEYS}} for i, f in enumerate(feats)]
        texto = json.dumps(filas_capa, ensure_ascii=False, default=str)
        if len(texto) > limite:
            texto = texto[:limite] + "…"
        return (f". Sus {len(feats)} elemento(s) (id en el mapa + atributos): {texto}. Usa estos valores en la "
                f"respuesta; para enlazar uno: [[layer:activa#<id>|texto]]")
    filas = delta.get("raw_data") or []
    if not filas or not isinstance(filas[0], dict):
        return ""
    limpias = [{k: v for k, v in f.items() if k not in _GEOM_KEYS} for f in filas[:8]]
    texto = json.dumps(limpias, ensure_ascii=False, default=str)
    if len(texto) > limite:
        texto = texto[:limite] + "…"
    extra = f" (de {len(filas)})" if len(filas) > 8 else ""
    return f". Datos{extra}: {texto}. Usa estos números en la respuesta"


def _tope_alcanzado(delta: dict, n: int) -> str:
    """Aviso si la consulta devolvió exactamente su LIMIT: el total real puede ser mayor."""
    import re

    m = re.search(r"\bLIMIT\s+(\d+)\s*;?\s*$", str(delta.get("sql") or ""), re.IGNORECASE)
    if not m:
        return ""
    tope = int(m.group(1))
    if n < tope:
        # V3 F2: tras un aviso de tope en un paso previo, el LLM narró un COUNT
        # exacto como "al menos… aproximado". Este resultado SÍ es completo.
        return " (resultado COMPLETO: no llegó al LIMIT; es exacto)"
    return (
        f" — ⚠ la consulta llegó a su LIMIT {tope}: hay {tope} O MÁS, no es el total; "
        "si la pregunta es cuántos, cuenta con una consulta de agregación (COUNT)"
    )


def _pick(delta: dict) -> dict:
    """Selecciona del delta del nodo solo las claves de estado a propagar."""
    return {k: delta[k] for k in _MERGE_KEYS if k in delta}


def _effective_source(working_state: dict) -> str:
    """Fuente activa EFECTIVA para una herramienta que opera sobre la capa.

    OJO: ``"none"`` es un string TRUTHY — el patrón viejo
    ``working_state.get("active_data_source") or fallback`` lo dejaba pasar y
    el python_agent rechazaba con "sin fuente activa" aunque query_database
    acababa de cargar datos (bug destapado por el bench hybrid).
    """
    src = working_state.get("active_data_source")
    if src in (None, "", "none"):
        src = "external" if working_state.get("external_geojson") else "internal"
    return cast(str, src)


def _resolve_target(args: dict[str, Any], working_state: dict) -> str | None:
    """FRT-04: id de la capa OBJETIVO, VALIDADO contra ``map_layers``.

    Preferencia:
      1. el arg EXPLÍCITO de la tool — el LLM del bucle dirigió ESTA acción a
         una capa concreta (per-tool, correcto en flujos multi-paso);
      2. si el arg falta o es inválido, el ``target_layer_id`` que el nodo
         router YA resolvió para la query (mismo map_context, misma regla).

    Ese fallback (2) es crítico: el nodo router SIEMPRE corre antes del bucle y
    resuelve la capa nombrada, pero el override de la tool ponía None cuando el
    LLM no repetía el arg — y ese None PISABA la resolución del router, dejando
    la simbología en la capa activa (bug destapado en validación en vivo).
    Un id que no existe en map_layers se descarta (→ intenta el siguiente → capa
    activa), no se usa a ciegas.
    """
    from geo_copilot.orchestrator.layer_resolution import id_de_capa

    for tid in (args.get("target_layer_id"), working_state.get("target_layer_id")):
        capa = id_de_capa(tid, working_state)
        if capa:
            return capa
    return None


async def _run_node(
    graph: GeoAgentGraph,
    node_run,
    working_state: dict,
    overrides: dict,
    *,
    clear: tuple[str, ...] = (),
    deriva_capa: bool = False,
) -> ToolOutcome:
    """Ejecuta un nodo del grafo con un sub-estado y normaliza su salida.

    ``clear`` impone PIZARRA LIMPIA (revisión adversarial F4.4, misma clase de bug
    que F3.1/step_router): las claves de output listadas que el nodo NO re-emitió
    se ponen a ``None`` en el delta, para que un dato/​error de una herramienta
    previa NO persista en el estado de trabajo y se reporte como si fuera de ésta.

    ``deriva_capa``: la herramienta TRANSFORMA la capa objetivo en otra nueva (buffer,
    análisis con geometría): si la produjo, esa es la capa en foco, no la de entrada.
    """
    substate = {**working_state, **overrides}
    raw = await node_run(graph, substate)
    raw = raw if isinstance(raw, dict) else {}
    name = overrides.get("intent", "tool")
    # La capa sobre la que actuó el nodo, con SU regla (V5 F4: «simbología aplicada a
    # 30 elementos» cuando coloreó la capa objetivo, de 15).
    from geo_copilot.orchestrator.layer_resolution import resolver_capa

    foco = resolver_capa(substate)
    layer_fc = len(foco.geojson.get("features") or []) if foco and isinstance(foco.geojson, dict) else 0
    obs, ok = _observe(name, raw, layer_fc=layer_fc or _feature_count(substate))
    picked = _pick(raw)
    if "geojson" not in clear and picked.get("geojson") is None:
        # Una transformación/análisis que NO produjo geometría (solo tabla o
        # gráfico) no borra la capa que traía el turno: su None es «no hay
        # geometría nueva», no «vaciar el mapa» (V5 F4: «trae los lotes y hazme
        # un gráfico del área» perdía los lotes).
        # (raw_data sí se limpia: son filas de ESTE paso, ver _TRANSFORM_CLEAR.)
        for k in ("geojson", "external_geojson", "has_external_data"):
            picked.pop(k, None)
        # La capa que se conserva sigue siendo el MISMO dataset del workspace.
        clear = tuple(k for k in clear if k != "result_layer_ref")
    delta = {k: None for k in clear if k not in picked}
    delta.update(picked)
    # FRT-04: propagar la capa objetivo que la tool eligió (override) al delta,
    # para que fluya al working_state y salga del bucle (→ query.py → el cliente
    # re-estila ESA capa). Sin esto, el override sólo vive en el substate.
    if deriva_capa and _tiene_features(picked.get("geojson")):
        # La capa NUEVA queda en foco (la observación le dice al LLM que es la `activa`). V3 F5
        # (E2.6): tras «buffer de 500 m a esos lotes», ws_measure(activa) midió los LOTES
        # (0,77 ha) porque el objetivo seguía siendo la capa de entrada, y el LLM acabó
        # pidiéndole al usuario que seleccionara el buffer.
        delta["target_layer_id"] = None
    elif overrides.get("target_layer_id"):
        delta["target_layer_id"] = overrides["target_layer_id"]
    elif "geojson" in clear and _tiene_features(picked.get("geojson")):
        # Una herramienta que TRAE una capa nueva al turno la pone en foco: el
        # objetivo que el router resolvió contra el mapa de ANTES ya no es el
        # defecto de los pasos siguientes (V5 F4: «trae los lotes de la manzana
        # 004503001, grafica su área y coloréalos» graficó y coloreó la manzana
        # que ya estaba en el mapa). Si el LLM nombra una capa, sigue mandando.
        delta["target_layer_id"] = None
    return ToolOutcome(observation=obs, success=ok, delta=delta)


# Claves de output que una herramienta que GENERA datos frescos debe dejar
# limpias si no las re-emite (mismo contrato que step_router en F3.1).
# Incluye el canal analítico: datos nuevos invalidan la tabla/gráfico viejos.
_FRESH = (
    "geojson", "raw_data", "sql", "symbology", "layer_name", "error",
    "data", "visualization", "python_code", "analiticos_extra",
    # S2.5: el dataset de una ws_* previa ya no es la capa del turno.
    "result_layer_ref",
)

# Claves que una TRANSFORMACIÓN/ANÁLISIS sobre la capa cargada limpia si no
# re-emite: conserva el geojson de entrada, pero un resultado analítico o un
# código de un paso previo no deben sobrevivir como si fueran de este paso.
_TRANSFORM_CLEAR = (
    "raw_data", "sql", "error", "data", "visualization", "python_code", "result_layer_ref",
    "analiticos_extra",
)


async def dispatch_tool(
    graph: GeoAgentGraph, working_state: dict, name: str, args: dict[str, Any]
) -> ToolOutcome:
    """Ejecuta la herramienta ``name`` con ``args`` y devuelve su resultado.

    La terminal ``answer`` no ejecuta nada: marca el fin del bucle. Una
    herramienta desconocida devuelve una observación honesta (no crashea).
    """
    args = args or {}

    if name == ANSWER_TOOL:
        text = str(args.get("text") or "").strip()
        return ToolOutcome(observation="(respuesta final)", success=True,
                           is_final=True, final_text=text)

    # S1.3: una capacidad es una entrada del registro; ya no hay una cadena de
    # `if name == ...` que tocar para añadir una herramienta.
    ensure_core()
    cap = registry().get(name)
    if cap is None:
        return ToolOutcome(
            observation=f"herramienta desconocida '{name}'; elige una del catálogo",
            success=False,
        )
    # F6: con los permisos del usuario y su constancia en la auditoría
    return await ejecutar(cap, graph, working_state, args, origen="agente")
