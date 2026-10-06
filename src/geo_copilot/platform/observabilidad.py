"""Observabilidad (F7, S7.3): correlación en los logs, trazas OpenTelemetry, métricas Prometheus y
errores a Sentry. Todo se ENCIENDE por entorno; sin configurar, no hace nada ni cuesta nada:

- `OTEL_EXPORTER_OTLP_ENDPOINT` (p. ej. http://jaeger:4318): trazas. FastAPI, httpx (el cliente
  MCP: inyecta `traceparent`, así la traza cruza al servidor MCP) y asyncpg se instrumentan
  solos; el turno, cada herramienta y cada llamada al LLM son spans propios.
- `PROMETHEUS_MULTIPROC_DIR`: métricas de varios workers en `/metrics` (sin él, las del proceso).
- `SENTRY_DSN`: errores no capturados y los `logger.error`.

La correlación no necesita nada: cada petición lleva un id (el `X-Request-ID` del cliente o uno
nuevo) que aparece en TODAS las líneas de log de esa petición y en el aviso a los servicios MCP.
"""
from __future__ import annotations

import contextlib
import os
import re
import time
from collections.abc import Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Literal, get_args

from geo_copilot.core.logging import FiltroCorrelacion, get_logger
from geo_copilot.core.logging import correlacion_actual as _correlacion

if TYPE_CHECKING:
    from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint

logger = get_logger(__name__)

__all__ = ["FiltroCorrelacion", "correlacion", "fijar_correlacion"]


def fijar_correlacion(valor: str | None) -> Any:
    return _correlacion.set(valor)


def correlacion() -> str | None:
    return _correlacion.get()


# --------------------------------------------------------------------------- trazas
_tracer: Any = None


def tracer() -> Any:
    global _tracer
    if _tracer is None:
        from opentelemetry import trace

        _tracer = trace.get_tracer("geo_copilot")
    return _tracer


@contextlib.contextmanager
def span(nombre: str, **atributos: Any) -> Iterator[Any]:
    """Un span con atributos (no-op si OTel no está configurado)."""
    with tracer().start_as_current_span(nombre) as s:
        for k, v in atributos.items():
            if v is not None:
                s.set_attribute(k, v if isinstance(v, (str, bool, int, float)) else str(v))
        yield s


def iniciar(app: Any, *, servicio: str = "geo-copilot-app") -> None:
    """Enciende lo que el entorno pida. Idempotente por proceso. (La correlación en los logs la pone
    `core.logging.setup_logging` en su handler.)"""
    if os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        _iniciar_otel(app, servicio)
    if os.environ.get("SENTRY_DSN"):
        _iniciar_sentry()


def _iniciar_otel(app: Any, servicio: str) -> None:
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("[observabilidad] OTEL_EXPORTER_OTLP_ENDPOINT definido pero faltan los paquetes de OTel")
        return
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        proveedor = TracerProvider(resource=Resource.create({
            "service.name": os.environ.get("OTEL_SERVICE_NAME", servicio),
            "deployment.environment": os.environ.get("ENVIRONMENT", "production"),
        }))
        proveedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace.set_tracer_provider(proveedor)
    # F7 (auditoría): `exclude_spans` quita el span «send»/«receive» de CADA mensaje ASGI (miles en
    # un WebSocket de horas; requiere instrumentation-fastapi >= 0.49b0). La sesión sigue siendo UNA
    # traza: los turnos que lanza el handler heredan su contexto y sus spans (LLM, SQL, MCP) cuelgan
    # del span del WebSocket. Pendiente (#36): cada turno, traza raíz propia enlazada con un Link.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics",
                                       server_request_hook=_url_sin_secretos_en_span,
                                       exclude_spans=["send", "receive"])
    HTTPXClientInstrumentor().instrument()
    AsyncPGInstrumentor().instrument()
    logger.info(f"[observabilidad] trazas OTLP → {os.environ['OTEL_EXPORTER_OTLP_ENDPOINT']}")


