"""F7 (auditoría): espera ante un 429 (reloj, plazo del turno, jitter), cuota agotada, métricas de
fallos del LLM, Sentry/OTel sin datos sensibles y rangos degenerados de simbología."""
from __future__ import annotations

import asyncio

import httpx
import openai
import pytest
from fastapi import FastAPI, WebSocket

from geo_copilot.core import llm_client as m
from geo_copilot.core.config import get_settings

_REQ = httpx.Request("POST", "https://x/chat")


def _saturado(retry_after: str | None = "2") -> openai.RateLimitError:
    cabeceras = {"retry-after": retry_after} if retry_after else {}
    return openai.RateLimitError("Error code: 429", response=httpx.Response(429, headers=cabeceras, request=_REQ),
                                 body=None)


def _sin_saldo() -> openai.RateLimitError:
    return openai.RateLimitError(
        "Error code: 429 - You exceeded your current quota", response=httpx.Response(429, request=_REQ),
        body={"message": "You exceeded your current quota", "type": "insufficient_quota",
              "code": "insufficient_quota"})


class _Reloj:
    """Reloj falso: avanza con los sueños y con lo que «tarda» cada intento."""

    def __init__(self) -> None:
        self.t = 1000.0
        self.esperas: list[float] = []

    def ahora(self) -> float:
        return self.t

    async def dormir(self, s: float) -> None:
        self.esperas.append(s)
        self.t += s


@pytest.fixture
def reloj(monkeypatch):
    """F7 (auditoría): se parchea el reloj DEL CLIENTE (`_ahora`/`_dormir`), no `asyncio.sleep`. Ese
    es global del proceso: el uvicorn que otro test deja en un hilo (mcp_helpers) dormía con él,
    avanzaba este reloj y llenaba `esperas` — los fallos que solo salían en la suite completa."""
    r = _Reloj()
    monkeypatch.setattr(m, "_ahora", r.ahora)
    monkeypatch.setattr(m, "_dormir", r.dormir)
    monkeypatch.setattr(get_settings(), "llm_espera_saturacion_s", 60.0)
    return r


# --------------------------------------------------------------------------- #45 presupuesto con reloj
async def test_el_presupuesto_cuenta_el_tiempo_real_incluidos_los_reintentos_del_sdk(reloj):
    """Antes solo se sumaban los sueños propios: con los reintentos del SDK (≈4 s por intento) una
    llamada tardaba ~184 s y hacía ~93 peticiones frente a los 60 s anunciados."""
    intentos = {"n": 0}

    async def llamar():
        intentos["n"] += 1
        reloj.t += 4.0  # 3 peticiones y 2 esperas del SDK dentro de un intento
        raise _saturado("2")

    inicio = reloj.t
    with pytest.raises(openai.RateLimitError):
        await m.LLMClient._con_paciencia(llamar)
    assert reloj.t - inicio <= 60.0
    assert intentos["n"] <= 11  # antes 31


async def test_el_presupuesto_se_agota_aunque_el_reloj_no_avance(monkeypatch):
    """Con la espera simulada el reloj real casi no se mueve: lo dormido cuenta como mínimo,
    así el 429 sostenido sube al agotar el presupuesto en vez de reintentar sin fin."""
    esperas: list[float] = []

    async def dormir(s):
        esperas.append(s)

    monkeypatch.setattr(m, "_dormir", dormir)
    monkeypatch.setattr(get_settings(), "llm_espera_saturacion_s", 10.0)
    intentos = {"n": 0}

    async def llamar():
        intentos["n"] += 1
        if intentos["n"] > 50:
            return "no debía llegar aquí"
        raise _saturado("2")

    with pytest.raises(openai.RateLimitError):
        await m.LLMClient._con_paciencia(llamar)
    assert sum(esperas) <= 10.0 and intentos["n"] <= 6


