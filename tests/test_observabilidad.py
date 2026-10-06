"""F7 (S7.3): observabilidad — correlación en los logs, métricas y trazas que cruzan al MCP."""
from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient


def test_cada_linea_de_log_de_una_peticion_lleva_su_id(caplog):
    from geo_copilot.platform import observabilidad

    registro = logging.LogRecord("geo_copilot.x", logging.INFO, __file__, 1, "hola", (), None)
    ficha = observabilidad.fijar_correlacion("abc123def456")
    try:
        assert observabilidad.FiltroCorrelacion().filter(registro)
    finally:
        observabilidad._correlacion.reset(ficha)
    assert registro.correlation_id == "abc123def456"
    fuera = logging.LogRecord("geo_copilot.x", logging.INFO, __file__, 1, "hola", (), None)
    observabilidad.FiltroCorrelacion().filter(fuera)
    assert fuera.correlation_id == "-"


def test_el_x_request_id_del_cliente_solo_si_parece_un_id():
    from geo_copilot.api.app import _ID_VALIDO

    assert _ID_VALIDO.fullmatch("0f9a8b7c6d5e4f30")
    assert not _ID_VALIDO.fullmatch("hola\ninyección en el log")
    assert not _ID_VALIDO.fullmatch("x" * 200)


def test_metrics_expone_herramientas_llm_y_http():
    from geo_copilot.api.app import create_app
    from geo_copilot.platform import observabilidad

    with observabilidad.medir_herramienta("query_database") as m:
        m["ok"] = True
    observabilidad.registrar_llm("gpt-4.1-mini", 1.2, {"prompt_tokens": 800, "completion_tokens": 40})
    observabilidad.registrar_mcp_error("imagery", "ConnectError")
    with TestClient(create_app()) as c:
        # F7 (auditoría): una ruta con parámetro ANTES de leer /metrics (la petición a /metrics se
        # registra después de generar su propio cuerpo). El código da igual (401 si hay API_KEY).
        c.get("/api/v1/session/sesion-de-prueba-7f3a")
        texto = c.get("/metrics").text
    assert 'geo_herramienta_segundos_count{herramienta="query_database",resultado="ok"}' in texto
    assert 'geo_llm_tokens_total{modelo="gpt-4.1-mini",tipo="entrada"}' in texto
    assert 'geo_mcp_errores_total{servidor="imagery",tipo="ConnectError"}' in texto
    # la ruta como plantilla, nunca la URL con el id (una serie por sesión)
    assert 'metodo="GET",ruta="/api/v1/session/{session_id}"}' in texto
    assert "sesion-de-prueba-7f3a" not in texto


