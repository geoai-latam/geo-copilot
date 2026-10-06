"""F7 (auditoría): capa HTTP y métricas — clave del rate limit, etiquetas de geo_http_segundos,
5xx por excepción, reglas de alerta y la correlación/trazas del kit MCP."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, Depends, FastAPI, Header, Request
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parents[1]


def _muestra(nombre: str, etiquetas: dict[str, str]) -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(nombre, etiquetas) or 0.0


# --------------------------------------------------------------------------- rate limit (#6, #20)
def test_tras_el_proxy_cuenta_la_ip_que_puso_el_proxy_no_la_que_manda_el_cliente(monkeypatch):
    from geo_copilot.api.limiter import client_ip_key
    from geo_copilot.core.config import get_settings

    monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
    req = SimpleNamespace(headers={"X-Forwarded-For": "6.6.6.6, 203.0.113.7"}, client=SimpleNamespace(host="10.0.0.2"))
    assert client_ip_key(req) == "203.0.113.7"  # la primera la inventó el cliente


def _app_limitada(monkeypatch):
    """Una ruta autenticada (el principal sale de una cabecera de prueba) con 2 peticiones/minuto."""
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    from geo_copilot.api.limiter import clave_de_limite
    from geo_copilot.core.config import get_settings
    from geo_copilot.platform.identidad.principal import PRINCIPAL_DEV, Principal, fijar_principal

    monkeypatch.setattr(get_settings(), "trust_proxy_headers", True)
    limitador = Limiter(key_func=clave_de_limite, storage_uri="memory://")

    async def autenticar(x_usuario: str = Header(default="")) -> None:
        fijar_principal(PRINCIPAL_DEV if x_usuario == "dev" else
                        Principal(sub=x_usuario, org_id="acme", roles=frozenset({"analyst"})))

    router = APIRouter(dependencies=[Depends(autenticar)])

    @router.get("/consulta")
    @limitador.limit("2/minute")
    async def consulta(request: Request) -> dict:
        return {"ok": True}

    app = FastAPI()
    app.state.limiter = limitador
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(router)
    return TestClient(app)


def test_con_usuario_autenticado_el_limite_es_suyo_y_rotar_x_forwarded_for_no_da_cuota(monkeypatch):
    c = _app_limitada(monkeypatch)
    codigos = [c.get("/consulta", headers={"X-Usuario": "ana", "X-Forwarded-For": f"10.77.0.{i}"}).status_code
               for i in range(3)]
    assert codigos == [200, 200, 429]
    # otra persona desde la misma IP tiene su propio cupo (una oficina tras un NAT no comparte uno)
    assert c.get("/consulta", headers={"X-Usuario": "beto", "X-Forwarded-For": "10.77.0.0"}).status_code == 200


def test_sin_autenticacion_real_el_limite_es_por_la_ip_que_puso_el_proxy(monkeypatch):
    c = _app_limitada(monkeypatch)
    # principal `dev` (todos son el mismo): cuenta la IP, y la IP es la última entrada
    for _ in range(2):
        assert c.get("/consulta", headers={"X-Usuario": "dev", "X-Forwarded-For": "1.1.1.1, 9.9.9.9"}).status_code == 200
    assert c.get("/consulta", headers={"X-Usuario": "dev", "X-Forwarded-For": "2.2.2.2, 9.9.9.9"}).status_code == 429
    assert c.get("/consulta", headers={"X-Usuario": "dev", "X-Forwarded-For": "9.9.9.8"}).status_code == 200


# --------------------------------------------------------------------------- métrica HTTP (#7, #10, #9)
def _app_con_rutas_de_prueba():
    from geo_copilot.api.app import create_app

    app = create_app()

    @app.get("/sonda-f7")
    async def sonda() -> dict:
        return {"ok": True}

    @app.get("/explota-f7")
    async def explota() -> dict:
        raise RuntimeError("Redis no responde")

    return app


def test_un_metodo_inventado_no_crea_series_nuevas():
    c = TestClient(_app_con_rutas_de_prueba())
    antes = _muestra("geo_http_segundos_count", {"metodo": "OTRO", "ruta": "/sonda-f7", "codigo": "405"})
    for metodo in ("ZZQX1", "ZZQX2", "ZZQX3"):
        assert c.request(metodo, "/sonda-f7").status_code == 405
    from prometheus_client import REGISTRY

    metodos = {s.labels.get("metodo") for m in REGISTRY.collect() if m.name == "geo_http_segundos"
               for s in m.samples}
    assert not {"ZZQX1", "ZZQX2", "ZZQX3"} & metodos
    assert _muestra("geo_http_segundos_count", {"metodo": "OTRO", "ruta": "/sonda-f7", "codigo": "405"}) == antes + 3


def test_un_500_por_excepcion_no_capturada_cuenta_como_5xx():
    """El manejador global está POR FUERA del middleware: antes ese 500 no llegaba a la métrica y
    ErroresHttpAltos no saltaba justo cuando caía Redis o la BD."""
    c = TestClient(_app_con_rutas_de_prueba(), raise_server_exceptions=False)
    etiquetas = {"metodo": "GET", "ruta": "/explota-f7", "codigo": "500"}
    antes = _muestra("geo_http_segundos_count", etiquetas)
    assert c.get("/explota-f7").status_code == 500
    assert _muestra("geo_http_segundos_count", etiquetas) == antes + 1
    # y lo normal sigue midiéndose con su código
    ok = {"metodo": "GET", "ruta": "/sonda-f7", "codigo": "200"}
    antes_ok = _muestra("geo_http_segundos_count", ok)
    assert c.get("/sonda-f7").status_code == 200
    assert _muestra("geo_http_segundos_count", ok) == antes_ok + 1


def test_con_varios_workers_metrics_suma_los_de_todos(tmp_path):
    """PROMETHEUS_MULTIPROC_DIR (la ruta de producción): dos procesos escriben, un tercero expone."""
    entorno = {k: v for k, v in os.environ.items() if k != "OTEL_EXPORTER_OTLP_ENDPOINT"}
    entorno["PROMETHEUS_MULTIPROC_DIR"] = str(tmp_path)
    escribir = ("from geo_copilot.platform import observabilidad as o; "
                "o.registrar_http('GET', '/f7-multi', 200, 0.2)")
    for _ in range(2):
        subprocess.run([sys.executable, "-c", escribir], env=entorno, check=True, cwd=RAIZ, timeout=120)
    salida = subprocess.run(
        [sys.executable, "-c", "from geo_copilot.platform import observabilidad as o; "
                               "print(o.exponer_metricas()[0].decode())"],
        env=entorno, check=True, cwd=RAIZ, timeout=120, capture_output=True, text=True).stdout
    assert 'geo_http_segundos_count{codigo="200",metodo="GET",ruta="/f7-multi"} 2.0' in salida


# --------------------------------------------------------------------------- plazo del turno (#46)
def test_nginx_espera_a_la_api_mas_que_el_turno_mas_largo_con_aprobacion():
    """Si nginx corta antes que el `wait_for` del turno, el usuario lee el 504 de nginx aunque la
    app le iba a responder (mensaje de saturación incluido)."""
    import re

    from geo_copilot.api.routes.query import compute_process_timeout
    from geo_copilot.core.config import Settings

    campos = Settings.model_fields
    produccion = SimpleNamespace(total_execution_timeout=campos["total_execution_timeout"].default,
                                 hitl_enabled=True, hitl_timeout=campos["hitl_timeout"].default)
    conf = (RAIZ / "docker/nginx.frontend.conf").read_text(encoding="utf-8")
    api = conf[conf.index("location /api/"):]
    api = api[:api.index("}")]
    lectura = int(re.search(r"proxy_read_timeout\s+(\d+)s;", api).group(1))
    assert lectura > compute_process_timeout(produccion) + 30


# --------------------------------------------------------------------------- alertas (#33, #72, #35)
async def test_el_turno_observado_no_cuenta_la_espera_humana_de_una_aprobacion():
    """#33/#72: ConsultaLenta (p95 de `geo_turno_segundos`) no salta porque alguien tarde en aprobar;
    esa espera va a `geo_hitl_espera_segundos`. La aprobación se pide desde la TAREA del turno, como
    en producción (el grafo corre en una tarea creada dentro del turno, con el contexto copiado)."""
    import asyncio

    from geo_copilot.api.routes.query import turno_observado
    from geo_copilot.security.hitl import HITLActionType, HITLManager

    etiquetas = {"resultado": "ok"}
    antes = _muestra("geo_turno_segundos_count", etiquetas), _muestra("geo_turno_segundos_sum", etiquetas)
    espera_antes = _muestra("geo_hitl_espera_segundos_count", {}), _muestra("geo_hitl_espera_segundos_sum", {})
    async with turno_observado("sesion-f7-hitl", "rest") as obs:
        # el turno solo espera a una persona que no contesta (1 s); no computa nada
        await asyncio.ensure_future(
            HITLManager(timeout=1).request_approval(HITLActionType.SQL_EXECUTION, "SQL", "consulta"))
        obs["ok"] = True
    assert _muestra("geo_turno_segundos_count", etiquetas) == antes[0] + 1
    assert _muestra("geo_turno_segundos_sum", etiquetas) - antes[1] < 0.5
    assert _muestra("geo_hitl_espera_segundos_count", {}) == espera_antes[0] + 1
    assert _muestra("geo_hitl_espera_segundos_sum", {}) - espera_antes[1] >= 1.0


async def test_una_aprobacion_fuera_de_un_turno_solo_va_a_su_histograma():
    """Sin turno abierto (p. ej. una herramienta suelta) la espera no se pierde ni rompe nada."""
    from geo_copilot.platform import observabilidad

    antes = _muestra("geo_hitl_espera_segundos_count", {})
    observabilidad.registrar_espera_hitl(2.0)
    assert _muestra("geo_hitl_espera_segundos_count", {}) == antes + 1


def _promtool() -> str:
    """Con REQUIRE_PROMTOOL=1 (el CI lo fija) la falta de promtool es un fallo, no un skip: si alguien
    quita el paso que lo instala, las reglas dejarían de probarse sin que nadie lo viera."""
    ruta = os.environ.get("PROMTOOL") or shutil.which("promtool")
    if not ruta:
        motivo = "sin promtool (PATH o PROMTOOL=<ruta>): las reglas de alertas.yml NO se han probado"
        if os.environ.get("REQUIRE_PROMTOOL") == "1":
            pytest.fail(f"REQUIRE_PROMTOOL=1 y {motivo}")
        pytest.skip(motivo)
    return ruta


def test_sin_promtool_y_exigido_falla_en_vez_de_saltarse(monkeypatch):
    monkeypatch.delenv("PROMTOOL", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _nombre: None)
    monkeypatch.setenv("REQUIRE_PROMTOOL", "1")
    with pytest.raises(pytest.fail.Exception, match="REQUIRE_PROMTOOL"):
        _promtool()
    monkeypatch.delenv("REQUIRE_PROMTOOL")
    with pytest.raises(pytest.skip.Exception):
        _promtool()


def test_el_cliente_del_llm_crea_sus_series_de_fallos_y_reintentos_a_cero():
    """Las reglas (abajo) dan por hecho que las series empiezan en 0: aquí se prueba que es así. Una
    serie que aparece ya en 1 no da `increase()` y la primera clave revocada no avisaría."""
    from prometheus_client import REGISTRY

    from geo_copilot.core.llm_client import LLMClient
    from geo_copilot.platform.observabilidad import TIPOS_FALLO_LLM

    modelo = "modelo-series-a-cero-f7"
    LLMClient(provider="openai", model=modelo)
    for tipo in TIPOS_FALLO_LLM:
        assert REGISTRY.get_sample_value("geo_llm_fallos_total", {"modelo": modelo, "tipo": tipo}) == 0.0, tipo
    assert REGISTRY.get_sample_value("geo_llm_reintentos_total", {"modelo": modelo}) == 0.0


def test_cada_tipo_de_fallo_que_distingue_el_cliente_tiene_su_serie_preparada():
    """`tipo_fallo_llm` y `TIPOS_FALLO_LLM` comparten el Literal (mypy); esto lo prueba en ejecución:
    un fallo de cada clase cae en un tipo con serie, y no queda ningún tipo sin fallo que lo produzca."""
    import httpx
    import openai

    from geo_copilot.core.llm_client import tipo_fallo_llm
    from geo_copilot.platform.observabilidad import TIPOS_FALLO_LLM

    req = httpx.Request("POST", "https://x/chat")
    fallos = [
        openai.RateLimitError("429", response=httpx.Response(429, request=req), body=None),
        openai.RateLimitError("429", response=httpx.Response(429, request=req),
                              body={"type": "insufficient_quota", "code": "insufficient_quota"}),
        openai.AuthenticationError("401", response=httpx.Response(401, request=req), body=None),
        openai.PermissionDeniedError("403", response=httpx.Response(403, request=req), body=None),
        openai.InternalServerError("500", response=httpx.Response(500, request=req), body=None),
        ValueError("respuesta rota"),
    ]
    vistos = {tipo_fallo_llm(e) for e in fallos}
    assert vistos == set(TIPOS_FALLO_LLM)


# `stale` = la réplica sale del DNS de Docker (parada): Prometheus marca su serie obsoleta, no 0.
_BUCLE = " ".join((["1"] * 4 + ["stale"] * 4) * 10)
_PRUEBAS_DE_REGLAS = f"""
rule_files: [alertas.yml]
evaluation_interval: 15s
tests:
  - interval: 15s  # la única réplica no responde
    input_series:
      - {{ series: 'up{{job="geo-copilot-app", instance="a:8000"}}', values: '0x40' }}
    alert_rule_test:
      - {{ eval_time: 1m, alertname: AppCaida, exp_alerts: [] }}
      - {{ eval_time: 3m, alertname: AppCaida, exp_alerts: [{{ exp_labels: {{ severidad: critica }},
           exp_annotations: {{ resumen: "Ninguna réplica de la app responde a /metrics" }} }}] }}
  - interval: 15s  # el DNS no resuelve ninguna réplica: no hay ni serie `up`
    input_series:
      - {{ series: 'up{{job="otra-cosa", instance="x:1"}}', values: '1x40' }}
    alert_rule_test:
      - {{ eval_time: 3m, alertname: AppCaida, exp_alerts: [{{ exp_labels: {{ severidad: critica }},
           exp_annotations: {{ resumen: "Ninguna réplica de la app responde a /metrics" }} }}] }}
  - interval: 15s  # sano: dos réplicas estables
    input_series:
      - {{ series: 'up{{job="geo-copilot-app", instance="a:8000"}}', values: '1x80' }}
      - {{ series: 'up{{job="geo-copilot-app", instance="b:8000"}}', values: '1x80' }}
    alert_rule_test:
      - {{ eval_time: 15m, alertname: AppCaida, exp_alerts: [] }}
      - {{ eval_time: 15m, alertname: ReplicaCaida, exp_alerts: [] }}
      - {{ eval_time: 15m, alertname: ReplicasReiniciando, exp_alerts: [] }}
  - interval: 15s  # una réplica en bucle de reinicio: ReplicaCaida no la ve, ReplicasReiniciando sí
    input_series:
      - {{ series: 'up{{job="geo-copilot-app", instance="a:8000"}}', values: '1x80' }}
      - {{ series: 'up{{job="geo-copilot-app", instance="b:8000"}}', values: '{_BUCLE}' }}
    alert_rule_test:
      - {{ eval_time: 15m, alertname: ReplicaCaida, exp_alerts: [] }}
      - {{ eval_time: 15m, alertname: ReplicasReiniciando, exp_alerts: [{{ exp_labels: {{ severidad: aviso }},
           exp_annotations: {{ resumen: "Las réplicas de la app entran y salen (¿bucle de reinicio?): mirar `docker compose ps` y sus logs" }} }}] }}
  - interval: 15s  # un despliegue: las dos réplicas se recrean, una tras otra, con IP nueva
    input_series:
      - {{ series: 'up{{job="geo-copilot-app", instance="a:8000"}}', values: '1x20 stale' }}
      - {{ series: 'up{{job="geo-copilot-app", instance="b:8000"}}', values: '1x28 stale' }}
      - {{ series: 'up{{job="geo-copilot-app", instance="c:8000"}}', values: '_x22 1x60' }}
      - {{ series: 'up{{job="geo-copilot-app", instance="d:8000"}}', values: '_x30 1x60' }}
    alert_rule_test:
      - {{ eval_time: 15m, alertname: ReplicasReiniciando, exp_alerts: [] }}
      - {{ eval_time: 15m, alertname: AppCaida, exp_alerts: [] }}
  - interval: 15s  # una réplica viva en el DNS que no responde (colgada)
    input_series:
      - {{ series: 'up{{job="geo-copilot-app", instance="a:8000"}}', values: '1x40' }}
      - {{ series: 'up{{job="geo-copilot-app", instance="b:8000"}}', values: '0x40' }}
    alert_rule_test:
      - {{ eval_time: 4m, alertname: ReplicaCaida, exp_alerts: [] }}
      - {{ eval_time: 6m, alertname: ReplicaCaida, exp_alerts: [{{
           exp_labels: {{ severidad: aviso, job: geo-copilot-app, instance: "b:8000" }},
           exp_annotations: {{ resumen: "La réplica b:8000 no responde a /metrics" }} }}] }}
      - {{ eval_time: 6m, alertname: AppCaida, exp_alerts: [] }}
  # --- LLM (#34/#80): las series de fallos nacen a 0 al crear el cliente (preparar_series_llm)
  - interval: 1m  # sano: 6 llamadas buenas por minuto, ningún fallo ni reintento
    input_series:
      - {{ series: 'geo_llm_segundos_count{{modelo="m"}}', values: '0+6x20' }}
      - {{ series: 'geo_llm_fallos_total{{modelo="m", tipo="saturacion"}}', values: '0x20' }}
      - {{ series: 'geo_llm_fallos_total{{modelo="m", tipo="auth"}}', values: '0x20' }}
      - {{ series: 'geo_llm_reintentos_total{{modelo="m"}}', values: '0x20' }}
    alert_rule_test:
      - {{ eval_time: 15m, alertname: LlmFallando, exp_alerts: [] }}
      - {{ eval_time: 15m, alertname: LlmSinCuotaOClave, exp_alerts: [] }}
      - {{ eval_time: 15m, alertname: LlmSaturado, exp_alerts: [] }}
  - interval: 1m  # 1 de cada 3 llamadas falla por 429 agotado: más del 20 %
    input_series:
      - {{ series: 'geo_llm_segundos_count{{modelo="m"}}', values: '0+6x20' }}
      - {{ series: 'geo_llm_fallos_total{{modelo="m", tipo="saturacion"}}', values: '0+3x20' }}
    alert_rule_test:
      - {{ eval_time: 4m, alertname: LlmFallando, exp_alerts: [] }}
      - {{ eval_time: 12m, alertname: LlmFallando, exp_alerts: [{{ exp_labels: {{ severidad: aviso, modelo: m }},
           exp_annotations: {{ resumen: "Más del 20 % de las llamadas al LLM m fallan" }} }}] }}
      - {{ eval_time: 12m, alertname: LlmSinCuotaOClave, exp_alerts: [] }}
  - interval: 1m  # caída total desde el arranque: ni una serie de éxito del modelo
    input_series:
      - {{ series: 'geo_llm_fallos_total{{modelo="m", tipo="error"}}', values: '0+4x20' }}
    alert_rule_test:
      - {{ eval_time: 12m, alertname: LlmFallando, exp_alerts: [{{ exp_labels: {{ severidad: aviso, modelo: m }},
           exp_annotations: {{ resumen: "Más del 20 % de las llamadas al LLM m fallan" }} }}] }}
  - interval: 1m  # la clave revocada: el PRIMER fallo ya avisa, sin esperar
    input_series:
      - {{ series: 'geo_llm_segundos_count{{modelo="m"}}', values: '0+6x20' }}
      - {{ series: 'geo_llm_fallos_total{{modelo="m", tipo="auth"}}', values: '0x5 1x15' }}
      - {{ series: 'geo_llm_fallos_total{{modelo="m", tipo="saturacion"}}', values: '0x5 1x15' }}
    alert_rule_test:
      - {{ eval_time: 4m, alertname: LlmSinCuotaOClave, exp_alerts: [] }}
      - {{ eval_time: 7m, alertname: LlmSinCuotaOClave, exp_alerts: [{{
           exp_labels: {{ severidad: critica, modelo: m, tipo: auth }},
           exp_annotations: {{ resumen: "El proveedor rechaza el LLM m (auth): clave o cuota de la cuenta" }} }}] }}
  - interval: 1m  # 429 sostenido: 30 reintentos por minuto (0.5/s) durante más de 10 min
    input_series:
      - {{ series: 'geo_llm_reintentos_total{{modelo="m"}}', values: '0+30x30' }}
    alert_rule_test:
      - {{ eval_time: 8m, alertname: LlmSaturado, exp_alerts: [] }}
      - {{ eval_time: 20m, alertname: LlmSaturado, exp_alerts: [{{ exp_labels: {{ severidad: aviso, modelo: m }},
           exp_annotations: {{ resumen: "El proveedor limita al LLM m (429 sostenido): revisar la cuota por minuto" }} }}] }}