# --------------------------------------------------------------------------- #46 plazo del turno
async def test_la_espera_no_pasa_del_plazo_del_turno(reloj):
    async def llamar():
        raise _saturado("2")

    inicio = reloj.t
    with m.plazo_turno(30):
        with pytest.raises(openai.RateLimitError):
            await m.LLMClient._con_paciencia(llamar)
    assert reloj.t - inicio <= 30 - m.MARGEN_PLAZO_S  # queda margen para responder MENSAJE_SATURADO


async def test_la_tarea_del_turno_hereda_el_plazo_fijado_al_crearla():
    async def plazo_visto():
        return m._plazo_turno.get()

    with m.plazo_turno(42):
        tarea = asyncio.ensure_future(plazo_visto())
    assert m._plazo_turno.get() is None  # fuera del bloque no queda nada fijado
    assert await tarea is not None


def test_un_429_sostenido_en_post_query_responde_saturacion_y_no_504(monkeypatch):
    """#46 por el camino real: POST /query crea la tarea del turno y la espera con su `wait_for`.
    El grafo solo enruta (router + cliente LLM reales; el proveedor da 429 sin fin). Sin el plazo
    del turno, el cliente seguiría reintentando hasta `llm_espera_saturacion_s` (60 s), el `wait_for`
    (6 s) cortaría antes y el usuario leería un 504 en vez del mensaje de saturación."""
    import time
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    from geo_copilot.agents.router_agent.agent import RouterAgent
    from geo_copilot.api import dependencies as deps
    from geo_copilot.api.app import create_app
    from geo_copilot.orchestrator.conversation import ConversationManager

    ajustes = get_settings()
    monkeypatch.setattr(ajustes, "total_execution_timeout", 6)
    monkeypatch.setattr(ajustes, "hitl_enabled", False)
    monkeypatch.setattr(ajustes, "llm_espera_saturacion_s", 60.0)
    cliente_llm = m.LLMClient(provider="openai", model="modelo-plazo-f7")
    cliente_llm._client = _ClienteQueFalla(_saturado("0.3"))

    class _Grafo:
        llm = MagicMock()

        async def process(self, query: str, **_kw):
            resp = await RouterAgent(llm_client=cliente_llm).process(query, context={})
            return {"success": resp.success, "message": resp.data["direct_response"]}

    app = create_app()
    conversaciones = ConversationManager()
    conversaciones.create_session("sesion-plazo-f7")
    app.dependency_overrides[deps.get_conversation_manager] = lambda: conversaciones
    app.dependency_overrides[deps.get_agent_graph] = lambda: _Grafo()
    inicio = time.monotonic()
    r = TestClient(app).post("/api/v1/query/", json={"query": "trae los lotes", "session_id": "sesion-plazo-f7"})
    assert r.status_code == 200, r.text
    assert m.MENSAJE_SATURADO in r.json()["message"]
    assert time.monotonic() - inicio < 6  # cerró ANTES del wait_for, con margen para responder


# --------------------------------------------------------------------------- #79 jitter con Retry-After
async def test_con_retry_after_tambien_hay_jitter_y_nunca_por_debajo_de_lo_pedido(reloj):
    intentos = {"n": 0}

    async def llamar():
        intentos["n"] += 1
        if intentos["n"] <= 12:
            raise _saturado("3")
        return "ok"

    assert await m.LLMClient._con_paciencia(llamar) == "ok"
    assert all(3.0 <= e <= 3.75 for e in reloj.esperas)
    assert len(set(reloj.esperas)) > 1  # no vuelven todas en el mismo instante


# --------------------------------------------------------------------------- #47 cuota agotada
async def test_la_cuota_agotada_no_es_saturacion_falla_ya_con_su_propio_mensaje(reloj):
    sin_saldo = _sin_saldo()
    assert m.es_cuota_agotada(sin_saldo)
    assert not m.es_saturacion(sin_saldo)
    assert m.es_saturacion(_saturado())
    assert m.mensaje_fallo_llm(sin_saldo) == m.MENSAJE_CUOTA_AGOTADA
    assert m.mensaje_fallo_llm(_saturado()) == m.MENSAJE_SATURADO
    assert m.mensaje_fallo_llm(ValueError("json roto")) is None
    assert "espera unos segundos" not in m.MENSAJE_CUOTA_AGOTADA.lower()

    try:  # también envuelta (raise … from)
        try:
            raise sin_saldo
        except openai.RateLimitError as e:
            raise RuntimeError("el LLM falló") from e
    except RuntimeError as envuelta:
        assert m.mensaje_fallo_llm(envuelta) == m.MENSAJE_CUOTA_AGOTADA

    async def llamar():
        raise sin_saldo

    with pytest.raises(openai.RateLimitError):
        await m.LLMClient._con_paciencia(llamar)
    assert reloj.esperas == []  # sin esperar: esperar no lo arregla