#: Parámetros de la URL que llevan una credencial: la API key de servicio del WebSocket (`?token=`)
#: y el ticket de un solo uso del navegador (`?ticket=`).
_PARAMS_SECRETOS = re.compile(r"(?i)(^|[?&])(token|ticket)=[^&#]*")


def sin_secretos_en_url(texto: str) -> str:
    """La URL (o su query) con el valor de `token`/`ticket` tapado."""
    return _PARAMS_SECRETOS.sub(r"\1\2=[oculto]", texto)


def _url_sin_secretos_en_span(span: Any, scope: dict) -> None:
    """F7 (auditoría): el instrumentador de FastAPI guarda la URL CON su query en el span, y su
    `redact_url` solo conoce firmas de AWS/GCS: la API key de `?token=` llegaba en claro a Jaeger."""
    atributos = getattr(span, "attributes", None) or {}
    for clave in ("http.url", "url.full", "url.query"):
        valor = atributos.get(clave)
        if isinstance(valor, str):
            span.set_attribute(clave, sin_secretos_en_url(valor))


#: Migas que llevan datos de la consulta: el SQL que generó el LLM con los literales de la pregunta
#: (`query`, de las integraciones asyncpg/SQLAlchemy que Sentry enciende solas) y las órdenes a Redis.
_MIGAS_CON_DATOS = frozenset({"query", "redis"})
#: Lo que una petición HTTP saliente o un span añaden aparte de la URL: su query (una dirección a
#: geocodificar, un `where=` de ArcGIS) y los parámetros del SQL.
_DATOS_CON_CONSULTA = ("http.query", "http.fragment", "db.params")


def _miga_sentry_sin_datos(miga: Breadcrumb, _pista: BreadcrumbHint) -> Breadcrumb | None:
    """F7 (auditoría): las migas viajan con el siguiente error. Apagar los cuerpos y las variables
    locales no basta: el SQL de cada consulta llegaba como miga."""
    if miga.get("category") in _MIGAS_CON_DATOS:
        return None
    datos = miga.get("data")
    if isinstance(datos, dict):
        for clave in _DATOS_CON_CONSULTA:
            datos.pop(clave, None)
    return miga


def _evento_sentry_sin_secretos(evento: Event, _pista: Hint) -> Event | None:
    """F7 (auditoría): Sentry adjunta la query de la URL (también la del WebSocket) sin filtrarla, y
    con trazas activas cada span de BD lleva su SQL como descripción."""
    peticion = evento.get("request")
    if isinstance(peticion, dict):
        for clave in ("query_string", "url"):
            valor = peticion.get(clave)
            if isinstance(valor, str):
                peticion[clave] = sin_secretos_en_url(valor)
    tramos = evento.get("spans")
    for tramo in tramos if isinstance(tramos, list) else []:
        op = str(tramo.get("op") or "")
        if op.startswith(("db", "cache")) or "redis" in op:
            tramo["description"] = op
        datos = tramo.get("data")
        if isinstance(datos, dict):
            for clave in _DATOS_CON_CONSULTA:
                datos.pop(clave, None)
    return evento


def _iniciar_sentry() -> None:
    try:
        import logging

        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("[observabilidad] SENTRY_DSN definido pero sentry-sdk no está instalado")
        return
    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        environment=os.environ.get("ENVIRONMENT", "production"),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0")),
        # F7 (auditoría): `send_default_pii=False` solo quita cookies e IP. El cuerpo de la petición
        # (la consulta, el map_context con atributos de predios), las variables locales de cada frame
        # (SQL, resultados) y las migas del SQL se mandaban igual: se apagan aparte.
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        before_breadcrumb=_miga_sentry_sin_datos,
        before_send=_evento_sentry_sin_secretos,
        before_send_transaction=_evento_sentry_sin_secretos,
        # Los `logger.error` son eventos; como migas solo avisos y errores (los INFO llevan la consulta).
        integrations=[LoggingIntegration(level=logging.WARNING, event_level=logging.ERROR)],
    )
    logger.info("[observabilidad] errores → Sentry (sin cuerpos, variables locales ni SQL)")


