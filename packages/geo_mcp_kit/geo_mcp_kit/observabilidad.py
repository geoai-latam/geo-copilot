"""Trazas de un servidor geo MCP (F7, S7.3): la traza del turno del agente SIGUE aquí.

El núcleo manda `traceparent` en cada llamada; con `OTEL_EXPORTER_OTLP_ENDPOINT` definido, el
servidor continúa esa traza (petición MCP → cada tool). Sin la variable o sin los paquetes de
OpenTelemetry, no hace nada.

La correlación no necesita nada: el `X-Request-ID` que manda el núcleo aparece al final de cada
línea de log de esa petición (también las del hilo de la tool), como `[request_id=…]`.
"""
from __future__ import annotations

import contextlib
import logging
import os
import re
from collections.abc import Iterator
from contextvars import ContextVar, Token
from typing import Any

logger = logging.getLogger("geo_mcp_kit")
_activo = False

# --------------------------------------------------------------------------- correlación
#: F7 (auditoría): el núcleo manda `X-Request-ID` a cada MCP, pero nadie lo leía: buscar en los
#: logs de un servidor el id de una consulta fallida no daba nada. Mismo criterio que la app: se
#: acepta solo si parece un id (no texto libre que acabe en los logs).
_ID_VALIDO = re.compile(r"[A-Za-z0-9._-]{8,64}")
_peticion: ContextVar[str | None] = ContextVar("geo_mcp_peticion", default=None)
_fabrica_instalada = False


def fijar_peticion(valor: str | None) -> Token[str | None]:
    """El id de la petición en curso (None si no viene o no parece un id)."""
    return _peticion.set(valor if valor and _ID_VALIDO.fullmatch(valor) else None)


def restaurar_peticion(ficha: Token[str | None]) -> None:
    _peticion.reset(ficha)


def peticion_actual() -> str | None:
    return _peticion.get()


def instalar_correlacion() -> None:
    """Cada registro de log lleva `request_id` y, si hay petición, `[request_id=…]` al final del
    mensaje. Por fábrica de registros y no por filtro: los servidores configuran el logging con
    `basicConfig` (sin el campo en su formato) y a veces después de construir la app. Idempotente."""
    global _fabrica_instalada
    if _fabrica_instalada:
        return
    anterior = logging.getLogRecordFactory()

    def fabrica(*args: Any, **kwargs: Any) -> logging.LogRecord:
        registro = anterior(*args, **kwargs)
        id_peticion = _peticion.get()
        registro.request_id = id_peticion or "-"
        if id_peticion:
            # el id ya validado no lleva `%`: no rompe el formateo con los args del mensaje
            registro.msg = f"{registro.msg} [request_id={id_peticion}]"
        return registro

    logging.setLogRecordFactory(fabrica)
    _fabrica_instalada = True


def instrumentar(app: Any, servicio: str) -> Any:
    """La app ASGI envuelta para continuar la traza entrante (o la misma app si no aplica)."""
    global _activo
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return app
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("OTEL_EXPORTER_OTLP_ENDPOINT definido pero faltan los paquetes de OpenTelemetry")
        return app
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        proveedor = TracerProvider(resource=Resource.create({"service.name": os.environ.get("OTEL_SERVICE_NAME", servicio)}))
        proveedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(proveedor)
    _activo = True
    return OpenTelemetryMiddleware(app, excluded_urls="health")


@contextlib.contextmanager
def span(nombre: str, **atributos: Any) -> Iterator[None]:
    if not _activo:
        yield
        return
    from opentelemetry import trace

    with trace.get_tracer("geo_mcp_kit").start_as_current_span(nombre) as s:
        for k, v in atributos.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))
        yield
