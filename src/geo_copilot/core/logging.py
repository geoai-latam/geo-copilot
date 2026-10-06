"""
Configuración de logging para el sistema GEO_COPILOT.
"""

import json
import logging
import sys
from collections.abc import MutableMapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, cast

#: F7 (S7.3): el id de la petición en curso (X-Request-ID). Lo fija el middleware HTTP y aparece en
#: CADA línea de log de esa petición, y viaja a los servidores MCP.
correlacion_actual: ContextVar[str | None] = ContextVar("correlacion", default=None)


def _trace_id() -> str | None:
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None
    except Exception:  # noqa: BLE001 — sin OTel instalado/configurado no hay trace
        return None


class FiltroCorrelacion(logging.Filter):
    """Añade `correlation_id` (y el `trace_id` de OpenTelemetry, si hay) a cada registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlacion_actual.get() or "-"
        record.trace_id = _trace_id() or "-"
        return True


class JSONFormatter(logging.Formatter):
    """Formatter para logs en formato JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "correlation_id": getattr(record, "correlation_id", "-"),
            "trace_id": getattr(record, "trace_id", "-"),
        }

        # Agregar campos extra si existen
        if hasattr(record, "extra_fields"):
            log_obj.update(record.extra_fields)

        # Agregar exception info si existe
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


class TextFormatter(logging.Formatter):
    """Formatter para logs en formato texto legible."""

    def __init__(self):
        super().__init__(
            fmt="%(asctime)s | %(levelname)-8s | %(correlation_id)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
            defaults={"correlation_id": "-"},
        )


def setup_logging(
    level: str = "INFO",
    format_type: str = "text",
    name: str = "geo_copilot"
) -> logging.Logger:
    """
    Configurar logging para la aplicación.

    Args:
        level: Nivel de logging (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        format_type: Formato de salida ('json' o 'text')
        name: Nombre del logger

    Returns:
        Logger configurado
    """
    # Forzar UTF-8 en stdout/stderr: en Windows el codec por defecto es
    # cp1252 y cualquier emoji/glifo no representable (p.ej. 🤖 en logs de
    # agentes) lanza UnicodeEncodeError. ``errors='replace'`` como red final.
    for _stream in (sys.stdout, sys.stderr):
        try:
            cast(Any, _stream).reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — streams sin reconfigure (ya envueltos, etc.)
            pass

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper()))

    # Evitar duplicación de handlers
    if logger.handlers:
        logger.handlers.clear()

    # Handler para stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, level.upper()))
    handler.addFilter(FiltroCorrelacion())

    # Seleccionar formatter
    if format_type == "json":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(TextFormatter())

    logger.addHandler(handler)

    return logger


def get_logger(name: str = "geo_copilot") -> logging.Logger:
    """
    Obtener un logger configurado.

    Args:
        name: Nombre del logger (usualmente __name__)

    Returns:
        Logger configurado
    """
    return logging.getLogger(name)


class LoggerAdapter(logging.LoggerAdapter):
    """Adapter para agregar contexto adicional a los logs."""

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> tuple[Any, MutableMapping[str, Any]]:
        extra = kwargs.get("extra", {})
        extra["extra_fields"] = self.extra
        kwargs["extra"] = extra
        return msg, kwargs


def get_context_logger(name: str, context: dict[str, Any]) -> LoggerAdapter:
    """
    Obtener un logger con contexto adicional.

    Args:
        name: Nombre del logger
        context: Diccionario con contexto a agregar a cada log

    Returns:
        LoggerAdapter con el contexto
    """
    logger = get_logger(name)
    return LoggerAdapter(logger, context)


# =============================================================================
# Helper para logging estructurado de agentes (reemplaza prints)
# =============================================================================

def log_agent_event(
    agent_name: str,
    event: str,
    details: dict[str, Any] | None = None,
    level: str = "info",
    console_output: bool | None = None
) -> None:
    """
    Log estructurado para eventos de agentes.

    Reemplaza prints decorativos con logging apropiado.
    Opcionalmente muestra en consola si debug_console_output está habilitado.

    Args:
        agent_name: Nombre del agente (ej: "RouterAgent", "GISAgent")
        event: Tipo de evento (ej: "query_received", "sql_generated")
        details: Diccionario con detalles adicionales
        level: Nivel de log ("debug", "info", "warning", "error")
        console_output: Forzar output a consola (None = usar config)

    Example:
        log_agent_event("GISAgent", "sql_executed", {"rows": 100, "time": 0.5})
    """
    from geo_copilot.core.config import get_settings

    logger = get_logger(f"geo_copilot.{agent_name}")
    log_func = getattr(logger, level.lower(), logger.info)

    # Crear mensaje estructurado
    message = f"[{agent_name}] {event}"
    extra = {
        "agent": agent_name,
        "event": event,
        **(details or {})
    }

    log_func(message, extra={"extra_fields": extra})

    # Output a consola si está habilitado
    settings = get_settings()
    should_print = console_output if console_output is not None else settings.debug_console_output

    if should_print:
        detail_str = ""
        if details:
            # Formatear detalles para consola
            items = [f"{k}={v}" for k, v in details.items() if v is not None]
            if items:
                detail_str = f" ({', '.join(items[:5])})"  # Max 5 items
        print(f"[{agent_name}] {event}{detail_str}")