# --------------------------------------------------------------------------- métricas
try:
    from prometheus_client import Counter, Histogram

    HERRAMIENTA_S = Histogram("geo_herramienta_segundos", "Duración de una herramienta del agente",
                              ["herramienta", "resultado"],
                              buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120))
    TURNO_S = Histogram("geo_turno_segundos", "Duración de un turno (consulta → respuesta) sin la espera humana",
                        ["resultado"], buckets=(1, 2.5, 5, 10, 20, 30, 60, 120, 300))
    LLM_S = Histogram("geo_llm_segundos", "Duración de una llamada al LLM", ["modelo"],
                      buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32, 64))
    LLM_TOKENS = Counter("geo_llm_tokens", "Tokens consumidos (coste)", ["modelo", "tipo"])
    # F7 (auditoría): `geo_llm_segundos` solo ve las llamadas que salen bien; una caída del proveedor
    # (clave revocada, cuota agotada, 429 sostenido) no dejaba ninguna muestra. Se expone como
    # `geo_llm_fallos_total{modelo, tipo}`; `geo_llm_reintentos_total` cuenta las esperas por 429.
    LLM_FALLOS = Counter("geo_llm_fallos", "Llamadas al LLM que fallaron", ["modelo", "tipo"])
    LLM_REINTENTOS = Counter("geo_llm_reintentos", "Reintentos tras un 429 del proveedor", ["modelo"])
    MCP_ERRORES = Counter("geo_mcp_errores", "Fallos al hablar con un servidor MCP", ["servidor", "tipo"])
    HITL = Counter("geo_hitl", "Decisiones de aprobación humana", ["estado"])
    # F7 (auditoría): lo que una aprobación esperó a la persona; `geo_turno_segundos` ya no lo cuenta.
    HITL_ESPERA_S = Histogram("geo_hitl_espera_segundos", "Espera de una decisión humana (HITL)",
                              buckets=(1, 5, 10, 30, 60, 120, 300, 600))
    HTTP_S = Histogram("geo_http_segundos", "Duración de las peticiones HTTP", ["metodo", "ruta", "codigo"],
                       buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300))
    _METRICAS = True
except ImportError:  # pragma: no cover — prometheus_client es dependencia de la imagen
    _METRICAS = False


@contextlib.contextmanager
def medir_herramienta(nombre: str) -> Iterator[dict[str, Any]]:
    """Span + histograma de una herramienta. El llamador pone `estado["ok"]`."""
    estado: dict[str, Any] = {"ok": False}
    inicio = time.perf_counter()
    with span(f"herramienta {nombre}", **{"geo.herramienta": nombre}) as s:
        try:
            yield estado
        finally:
            s.set_attribute("geo.resultado", "ok" if estado["ok"] else "fallo")
            if _METRICAS:
                HERRAMIENTA_S.labels(nombre, "ok" if estado["ok"] else "fallo").observe(time.perf_counter() - inicio)


def registrar_llm(modelo: str, segundos: float, uso: dict | None) -> None:
    if not _METRICAS:
        return
    LLM_S.labels(modelo).observe(segundos)
    for tipo, clave in (("entrada", "prompt_tokens"), ("salida", "completion_tokens")):
        n = (uso or {}).get(clave)
        if isinstance(n, int) and n > 0:
            LLM_TOKENS.labels(modelo, tipo).inc(n)


#: Los `tipo` de `geo_llm_fallos_total`. Fuente única: `llm_client.tipo_fallo_llm` devuelve este
#: tipo (mypy rechaza un literal que no esté aquí) y `preparar_series_llm` crea una serie por cada uno.
TipoFalloLLM = Literal["saturacion", "cuota_agotada", "auth", "error"]
TIPOS_FALLO_LLM: tuple[str, ...] = get_args(TipoFalloLLM)