@pytest.mark.parametrize(("exc", "mensaje"), [(_sin_saldo(), m.MENSAJE_CUOTA_AGOTADA),
                                              (_saturado(), m.MENSAJE_SATURADO)])
async def test_el_router_dice_al_usuario_el_fallo_del_proveedor_que_es(exc, mensaje):
    from unittest.mock import AsyncMock, MagicMock

    from geo_copilot.agents.router_agent.agent import RouterAgent

    router = RouterAgent(llm_client=MagicMock())
    router.llm_client.chat = AsyncMock(side_effect=exc)
    resp = await router.process("trae los lotes", context={})
    assert resp.success is False
    assert mensaje in resp.data["direct_response"]
    assert "reformula" not in resp.data["direct_response"].lower()


# --------------------------------------------------------------------------- #34/#80 métricas de fallos
def _muestra(nombre: str, etiquetas: dict) -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(nombre, etiquetas) or 0.0


class _ClienteQueFalla:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    async def chat(self, *a, **k):
        raise self.exc


@pytest.mark.parametrize(("exc", "tipo"), [
    (openai.AuthenticationError("401", response=httpx.Response(401, request=_REQ), body=None), "auth"),
    (_sin_saldo(), "cuota_agotada"),
    (ValueError("respuesta rota"), "error"),
])
async def test_una_llamada_fallida_al_llm_se_cuenta_por_modelo_y_tipo(exc, tipo):
    cli = m.LLMClient(provider="openai", model="modelo-auditoria-f7")
    cli._client = _ClienteQueFalla(exc)
    antes = _muestra("geo_llm_fallos_total", {"modelo": "modelo-auditoria-f7", "tipo": tipo})
    with pytest.raises(type(exc)):
        await cli.chat([m.LLMMessage(role="user", content="hola")])
    assert _muestra("geo_llm_fallos_total", {"modelo": "modelo-auditoria-f7", "tipo": tipo}) == antes + 1