"""


def test_las_alertas_de_disponibilidad_y_del_llm_saltan_cuando_deben_y_callan_cuando_esta_sano(tmp_path):
    """Las reglas evaluadas por Prometheus (`promtool test rules`), no su texto."""
    promtool = _promtool()
    shutil.copy(RAIZ / "docker/observabilidad/alertas.yml", tmp_path / "alertas.yml")
    (tmp_path / "pruebas.yml").write_text(_PRUEBAS_DE_REGLAS, encoding="utf-8")
    r = subprocess.run([promtool, "test", "rules", "pruebas.yml"], cwd=tmp_path,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


# --------------------------------------------------------------------------- kit MCP (#37, #59)
def _keys():
    from geo_mcp_kit import KeyRing

    return KeyRing([{"name": "svc", "key": "k-svc", "scopes": ["t:use"]}], known_scopes={"t:use"},
                   tool_scopes={"t_tool": "t:use"})


def _app_que_corre_una_tool(runner, registro: list):
    """App ASGI mínima: ejecuta una «tool» en el ToolRunner (hilo del pool) y responde."""

    def mi_tool() -> dict:
        logging.getLogger("t_mcp").warning("dentro de la tool")
        return {"ok": True}

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while (await receive())["type"] != "lifespan.shutdown":
                await send({"type": "lifespan.startup.complete"})
            await send({"type": "lifespan.shutdown.complete"})
            return
        registro.append(runner.run(mi_tool))
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"ok"})

    return app


def test_el_servidor_mcp_escribe_el_request_id_del_nucleo_en_sus_logs(caplog, monkeypatch):
    from geo_mcp_kit import GeoMcpAuth, RateLimiter, ToolRunner

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    runner, hechas = ToolRunner(timeout_s=10, service="t"), []
    auth = GeoMcpAuth(_app_que_corre_una_tool(runner, hechas), _keys(), RateLimiter(), service="t")
    with caplog.at_level(logging.WARNING, logger="t_mcp"), TestClient(auth) as c:
        r = c.post("/mcp", json={}, headers={"Authorization": "Bearer k-svc", "X-Request-ID": "0f9a8b7c6d5e4f30"})
        assert r.status_code == 200 and hechas == [{"ok": True}]
        # un id que no parece un id (inyección en el log) no se escribe
        c.post("/mcp", json={}, headers={"Authorization": "Bearer k-svc", "X-Request-ID": "x\nFALSO"})
    primero, segundo = [r for r in caplog.records if r.name == "t_mcp"]
    assert primero.getMessage() == "dentro de la tool [request_id=0f9a8b7c6d5e4f30]"  # desde el hilo de la tool
    assert primero.request_id == "0f9a8b7c6d5e4f30"
    assert segundo.getMessage() == "dentro de la tool" and segundo.request_id == "-"
    # fuera de una petición, nada
    from geo_mcp_kit.observabilidad import peticion_actual

    assert peticion_actual() is None


def test_con_otel_la_tool_del_mcp_cuelga_de_la_traza_del_nucleo(monkeypatch):
    """Rama positiva: con OTEL_EXPORTER_OTLP_ENDPOINT, el servidor continúa el `traceparent` entrante
    y el span de la tool (en el hilo del pool) es de esa misma traza."""
    pytest.importorskip("opentelemetry.instrumentation.asgi")
    from geo_mcp_kit import GeoMcpAuth, RateLimiter, ToolRunner
    from geo_mcp_kit import observabilidad as kit
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.util._once import Once

    # proveedor propio y desechable: el global de OTel solo se fija una vez por proceso
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", None)
    monkeypatch.setattr(trace, "_TRACER_PROVIDER_SET_ONCE", Once())
    monkeypatch.setattr(kit, "_activo", False)
    exportador = InMemorySpanExporter()
    proveedor = TracerProvider()
    proveedor.add_span_processor(SimpleSpanProcessor(exportador))
    trace.set_tracer_provider(proveedor)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")  # no se usa: el proveedor ya es SDK

    runner, hechas = ToolRunner(timeout_s=10, service="t"), []
    auth = GeoMcpAuth(_app_que_corre_una_tool(runner, hechas), _keys(), RateLimiter(), service="t")
    traza = "4bf92f3577b34da6a3ce929d0e0e4736"
    with TestClient(auth) as c:
        r = c.post("/mcp", json={}, headers={"Authorization": "Bearer k-svc",
                                             "traceparent": f"00-{traza}-00f067aa0ba902b7-01"})
    assert r.status_code == 200 and hechas == [{"ok": True}]
    spans = exportador.get_finished_spans()
    tool = next(s for s in spans if s.name == "tool mi_tool")
    assert format(tool.context.trace_id, "032x") == traza
    assert tool.attributes["geo.servicio"] == "t"
    assert tool.parent is not None  # cuelga de la petición MCP, no es una raíz suelta
    assert any(s.context.span_id == tool.parent.span_id for s in spans)
