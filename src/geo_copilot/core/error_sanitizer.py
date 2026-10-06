"""Sanitización de errores para mensajes visibles al usuario.

Principio "agentic, no fallbacks": ante un fallo el agente declara un error
HONESTO, pero NUNCA filtra internals (traceback, rutas de archivo, nombres de
módulo) a la UI. El detalle completo va SOLO al log para depuración.

Motivado por el audit 2026-06-13: un ``ImportError`` crudo (con la ruta
absoluta del repo) llegó al usuario final en ``final_response``.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# Mensaje genérico honesto por defecto (sin internals).
_GENERIC = (
    "Ocurrió un error al procesar tu solicitud. "
    "Inténtalo de nuevo o reformula la consulta."
)


def sanitize_error(
    exc: BaseException,
    *,
    context: str = "",
    user_message: str | None = None,
) -> str:
    """Loguea el error completo y devuelve un mensaje SEGURO para el usuario.

    - El traceback/detalle completo se registra con ``logger.error(exc_info=True)``.
    - Al usuario se le devuelve un mensaje honesto SIN stack, rutas ni nombres
      de módulo. ``str(exc)`` NO se expone (puede contener rutas, p. ej. en
      ``ImportError``).
    - En modo ``settings.debug`` se anexa SOLO el TIPO de excepción (su nombre
      de clase, sin rutas) para facilitar el diagnóstico en desarrollo.

    Args:
        exc: la excepción capturada.
        context: etiqueta corta para el log (p. ej. ``"graph.process"``).
        user_message: mensaje base honesto (ya sin internals) que reemplaza al
            genérico; en debug se le anexa el tipo de excepción.
    """
    logger.error("[error] %s: %s", context or "unhandled", exc, exc_info=True)

    base = user_message or _GENERIC
    try:
        from geo_copilot.core.config import get_settings

        if getattr(get_settings(), "debug", False):
            # Solo el nombre de la clase — nunca str(exc) (puede traer rutas).
            return f"{base} [debug: {type(exc).__name__}]"
    except Exception:  # noqa: BLE001 — nunca dejar que el sanitizador rompa el flujo; sin log: ya estamos dentro del manejo de un error
        pass
    return base


# ---------------------------------------------------------------------------
# S0.4 (auditoría 2026-09-08 §3): de denylist a ALLOWLIST.
#
# Antes, un texto de error llegaba al cliente salvo que contuviera una de 13
# subcadenas prohibidas: todo lo que nadie había previsto pasaba. Ahora:
#
#  1. Los canales técnicos (auto-corrección, traza de razonamiento, reintentos
#     por WebSocket) NUNCA reenvían texto crudo: `describe_error` lo traduce a
#     un VOCABULARIO CERRADO de mensajes (`ERROR_MESSAGES`). Lo único que puede
#     llegar al cliente está escrito en este archivo.
#  2. El error final que narra el responder pasa tal cual SOLO si tiene forma
#     de frase natural (`_FRASE_SEGURA`) y ningún marcador de internals; si no,
#     se sustituye por su categoría. Por defecto se deniega.
# ---------------------------------------------------------------------------

ERROR_MESSAGES: dict[str, str] = {
    "table_not_found": "No encontré esa tabla en la base de datos: no existe o no está disponible.",
    "column_not_found": "La consulta pidió una columna que no existe en esa tabla.",
    "database": "La consulta a la base de datos falló.",
    "timeout": "La operación tardó demasiado y se canceló.",
    "sandbox": "El código de análisis no se pudo ejecutar de forma segura.",
    "url_not_allowed": "La dirección del servicio no está permitida.",
    "external_service": "Un servicio externo no respondió correctamente.",
    "llm": "El modelo de lenguaje no respondió correctamente.",
    "rejected": "La operación fue rechazada.",
    "generic": _GENERIC,
}

# Orden relevante: la primera categoría que coincide gana. Los patrones solo
# ELIGEN la categoría; el texto que se muestra sale siempre de ERROR_MESSAGES.
_CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("timeout", ("timed out", "timeout", "tiempo máximo", "tardó demasiado")),
    ("url_not_allowed", ("ssrf", "url not allowed", "url no permitida",
                         "domain not allowed", "dominio no permitido")),
    ("sandbox", ("sandbox", "security violation", "systemexit", "rlimit")),
    # Antes que "database": decirle al usuario QUÉ no existe (sin nombrarlo)
    # es la respuesta honesta a "¿cuántos X hay en la tabla X?".
    ("table_not_found", ("undefinedtable", "relation \"", "no disponible en el catálogo")),
    ("column_not_found", ("undefinedcolumn", "column \"")),
    ("database", ("asyncpg", "psycopg", "sqlstate", "postgres", "postgis",
                  "relation \"", "column \"", "syntax error", "does not exist",
                  "no existe", "undefinedcolumn", "undefinedtable", "sql")),
    ("llm", ("openai", "anthropic", "api_key", "credentials", "rate limit",
             "llm", "modelo de lenguaje")),
    ("rejected", ("rejected", "rechazad", "not approved", "no aprobad")),
    ("external_service", ("connection", "conexión", "http", "status code",
                          "arcgis", "socrata", "imagery", "unavailable")),
)


def error_category(text: object) -> str:
    """Categoría (clave de ``ERROR_MESSAGES``) de un error crudo."""
    low = str(text or "").lower()
    for category, patterns in _CATEGORY_PATTERNS:
        if any(p in low for p in patterns):
            return category
    return "generic"


def describe_error(text: object, *, context: str = "") -> str | None:
    """Mensaje SEGURO para un error crudo: siempre del vocabulario cerrado.

    El crudo va al log. Devuelve ``None`` si no hay error.
    """
    if text is None or not str(text).strip():
        return None
    logger.warning("[error-desc] %s: %s", context or "unhandled", text)
    return ERROR_MESSAGES[error_category(text)]


# Marcadores de internals: aunque la frase tenga forma segura, si aparece uno
# de estos se trata como crudo. Es una red adicional, no el control principal.
_INTERNALS_MARKERS = (
    "traceback (most recent call last)", 'file "', ".py:", "/app/",
    "psycopg", "asyncpg", 'relation "', 'column "', "does not exist",
    "syntax error at or near", "sqlstate", "detail:", "hint:",
    # Jerga del validador de SQL (S0.2): no es un secreto, pero tampoco una
    # respuesta; el usuario recibe la categoría ("No encontré esa tabla…").
    "rechazado por el validador", "estructura no admitida",
)

# Frase natural: letras (con tildes), dígitos, espacios y puntuación de prosa.
# Quedan fuera las comillas dobles, llaves, corchetes, <>, =, backticks, barras
# invertidas y saltos de línea: la forma del SQL, del JSON y de las trazas.
_FRASE_SEGURA = re.compile(r"[\wÁÉÍÓÚÜÑáéíóúüñ¿¡ .,;:!?()%'’«»/+-]{1,300}")


def is_user_safe(text: str) -> bool:
    """¿Se puede mostrar ``text`` tal cual? Allowlist de forma + marcadores."""
    if not _FRASE_SEGURA.fullmatch(text):
        return False
    low = text.lower()
    return not any(m in low for m in _INTERNALS_MARKERS)


def sanitize_error_message(message: object, *, context: str = "") -> str:
    """Sanea un MENSAJE de error ya-string antes de mostrarlo al cliente.

    A diferencia de :func:`sanitize_error` (que recibe la excepción), aquí el
    nodo ya convirtió el error a string y lo dejó en ``state['error']``.

    Pasa tal cual solo si :func:`is_user_safe`; si no, se devuelve el mensaje
    de su categoría (vocabulario cerrado). El crudo queda en el log; en modo
    debug se anexa para diagnóstico local.
    """
    text = str(message or "").strip()
    if not text:
        return ""
    logger.error("[error-msg] %s: %s", context or "unhandled", text)
    if is_user_safe(text):
        return text
    safe = ERROR_MESSAGES[error_category(text)]
    try:
        from geo_copilot.core.config import get_settings

        if getattr(get_settings(), "debug", False):
            return f"{safe} [debug: {text}]"
    except Exception:  # noqa: BLE001 — nunca dejar que el sanitizador rompa el flujo; sin log: ya estamos dentro del manejo de un error
        pass
    return safe