async def test_la_saturacion_agotada_se_cuenta_y_cada_reintento_tambien(reloj, monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_espera_saturacion_s", 5.0)
    cli = m.LLMClient(provider="openai", model="modelo-saturado-f7")
    cli._client = _ClienteQueFalla(_saturado("2"))
    etiquetas = {"modelo": "modelo-saturado-f7"}
    fallos = _muestra("geo_llm_fallos_total", {**etiquetas, "tipo": "saturacion"})
    reintentos = _muestra("geo_llm_reintentos_total", etiquetas)
    with pytest.raises(openai.RateLimitError):
        await cli.chat([m.LLMMessage(role="user", content="hola")])
    assert _muestra("geo_llm_fallos_total", {**etiquetas, "tipo": "saturacion"}) == fallos + 1
    assert _muestra("geo_llm_reintentos_total", etiquetas) == reintentos + len(reloj.esperas) > reintentos


# --------------------------------------------------------------------------- #29 Sentry
#: Corre en otro proceso: las integraciones de Sentry parchean FastAPI, httpx y asyncpg para siempre.
#: La marca se arma en ejecución para que no aparezca en el contexto de código de la traza.
_SONDA_SENTRY = """
import json, logging
import httpx
import sentry_sdk
from sentry_sdk.transport import Transport

capturado = []

class Captura(Transport):
    def capture_envelope(self, envelope):
        capturado.extend(i.payload.json for i in envelope.items if i.payload.json)

_init = sentry_sdk.init
sentry_sdk.init = lambda **kw: _init(transport=Captura(), **kw)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sentry_sdk.tracing_utils import record_sql_queries

from geo_copilot.platform import observabilidad

observabilidad._iniciar_sentry()
app = FastAPI()
log = logging.getLogger("geo_copilot.sonda")

@app.post("/q")
async def q(cuerpo: dict):
    consulta = cuerpo["query"]  # variable local con la marca
    # lo mismo que hacen las integraciones asyncpg/SQLAlchemy que Sentry enciende solas
    with record_sql_queries(None, "SELECT * FROM predios WHERE propietario='" + consulta + "'",
                            [consulta], "format", False):
        pass
    sentry_sdk.add_breadcrumb(category="redis", message="SET geo:turno " + consulta)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as c:
        c.get("http://geo.invalid/geocode?direccion=" + consulta)
    try:
        raise ValueError("fallo de prueba")
    except ValueError:
        log.error("el turno fallo", exc_info=True)
    return {"ok": True}

TestClient(app).post("/q?token=" + "SECRE" + "TO_URL", json={"query": "MARCA_" + "PRIVADA"})
sentry_sdk.flush()
print(json.dumps(capturado))
"""


def test_un_error_real_en_sentry_no_lleva_cuerpo_ni_locales_ni_sql_ni_el_token():
    """Antes solo se miraba la configuración: el SQL con los literales de la pregunta seguía yendo
    como miga (integraciones asyncpg/SQLAlchemy automáticas). Aquí se mira el evento que sale."""
    import json
    import os
    import subprocess
    import sys

    pytest.importorskip("sentry_sdk")
    entorno = {**os.environ, "SENTRY_DSN": "https://clave@sentry.invalid/1", "SENTRY_TRACES_SAMPLE_RATE": "1"}
    salida = subprocess.run([sys.executable, "-c", _SONDA_SENTRY], capture_output=True, text=True,
                            encoding="utf-8", env=entorno, timeout=120, check=True)
    eventos = json.loads(salida.stdout.strip().splitlines()[-1])
    error = next(e for e in eventos if e.get("logentry", {}).get("message") == "el turno fallo")
    transaccion = next(e for e in eventos if e.get("type") == "transaction")
    # la sonda sí produjo lo que se filtra: el error, su miga HTTP y el span de la consulta SQL
    assert any(m.get("category") == "httplib" for m in error["breadcrumbs"]["values"])
    assert any(str(t.get("op", "")).startswith("db") for t in transaccion["spans"])
    todo = json.dumps(eventos)
    assert "MARCA_PRIVADA" not in todo  # ni cuerpo, ni variables locales, ni SQL, ni Redis, ni query HTTP
    assert "SECRETO_URL" not in todo
    assert "vars" not in json.dumps(error["exception"])


def test_before_send_tapa_el_token_y_el_ticket_de_la_url():
    from geo_copilot.platform import observabilidad

    evento = {"request": {"url": "wss://app/ws/s1", "query_string": "token=SECRETO&ticket=T1&x=1"}}
    limpio = observabilidad._evento_sentry_sin_secretos(evento, {})
    assert "SECRETO" not in str(limpio) and "T1" not in str(limpio)
    assert "x=1" in limpio["request"]["query_string"]


# --------------------------------------------------------------------------- #31 OTel
def test_las_trazas_no_guardan_el_token_ni_el_ticket_de_la_url():
    pytest.importorskip("opentelemetry.instrumentation.fastapi")
    from fastapi.testclient import TestClient
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from geo_copilot.platform import observabilidad

    exportador = InMemorySpanExporter()
    proveedor = TracerProvider()
    proveedor.add_span_processor(SimpleSpanProcessor(exportador))
    app = FastAPI()

    @app.get("/x")
    def x():
        return {"ok": True}

    @app.websocket("/ws/{s}")
    async def ws(websocket: WebSocket, s: str):
        await websocket.accept()
        await websocket.close()

    FastAPIInstrumentor.instrument_app(app, tracer_provider=proveedor,
                                       server_request_hook=observabilidad._url_sin_secretos_en_span)
    try:
        cliente = TestClient(app)
        cliente.get("/x?token=SECRETO&a=1")
        with cliente.websocket_connect("/ws/s1?token=SECRETO&ticket=T1"):
            pass
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    urls = [s.attributes.get("http.url") for s in exportador.get_finished_spans() if s.attributes.get("http.url")]
    assert urls and all("SECRETO" not in u and "T1" not in u for u in urls)
    assert any("a=1" in u for u in urls)


def test_iniciar_otel_no_abre_un_span_por_mensaje_del_websocket_y_tapa_la_url(monkeypatch):
    """#36, de comportamiento: la app instrumentada por `_iniciar_otel` (no un mock de sus argumentos)
    con un WebSocket que intercambia varios mensajes deja UN span de la conexión, sin los «send» /
    «receive» de cada mensaje, y con `?token=` tapado. Sin `exclude_spans` salían 14 spans."""
    pytest.importorskip("opentelemetry.instrumentation.fastapi")
    from fastapi.testclient import TestClient
    from opentelemetry import trace
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from geo_copilot.platform import observabilidad

    exportador = InMemorySpanExporter()
    proveedor = TracerProvider()
    proveedor.add_span_processor(SimpleSpanProcessor(exportador))
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: proveedor)
    monkeypatch.setattr(HTTPXClientInstrumentor, "instrument", lambda self, **kw: None)
    monkeypatch.setattr(AsyncPGInstrumentor, "instrument", lambda self, **kw: None)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger.invalid:4318")
    app = FastAPI()

    @app.websocket("/ws/{s}")
    async def ws(websocket: WebSocket, s: str):
        await websocket.accept()
        while (texto := await websocket.receive_text()) != "fin":
            await websocket.send_text(f"eco:{texto}")
        await websocket.close()

    observabilidad._iniciar_otel(app, "prueba")
    try:
        with TestClient(app).websocket_connect("/ws/s1?token=SECRETO&ticket=T1") as conexion:
            for i in range(5):
                conexion.send_text(str(i))
                assert conexion.receive_text() == f"eco:{i}"
            conexion.send_text("fin")
    finally:
        FastAPIInstrumentor.uninstrument_app(app)
    spans = exportador.get_finished_spans()
    nombres = [s.name for s in spans]
    assert not [n for n in nombres if "send" in n or "receive" in n], nombres
    assert len(spans) == 1, nombres
    url = spans[0].attributes.get("http.url") or spans[0].attributes.get("url.full")
    assert url and "SECRETO" not in url and "T1" not in url and "token=[oculto]" in url