def preparar_series_llm(modelo: str) -> None:
    """F7 (auditoría): las series de fallos y reintentos del modelo nacen a 0 al crear su cliente.
    Un contador que aparece ya en 1 no da `increase()`: sin esto, el primer fallo por clave revocada
    o cuota agotada no hacía saltar LlmSinCuotaOClave."""
    if _METRICAS:
        for tipo in TIPOS_FALLO_LLM:
            LLM_FALLOS.labels(modelo, tipo)
        LLM_REINTENTOS.labels(modelo)


def registrar_llm_fallo(modelo: str, tipo: TipoFalloLLM) -> None:
    """`tipo`: saturacion (429 más allá de la espera), cuota_agotada, auth (401/403) o error."""
    if _METRICAS:
        LLM_FALLOS.labels(modelo, tipo).inc()


def registrar_llm_reintento(modelo: str) -> None:
    if _METRICAS:
        LLM_REINTENTOS.labels(modelo).inc()


class EsperaHumana:
    """Lo que el turno lleva esperando a una persona (aprobaciones). Mutable: la tarea del turno y
    los nodos del grafo corren en COPIAS del contexto y lo que suman tiene que llegar al turno."""

    def __init__(self) -> None:
        self.segundos = 0.0
        self.aprobaciones = 0  # decisiones humanas que pidió el turno (aprobada, rechazada o caducada)


_espera_humana: ContextVar[EsperaHumana | None] = ContextVar("geo_espera_humana", default=None)


@contextlib.contextmanager
def contar_espera_humana() -> Iterator[EsperaHumana]:
    """Lo fija quien abre el turno, ANTES de crear su tarea; `registrar_espera_hitl` suma aquí."""
    acumulada = EsperaHumana()
    ficha = _espera_humana.set(acumulada)
    try:
        yield acumulada
    finally:
        _espera_humana.reset(ficha)


def registrar_espera_hitl(segundos: float) -> None:
    """F7 (auditoría): una aprobación esperó `segundos` a la persona: su histograma y, si hay turno
    abierto, su cuenta (para que la duración del turno mida solo lo que hace el sistema)."""
    acumulada = _espera_humana.get()
    if acumulada is not None:
        acumulada.segundos += segundos
        acumulada.aprobaciones += 1
    if _METRICAS:
        HITL_ESPERA_S.observe(segundos)


def espera_del_turno() -> EsperaHumana | None:
    """La cuenta de espera humana del turno abierto en este contexto (None fuera de un turno)."""
    return _espera_humana.get()


def registrar_turno(segundos: float, ok: bool) -> None:
    """`segundos` del sistema: quien llama ya descontó la espera humana (`contar_espera_humana`)."""
    if _METRICAS:
        TURNO_S.labels("ok" if ok else "fallo").observe(max(segundos, 0.0))


def registrar_mcp_error(servidor: str, tipo: str) -> None:
    if _METRICAS:
        MCP_ERRORES.labels(servidor, tipo).inc()


def registrar_hitl(estado: str) -> None:
    if _METRICAS:
        HITL.labels(estado).inc()


def registrar_http(metodo: str, ruta: str, codigo: int, segundos: float) -> None:
    if _METRICAS:
        HTTP_S.labels(metodo, ruta, str(codigo)).observe(segundos)


def exponer_metricas() -> tuple[bytes, str]:
    """El texto de `/metrics`: de TODOS los workers si hay PROMETHEUS_MULTIPROC_DIR."""
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        generate_latest,
        multiprocess,
    )

    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registro = CollectorRegistry()
        multiprocess.MultiProcessCollector(registro)
        return generate_latest(registro), CONTENT_TYPE_LATEST
    return generate_latest(), CONTENT_TYPE_LATEST
