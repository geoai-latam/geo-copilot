"""
Funciones de formateo compartidas para GEO_COPILOT.

Centraliza la lógica de formateo usada por múltiples agentes y componentes.

También es la FUENTE ÚNICA de "¿qué SRID mide en metros?" (`is_metric_crs`,
`check_measurable_crs`, `crs_units_note`). Vive aquí y no en el validador SQL
porque `core/` lo puede importar todo el mundo y `agents/` no: el prompt que ve
el LLM y la regla que le hacen cumplir tienen que salir del mismo sitio.
"""


def format_found_services(found_services: list[dict] | None, max_services: int = 10) -> str:
    """
    Formatear lista de servicios encontrados para mostrar al usuario.

    Args:
        found_services: Lista de servicios con 'name' o 'title'
        max_services: Máximo de servicios a mostrar

    Returns:
        String formateado con la lista numerada de servicios
    """
    if not found_services:
        return ""

    lines = ["SERVICIOS ENCONTRADOS EN BÚSQUEDA ANTERIOR (el usuario puede seleccionar por número):"]
    for i, svc in enumerate(found_services[:max_services], 1):
        name = svc.get("name", svc.get("title", "Sin nombre"))
        lines.append(f"  {i}. {name}")

    return "\n".join(lines)


def format_external_data_context(
    has_external_data: bool,
    source_name: str = "servicio externo",
    feature_count: int = 0
) -> str:
    """
    Formatear contexto de datos externos cargados.

    Args:
        has_external_data: Si hay datos externos
        source_name: Nombre de la fuente
        feature_count: Número de features

    Returns:
        String formateado con información del contexto
    """
    if not has_external_data:
        return ""

    return f"""DATOS EXTERNOS CARGADOS EN MEMORIA:
  - Fuente: {source_name}
  - Features disponibles: {feature_count}
  - IMPORTANTE: Para operaciones espaciales (buffer, área, centroide, unir, etc.) sobre estos datos, usa "spatial_operation"
  - Operaciones disponibles: buffer, centroid, area, union, intersect, dissolve, clip, simplify, distance"""