def test_iniciar_otel_instala_el_filtro_de_la_url(monkeypatch):
    pytest.importorskip("opentelemetry.instrumentation.fastapi")
    from opentelemetry import trace
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.trace import TracerProvider

    from geo_copilot.platform import observabilidad

    visto: dict = {}
    monkeypatch.setattr(trace, "get_tracer_provider", lambda: TracerProvider())
    monkeypatch.setattr(FastAPIInstrumentor, "instrument_app", staticmethod(lambda app, **kw: visto.update(kw)))
    monkeypatch.setattr(HTTPXClientInstrumentor, "instrument", lambda self, **kw: None)
    monkeypatch.setattr(AsyncPGInstrumentor, "instrument", lambda self, **kw: None)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://jaeger.invalid:4318")
    observabilidad._iniciar_otel(object(), "prueba")
    assert visto["server_request_hook"] is observabilidad._url_sin_secretos_en_span
    # #36: sin un span por cada mensaje ASGI (un WebSocket de horas era una traza sin tope)
    assert visto["exclude_spans"] == ["send", "receive"]


def test_los_minimos_declarados_traen_lo_que_el_codigo_usa():
    """`exclude_spans` llegó en instrumentation-fastapi 0.49b0: con la 0.48b0 (antes admitida)
    `_iniciar_otel` daba TypeError y la app no arrancaba al encender OTel. PyJWT se sube a 2.10.1
    aunque el emisor ya se pasa como str (ver test_f7_auditoria_oidc)."""
    import re
    from pathlib import Path

    from packaging.version import Version

    raiz = Path(__file__).resolve().parents[1]
    textos = {r: (raiz / r).read_text(encoding="utf-8")
              for r in ("pyproject.toml", "requirements.txt", "packages/geo_mcp_kit/pyproject.toml")}

    def minimo(texto: str, paquete: str) -> Version:
        m = re.search(rf"{re.escape(paquete)}(?:\[[^\]]*\])?>=([0-9][^,\"\s]*)", texto)
        assert m, paquete
        return Version(m.group(1))

    assert minimo(textos["pyproject.toml"], "opentelemetry-instrumentation-fastapi") >= Version("0.49b0")
    for ruta, texto in textos.items():
        assert minimo(texto, "pyjwt") >= Version("2.10.1"), ruta