@pytest.mark.asyncio
async def test_la_traza_del_turno_sigue_en_el_servidor_mcp(monkeypatch):
    """Con OTel activo, el traceparent que va al MCP es el del span EN CURSO (antes, uno al azar:
    la traza quedaba partida en dos)."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    from geo_copilot.platform.mcp.connection import traceparent

    proveedor = TracerProvider()
    with proveedor.get_tracer("t").start_as_current_span("herramienta imagery__ndvi") as s:
        tp = traceparent()
        esperado = format(s.get_span_context().trace_id, "032x")
    assert tp.split("-")[1] == esperado
    assert trace.get_current_span().get_span_context().is_valid is False  # fuera del span
    assert len(traceparent().split("-")[1]) == 32  # sin traza en curso: uno nuevo, válido


def test_el_kit_continua_la_traza_solo_si_se_pide(monkeypatch):
    from geo_mcp_kit import observabilidad as kit

    app = object()
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    assert kit.instrumentar(app, "imagery-mcp") is app  # sin endpoint: la misma app, sin coste


def test_un_429_del_proveedor_se_nombra_como_saturacion_no_como_culpa_del_usuario():
    """E7.5: con cuota agotada el router decía «reformula tu consulta»."""
    import httpx
    import openai

    from geo_copilot.core.llm_client import es_saturacion

    req = httpx.Request("POST", "https://x/chat")
    limite = openai.RateLimitError("Error code: 429", response=httpx.Response(429, request=req), body=None)
    assert es_saturacion(limite)
    try:
        try:
            raise limite
        except openai.RateLimitError as e:
            raise RuntimeError("el LLM falló") from e
    except RuntimeError as envuelta:
        assert es_saturacion(envuelta)
    otro = openai.APIStatusError("boom", response=httpx.Response(500, request=req), body=None)
    assert not es_saturacion(otro)
    assert not es_saturacion(ValueError("json roto"))


async def test_ante_un_429_de_cuota_se_espera_al_proveedor_hasta_el_presupuesto(monkeypatch):
    """E7.5: los reintentos cortos del SDK tumbaban el 20 % de las consultas con la cuota agotada."""
    import httpx
    import openai

    from geo_copilot.core import llm_client as m
    from geo_copilot.core.config import get_settings

    esperas: list[float] = []
    reloj = {"t": 1000.0}

    async def dormir(s):
        esperas.append(s)
        reloj["t"] += s  # el presupuesto se mide con el reloj: dormir lo hace avanzar

    # el reloj del cliente, no `asyncio.sleep` (global: lo usan los uvicorn de otros tests en sus hilos)
    monkeypatch.setattr(m, "_dormir", dormir)
    monkeypatch.setattr(m, "_ahora", lambda: reloj["t"])
    req = httpx.Request("POST", "https://x/chat")
    saturado = openai.RateLimitError(
        "Error code: 429", response=httpx.Response(429, headers={"retry-after": "3"}, request=req), body=None)
    llamadas = {"n": 0}

    async def llamar():
        llamadas["n"] += 1
        if llamadas["n"] < 3:
            raise saturado
        return "ok"

    assert await m.LLMClient._con_paciencia(llamar) == "ok"
    # lo que pidió el proveedor, con jitter solo hacia arriba (hasta +25 %) para no volver todos a la vez
    assert len(esperas) == 2 and all(3.0 <= e <= 3.75 for e in esperas)

    # agotado el presupuesto, el 429 sube tal cual (y el agente lo nombra como saturación)
    monkeypatch.setattr(get_settings(), "llm_espera_saturacion_s", 5.0)
    llamadas["n"] = -10
    with pytest.raises(openai.RateLimitError):
        await m.LLMClient._con_paciencia(llamar)

    async def roto():
        raise ValueError("no es cuota")

    with pytest.raises(ValueError):
        await m.LLMClient._con_paciencia(roto)


def test_la_ruta_de_la_metrica_lleva_el_prefijo_del_router_en_cualquier_version_de_fastapi():
    """FastAPI 0.142 deja en scope["route"].path solo el tramo del router: la serie salía como
    /session/{session_id} (CI) y en 0.136 como /api/v1/session/{session_id} (local)."""
    from fastapi import APIRouter, FastAPI

    from geo_copilot.api.app import plantilla_de_ruta

    r = APIRouter(prefix="/session")

    @r.get("/{session_id}")
    def una(session_id: str) -> dict:
        return {}

    @r.get("/{session_id}/capas/{capa_id}")
    def dos(session_id: str, capa_id: int) -> dict:
        return {}

    app = FastAPI()
    app.include_router(r, prefix="/api/v1")
    vistas: list[str] = []

    @app.middleware("http")
    async def mirar(request, call_next):  # como el middleware de métricas: después de enrutar
        respuesta = await call_next(request)
        vistas.append(plantilla_de_ruta(request.scope))
        return respuesta

    with TestClient(app) as c:
        assert c.get("/api/v1/session/abc-123").status_code == 200
        assert c.get("/api/v1/session/abc-123/capas/7").status_code == 200
    assert vistas == ["/api/v1/session/{session_id}", "/api/v1/session/{session_id}/capas/{capa_id}"]
    assert plantilla_de_ruta({"path": "/x"}) == "sin_ruta"


def test_la_telemetria_nativa_de_fastapi_no_duplica_ni_exporta_por_su_cuenta(monkeypatch):
    """E7.5: con FastAPI 0.142 y OTEL_EXPORTER_OTLP_ENDPOINT, FastAPI añadía un segundo exportador
    a nuestro TracerProvider y mandaba métricas/logs al colector (404 en bucle en los logs)."""
    import importlib.util

    from geo_copilot.api.app import TELEMETRIA_FASTAPI_APAGADA, create_app

    assert all(v is False for v in TELEMETRIA_FASTAPI_APAGADA.values())
    if importlib.util.find_spec("fastapi.telemetry") is None:
        pytest.skip("esta versión de FastAPI no trae telemetría propia (ignora el argumento)")
    from fastapi.telemetry import _runtime

    from geo_copilot.platform import observabilidad

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://colector-que-no-existe:4318")
    # solo interesa lo que haga FastAPI: nuestro exportador no se monta (dejaría un proveedor global vivo)
    monkeypatch.setattr(observabilidad, "_iniciar_otel", lambda *a, **k: None)
    antes = list(_runtime._configured)
    with TestClient(create_app()) as c:
        c.get("/health")
    assert _runtime._configured == antes  # FastAPI no registró exportadores propios