def format_active_layer_context(
    *,
    active_source: str,
    source_name: str | None,
    feature_count: int,
    geometry_type: str | None = None,
    field_names: list[str] | None = None,
) -> str:
    """Formato del contexto de "CAPA ACTIVA" para el Smart Router.

    Cubre tanto datos internos (PostGIS) como externos (ArcGIS Hub /
    archivo). Antes solo había ``format_external_data_context`` y el
    router NO sabía cuándo una consulta era "ajusta la capa interna que
    está cargada" — terminaba volviendo a ejecutar SQL.

    Args:
        active_source: "internal" | "external" | "previous" | "none".
        source_name: Etiqueta humana de la fuente (puede ser ``None``).
        feature_count: Número de features visibles en el mapa.
        geometry_type: "Point" / "Polygon" / ... si se conoce.
        field_names: Campos disponibles por feature (para que el LLM
            sepa qué atributos puede usar en simbología / preguntas).

    Returns:
        Bloque de texto listo para inyectar en el prompt, o ``""`` si
        no hay capa activa.
    """
    if active_source == "none" or feature_count <= 0:
        return ""

    source_label = {
        "internal": "BASE DE DATOS INTERNA",
        "external": "SERVICIO EXTERNO",
        "previous": "CAPA HEREDADA DEL TURNO ANTERIOR",
    }.get(active_source, "FUENTE DESCONOCIDA")

    lines = [
        "CAPA ACTIVA EN EL MAPA (el usuario YA tiene estos datos cargados):",
        f"  - Fuente: {source_label}" + (f" — {source_name}" if source_name else ""),
        f"  - Features: {feature_count}",
    ]
    if geometry_type:
        lines.append(f"  - Geometría: {geometry_type}")
    if field_names:
        sample = ", ".join(field_names[:15])
        more = f" (+{len(field_names) - 15} más)" if len(field_names) > 15 else ""
        lines.append(f"  - Campos: {sample}{more}")

    lines.append(
        "  - IMPORTANTE: si el usuario pide ajustes sobre estos datos "
        "(color/estilo, buffer/centroide/área, preguntas sobre atributos), "
        "NO consultes la BD ni busques fuera. Elige `apply_symbology`, "
        "`spatial_operation` o `follow_up` según el caso."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CRS y unidades — FUENTE ÚNICA (revisión del 8-sep-2026)
#
# En el repo convivían TRES definiciones vivas de "CRS métrico" y se
# contradecían entre sí:
#
#   1. `is_metric_crs` aquí abajo: `srid == 3857 or 32601<=srid<=32660 or
#      32701<=srid<=32760`. Solo Pseudo-Mercator y las zonas UTM. Decía que
#      **9377 y 3116 NO son métricos** —los dos CRS proyectados oficiales de
#      Colombia, ambos en metros, que son el caso de uso principal— y sí
#      aceptaba 3857. `tests/test_crs_units.py` probaba 4326, 32618, 32718 y
#      `None`: ni un CRS colombiano, que es cómo sobrevivió.
#   2. Los prompts de `gis_agent/agent.py:947-949`.
#   3. El rango 4000–4999 del validador AST (auditoría 2026-09-08, §1.4),
#      enumerado y descartado: de los 804 CRS geográficos vigentes 385 caen
#      fuera y de los 5.291 proyectados 215 caen dentro.
#
# Lo grave no era ninguna por separado: era que el prompt le decía una cosa al
# modelo (`crs_units_note` alimenta `semantic/layer.py:440`) y el validador le
# hacía cumplir otra. Ahora las dos preguntas se contestan aquí, contra la base
# EPSG que trae pyproj —sin red— y `sql_ast_validator` importa de aquí.
#
# Son DOS preguntas distintas y hay que no mezclarlas:
#
#   `is_metric_crs`        ¿las coordenadas están en metros? 3857 SÍ lo está.
#   `check_measurable_crs` ¿se puede MEDIR área o longitud ahí? 3857 NO: es
#                          conforme, no equivalente, y deforma la magnitud.
# ---------------------------------------------------------------------------

# T5.1: vive en `geo_sql_guard.crs` (lo comparte el servidor MCP de SQL); aquí se reexporta.
from geo_sql_guard.crs import (  # noqa: F401
    _METRICOS_QUE_DEFORMAN,
    _describe_crs,
    check_measurable_crs,
    is_metric_crs,
)

# F4: el contexto del mapa vive en contexto_mapa; se reexporta porque el resto del código lo importa
# de `formatters`.
from geo_copilot.core.contexto_mapa import (  # noqa: F401
    _respuesta_mapa,
    describir_filtro,
    format_map_context,
)


def crs_units_note(srid: int | None) -> str:
    """F1.3: nota honesta de unidades para un SRID. Reusada por el
    SpatialReasoner (F2.1) y por el contexto que ve el LLM
    (`semantic/layer.py:440`).

    Evita reportar "m²" cuando el cálculo fue en grados: un ST_Area sobre
    una geometría en 4326 da grados² (sin sentido físico).

    Antes decidía por rangos y para 9377 —el CRS oficial de Colombia— decía
    "verificar unidades del CRS", que es lo mismo que no decir nada. Ahora lo
    resuelve `_describe_crs` contra la base EPSG.
    """
    if srid is None or srid == 0:
        return "SRID desconocido (unidades indeterminadas)"

    clase, nombre, unidad = _describe_crs(srid)
    etiqueta = f"SRID {srid}" + (f" ({nombre})" if nombre else "")

    if clase == "desconocido":
        return f"SRID {srid} (no está en la base EPSG: unidades indeterminadas)"

    if clase == "geografico":
        return (
            f"{etiqueta} (geográfico, GRADOS — NO métrico: áreas/distancias "
            "directas salen en grados; para metros hay que reproyectar a un "
            "CRS proyectado en metros o usar ::geography)"
        )

    if unidad != "metre":
        return (
            f"{etiqueta} (proyectado pero en {unidad or 'unidad desconocida'}, "
            "NO metros: reproyecta antes de medir)"
        )

    apto, motivo = check_measurable_crs(srid)
    if not apto:
        return f"{etiqueta} (METROS — pero NO sirve para medir: {motivo})"

    return f"{etiqueta} (proyectado, METROS)"


def format_platform_capabilities(sandbox_available: bool,
                                 servicios_conectados: str | None = None) -> str:
    """Autoconocimiento (Fase 1 / F1.1): le dice al LLM qué puede y qué NO
    puede hacer ESTA plataforma EN DECISION-TIME, no al fallar.

    El gap original: las operaciones espaciales en memoria (buffer, área,
    centroide vía Python/GeoPandas) corren en un sandbox POSIX; en Windows
    ``SANDBOX_AVAILABLE`` es False y el agente lo descubría AL FALLAR. Ahora
    el router lo sabe antes de decidir y puede proponer la alternativa
    PostGIS (que no usa el sandbox) o avisar honestamente.

    Returns:
        Bloque para el prompt, o "" si todo está disponible (nada que avisar).
    """
    if sandbox_available and servicios_conectados is None:
        return ""
    lines: list[str] = ["CAPACIDADES DE LA PLATAFORMA (runtime):"]
    if servicios_conectados is not None:
        # F3: lo que hay enchufado lo dice el hub (servidores MCP del YAML), no
        # una lista fija: el intent `connected_service` solo tiene sentido si hay.
        lines.append(
            "  " + servicios_conectados.replace("\n", "\n  ") if servicios_conectados
            else "  - SERVICIOS CONECTADOS: ninguno. NO elijas `connected_service`."
        )
    if not sandbox_available:
        lines.append(
            "  - Operaciones espaciales EN MEMORIA (buffer/área/centroide/"
            "intersección vía Python/GeoPandas): NO DISPONIBLES en esta "
            "plataforma (el sandbox requiere POSIX/Linux).\n"
            "  - ALTERNATIVA: si los datos están en la BD interna (PostGIS), esas "
            "operaciones SÍ se pueden hacer con SQL (ST_Buffer, ST_Area, "
            "ST_Centroid, ST_Intersection) → usa `query_data`/`spatial_operation` "
            "sobre la BD, NO el sandbox.\n"
            "  - Para datos EXTERNOS/cargados que no están en PostGIS, NO ofrezcas "
            "la operación: explica al usuario que el cómputo geométrico no está "
            "disponible en este entorno (Windows) y que requiere Linux/Docker."
        )
    return "\n".join(lines)


def format_plan_steps(steps: list[dict], include_status: bool = False) -> str:
    """
    Formatear pasos de un plan para mostrar al usuario.

    Args:
        steps: Lista de pasos con 'description' y opcionalmente 'status'
        include_status: Si incluir el estado de cada paso

    Returns:
        String formateado con los pasos del plan
    """
    if not steps:
        return "Sin pasos definidos"

    lines = []
    for i, step in enumerate(steps, 1):
        desc = step.get("description", step.get("query_fragment", "Sin descripción"))
        if include_status:
            status = step.get("status", "pending")
            status_icon = {"completed": "✓", "failed": "✗", "pending": "○", "in_progress": "►"}.get(status, "?")
            lines.append(f"  {status_icon} Paso {i}: {desc}")
        else:
            lines.append(f"  Paso {i}: {desc}")

    return "\n".join(lines)


def format_error_for_user(error: str, include_suggestion: bool = True) -> str:
    """
    Formatear un error técnico para mostrar al usuario de forma amigable.

    Args:
        error: Mensaje de error técnico
        include_suggestion: Si incluir sugerencia genérica

    Returns:
        Mensaje de error formateado
    """
    # Mapeo de errores técnicos a mensajes amigables
    error_mappings = {
        "timeout": "La operación tardó demasiado tiempo. Intenta con menos datos.",
        "connection": "No se pudo conectar al servicio. Verifica tu conexión.",
        "permission": "No tienes permisos para realizar esta operación.",
        "not found": "No se encontró el recurso solicitado.",
        "invalid": "Los datos proporcionados no son válidos.",
    }

    error_lower = error.lower()
    for key, friendly_msg in error_mappings.items():
        if key in error_lower:
            return friendly_msg

    if include_suggestion:
        return f"Ocurrió un error: {error[:100]}. Intenta reformular tu consulta."

    return f"Error: {error[:100]}"


#: FH.7 — cómo citar el mapa en una respuesta. Es la descripción de una capacidad de la
#: interfaz (como la de una herramienta): el LLM decide si cita y qué; el frontend resuelve
#: la referencia contra el mapa real y, si no resuelve, muestra solo el texto.
INSTRUCCION_REFERENCIAS = (
    "ENLACES AL MAPA: cuando en tu respuesta nombres un elemento concreto (un lote, una vía…) "
    "o una capa que está (o queda tras este turno) en el mapa, escríbelo como enlace; el usuario "
    "ve «texto», al pasar el ratón se resalta y al hacer clic se selecciona y encuadra:\n"
    "  - capa: [[layer:<capa>|texto]]\n"
    "  - elemento por un valor que VISTE en los datos: [[layer:<capa>?<campo>=<valor>|texto]]\n"
    "  - elemento por su id, si viste ese id EN ESA capa: [[layer:<capa>#<id>|texto]] (si no, "
    "por un valor: ?<campo>=<valor> sirve en cualquier capa que tenga ese campo)\n"
    "  <capa> = el id de una capa del mapa (layer-…), un dataset ds_… o `activa` (la capa que "
    "deja este turno). Los ids de capa válidos son los de la lista de capas del mapa (uno que "
    "aparezca en mensajes anteriores puede ya no existir). No inventes valores ni ids: cita solo "
    "lo que viste.\n"
    "  Ejemplo (valores inventados): «El más grande es el lote [[layer:activa?codigo=A-17|A-17]], "
    "de 950 m², en [[layer:layer-3|Predios]].»"
)