def test_sin_secretos_en_url():
    from geo_copilot.platform.observabilidad import sin_secretos_en_url

    assert sin_secretos_en_url("ws://h/ws/s?token=K&a=1&ticket=T") == "ws://h/ws/s?token=[oculto]&a=1&ticket=[oculto]"
    assert sin_secretos_en_url("token=K") == "token=[oculto]"
    assert sin_secretos_en_url("/x?mytoken=K") == "/x?mytoken=K"  # solo los parámetros exactos


# --------------------------------------------------------------------------- #13 rangos degenerados
def _agente_simbologia():
    from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

    return SymbologyAgent.__new__(SymbologyAgent)


def test_un_rango_min_igual_max_es_igualdad_y_no_se_ensancha_sobre_el_hueco():
    """«rojo los lotes con 0 incidentes y verde los de 5 a 10»: 2 y 3 quedan en gris, no en
    «0 incidentes» (antes la clase [0,0] se ensanchaba a [0,5))."""
    spec = [{"min": 0, "max": 0, "color": "#e41a1c", "label": "0 incidentes"},
            {"min": 5, "max": 10, "color": "#4daf4a", "label": "5 a 10"}]
    clases = _agente_simbologia()._build_manual_breaks(spec, [0, 2, 3, 7, 10])
    assert [(c.label, c.min_value, c.max_value, c.count) for c in clases] == [
        ("0 incidentes", 0.0, 0.0, 1), ("5 a 10", 5.0, 10.0, 2)]


def test_clusters_de_un_solo_valor_atrapan_su_valor():
    """V5 F7: k-means de 4 lotes, «Cluster 0» = [0,0] y «Cluster 1» = [1,1]."""
    spec = [{"min": 0, "max": 0, "color": "#66c2a5", "label": "Cluster 0"},
            {"min": 1, "max": 1, "color": "#fc8d62", "label": "Cluster 1"}]
    clases = _agente_simbologia()._build_manual_breaks(spec, [0, 1, 1, 1])
    assert [(c.min_value, c.max_value, c.count) for c in clases] == [(0.0, 0.0, 1), (1.0, 1.0, 3)]


def test_menor_que_max_sin_datos_por_debajo_queda_vacia_no_igual_a_max():
    """Sin datos por debajo de max la clase queda VACÍA y sin `min` (que el render salta), no con
    un límite inventado ni como [5, 5] (igualdad, que atraparía el 5)."""
    spec = [{"min": 5, "max": None, "color": "#4daf4a", "label": "≥ 5"},
            {"min": None, "max": 5, "color": "#e41a1c", "label": "< 5"}]
    clases = _agente_simbologia()._build_manual_breaks(spec, [5, 6, 9])
    assert [(c.label, c.min_value, c.max_value, c.count) for c in clases] == [
        ("< 5", None, 5.0, 0), ("≥ 5", 5.0, 9.0, 3)]


def test_cada_valor_cuenta_en_una_sola_clase_la_primera_como_el_mapa():
    spec = [{"min": 0, "max": 0, "color": "#111111", "label": "cero"},
            {"min": 0, "max": 5, "color": "#222222", "label": "0 a 5"}]
    clases = _agente_simbologia()._build_manual_breaks(spec, [0, 1, 5])
    assert [c.count for c in clases] == [1, 2]
