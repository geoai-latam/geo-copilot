"""F7 (S7.1 · E7.1/E7.2): estado fuera del proceso.

Con Redis (aquí `fakeredis`, mismo protocolo): lo que un worker publica lo recibe otro; una
aprobación pedida en un proceso se responde desde otro; una aprobación que ya nadie espera (el
proceso se reinició) reanuda el turno con ESE contenido pre-aprobado; «Detener» y los mensajes del
WebSocket cruzan procesos; y producción no arranca sin Redis.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import fakeredis
import pytest
import pytest_asyncio

from geo_copilot.platform.estado import aprobaciones, bus
from geo_copilot.security.hitl import HITLActionType, HITLManager, HITLStatus, content_digest


@pytest.fixture
def servidor():
    return fakeredis.FakeServer()


def _cliente(servidor):
    return fakeredis.aioredis.FakeRedis(server=servidor)


@pytest_asyncio.fixture(loop_scope="function")  # el lector del bus corre en el loop del test
async def redis_instalado(servidor):
    """Bus y almacén de Redis instalados como en producción; se restauran al terminar."""
    viejo_bus, viejo_alm = bus.bus(), aprobaciones.almacen()
    clientes = [_cliente(servidor), _cliente(servidor)]
    b = bus.BusEnRedis(clientes[0])
    await b.iniciar()
    bus.instalar(b)
    aprobaciones.instalar(aprobaciones.AprobacionesEnRedis(clientes[1]))
    yield b
    await b.cerrar()
    bus.instalar(viejo_bus)
    aprobaciones.instalar(viejo_alm)
    for c in clientes:  # sin cerrar, el cierre del loop de pytest-asyncio 1.4 se queda esperando (CI)
        await c.aclose()


async def _hasta(cond, segundos=3.0):
    fin = asyncio.get_event_loop().time() + segundos
    while not cond():
        if asyncio.get_event_loop().time() > fin:
            raise AssertionError("no ocurrió a tiempo")
        await asyncio.sleep(0.02)


# --------------------------------------------------------------------------- bus
@pytest.mark.asyncio
async def test_lo_que_publica_un_proceso_lo_recibe_otro(servidor):
    ca, cb = _cliente(servidor), _cliente(servidor)
    a, b = bus.BusEnRedis(ca), bus.BusEnRedis(cb)
    recibidos: list[tuple[str, dict]] = []

    async def en_b(canal, datos):
        recibidos.append((canal, datos))

    b.suscribir("ws:", en_b)
    await a.iniciar()
    await b.iniciar()
    try:
        await a.publicar("ws:sesion-1", {"type": "status", "data": {"x": 1}})
        await a.publicar("otro:canal", {"no": "llega"})
        await _hasta(lambda: recibidos)
        assert recibidos == [("ws:sesion-1", {"type": "status", "data": {"x": 1}})]
    finally:
        await a.cerrar()
        await b.cerrar()
        await ca.aclose()
        await cb.aclose()


# --------------------------------------------------------------------------- almacén
@pytest.mark.asyncio
async def test_el_almacen_de_redis_guarda_caduca_y_consume_una_vez(servidor):
    alm = aprobaciones.AprobacionesEnRedis(_cliente(servidor))
    await alm.guardar({"id": "a1", "session_id": "s", "created_at": "2026-09-28T10:00:00"}, ttl_s=60)
    await alm.guardar({"id": "a2", "session_id": "s", "created_at": "2026-09-28T10:01:00"}, ttl_s=60)
    assert [p["id"] for p in await alm.pendientes("s")] == ["a1", "a2"]
    assert not await alm.esperada("a1")
    await alm.renovar("a1")
    assert await alm.esperada("a1")
    assert await alm.resolver("a1")  # la reclama quien la borra…
    assert not await alm.resolver("a1")  # …y solo uno
    assert await alm.obtener("a1") is None and [p["id"] for p in await alm.pendientes("s")] == ["a2"]
    # la pre-aprobación es de un TURNO y de una solicitud concreta (tipo + ordinal), no de la sesión
    await alm.preaprobar("t1", {"tipo": "sql_execution", "ordinal": 1, "contenido": "SELECT 1", "huella": "h"},
                         ttl_s=60)
    assert await alm.tomar_preaprobacion("t1", "code_execution", 1) is None  # otro tipo: no es suya
    assert await alm.tomar_preaprobacion("t1", "sql_execution", 2) is None  # otra solicitud del turno
    assert await alm.tomar_preaprobacion("otro-turno", "sql_execution", 1) is None
    assert (await alm.tomar_preaprobacion("t1", "sql_execution", 1))["contenido"] == "SELECT 1"
    assert await alm.tomar_preaprobacion("t1", "sql_execution", 1) is None  # una sola vez
    await alm.guardar_turno("t1", "s", {"consulta": {"query": "q"}}, ttl_s=60)
    await alm.guardar_turno("t2", "s", {"consulta": {"query": "q2"}}, ttl_s=60)
    assert await alm.turnos_en_curso("s") == ["t1", "t2"]
    await alm.cerrar_turno("t2")  # cerrar uno no toca el otro turno de la sesión
    assert (await alm.tomar_turno("t1"))["consulta"]["query"] == "q"
    assert await alm.tomar_turno("t1") is None  # se toma UNA vez
    assert await alm.turnos_en_curso("s") == []


# --------------------------------------------------------------------------- HITL entre procesos
@pytest.mark.asyncio
async def test_la_aprobacion_pedida_en_un_worker_se_responde_desde_otro(redis_instalado):
    worker_a, worker_b = HITLManager(timeout=10), HITLManager(timeout=10)
    worker_a.conectar_bus()
    worker_b.conectar_bus()
    espera = asyncio.create_task(worker_a.request_approval(
        HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": "SELECT 1"}, session_id="s1"))
    await _hasta(lambda: worker_a.get_pending_requests())
    id_ = worker_a.get_pending_requests()[0].id

    # la petición de aprobar cae en el OTRO worker: la encuentra fuera de su memoria
    assert (await worker_b.buscar(id_)) is not None
    assert await worker_b.approve(id_)
    resp = await asyncio.wait_for(espera, 3)
    assert resp.status == HITLStatus.APPROVED
    assert await aprobaciones.almacen().obtener(id_) is None  # resuelta: fuera del almacén


@pytest.mark.asyncio
async def test_una_aprobacion_huerfana_reanuda_el_turno_con_ese_contenido_preaprobado(redis_instalado):
    """E7.1: el proceso que la pedía se reinició (su concesión caducó). Aprobarla reanuda el turno.
    F7 (auditoría): si el turno reanudado regenera el SQL distinto (V5: con alias `c."objectid"`), NO
    se sustituye por lo aprobado (el código no corrige al LLM, y hay consumidores que ignoran
    `modified_content`): se vuelve a preguntar, diciendo que antes se aprobó otra versión."""
    sql = "SELECT lotcodigo FROM catastro.lotes WHERE manzcodigo = '004503009'"
    huerfana = {"id": "h1", "session_id": "s1", "created_at": "2026-09-28T10:00:00", "action_type": "sql_execution",
                "title": "SQL", "description": "d", "details": {"sql": sql},
                "metadata": {"turno_id": "t1", "ordinal": 1},
                "aviso": {"approval_id": "h1", "content": sql, "content_sha256": content_digest(sql)}}
    await aprobaciones.almacen().guardar(huerfana, ttl_s=600)  # sin concesión: nadie la espera

    reanudadas: list[tuple[str, HITLStatus]] = []

    async def reanudar(solicitud, respuesta, pre):
        # como `estado.reanudar_turno`: reclamado el turno, se pre-aprueba lo aprobado para él
        reanudadas.append((solicitud.session_id, respuesta.status))
        await aprobaciones.almacen().preaprobar(solicitud.metadata["turno_id"], pre, 600)

    avisos: list[dict] = []

    async def notificar(_sesion, datos):
        avisos.append(datos)

    tras_reinicio = HITLManager(timeout=1, notification_callback=notificar)
    tras_reinicio.set_reanudador(reanudar)
    assert [a["approval_id"] for a in await tras_reinicio.pendientes_de("s1")] == ["h1"]  # se re-muestra
    assert await tras_reinicio.approve("h1")
    assert reanudadas == [("s1", HITLStatus.APPROVED)]

    # el turno reanudado (mismo id) regenera el SQL con otro texto → se le pregunta (aquí expira)
    regenerado = sql.replace("lotcodigo", 'l."lotcodigo"')
    token = aprobaciones.fijar_turno("t1")
    try:
        resp = await tras_reinicio.request_approval(
            HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": regenerado}, session_id="s1")
        assert resp.status == HITLStatus.EXPIRED and resp.modified_content is None
        assert avisos and avisos[0]["content"] == regenerado and avisos[0]["aprobado_antes"] == sql
        assert "aprobaste una versión distinta" in avisos[0]["description"]
        # …y la pre-aprobación se gastó: la siguiente solicitud igual vuelve a preguntar
        resp2 = await tras_reinicio.request_approval(
            HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": sql}, session_id="s1")
        assert resp2.status == HITLStatus.EXPIRED
    finally:
        aprobaciones.soltar_turno(token)


@pytest.mark.asyncio
async def test_si_se_regenera_igual_pasa_como_aprobada(redis_instalado):
    sql = "SELECT 1"
    await aprobaciones.almacen().guardar({"id": "h3", "session_id": "s3", "action_type": "sql_execution",
                                          "title": "t", "description": "d", "details": {"sql": sql},
                                          "metadata": {"turno_id": "t3", "ordinal": 1},
                                          "aviso": {"approval_id": "h3", "content": sql,
                                                    "content_sha256": content_digest(sql)}}, ttl_s=600)

    async def reanudar(solicitud, _r, pre):
        await aprobaciones.almacen().preaprobar(solicitud.metadata["turno_id"], pre, 600)

    m = HITLManager(timeout=1)
    m.set_reanudador(reanudar)
    assert await m.approve("h3")
    token = aprobaciones.fijar_turno("t3")
    try:
        resp = await asyncio.wait_for(m.request_approval(
            HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": sql}, session_id="s3"), 1)
    finally:
        aprobaciones.soltar_turno(token)
    assert resp.status == HITLStatus.APPROVED and resp.modified_content is None


@pytest.mark.asyncio
async def test_rechazar_una_huerfana_no_reanuda_nada_y_se_dice(redis_instalado):
    await aprobaciones.almacen().guardar({"id": "h2", "session_id": "s2", "action_type": "sql_execution",
                                          "title": "t", "description": "d", "details": {},
                                          "metadata": {"turno_id": "t2", "ordinal": 1},
                                          "aviso": {"approval_id": "h2", "content_sha256": "x"}}, ttl_s=600)
    llamadas = []

    async def reanudar(solicitud, respuesta, pre):
        llamadas.append((respuesta.status, pre))

    m = HITLManager(timeout=1)
    m.set_reanudador(reanudar)
    assert await m.reject("h2", "no")
    assert llamadas == [(HITLStatus.REJECTED, None)]  # el reanudador decide decirlo, sin pre-aprobar nada


# --------------------------------------------------------------------------- WebSocket y cancelar
@pytest.mark.asyncio
async def test_un_mensaje_para_un_socket_de_otro_worker_llega_por_el_bus(redis_instalado):
    from geo_copilot.api.websocket import ConnectionManager, WSMessage, WSMessageType

    enviados: list[dict] = []
    socket = SimpleNamespace(send_json=lambda carga: _anotar(enviados, carga))
    worker_a, worker_b = ConnectionManager(), ConnectionManager()
    worker_a.conectar_bus()
    worker_b.conectar_bus()
    worker_a.active_connections["s1"] = socket  # el socket está en A
    # la consulta corre en B: su aviso llega al socket de A
    assert await worker_b.send_message("s1", WSMessage(type=WSMessageType.STATUS, data={"status": "processing"}))
    await _hasta(lambda: enviados)
    assert enviados[0]["data"] == {"status": "processing"}


async def _anotar(lista, carga):
    lista.append(carga)


@pytest.mark.asyncio
async def test_detener_cancela_la_consulta_aunque_corra_en_otro_worker(redis_instalado):
    from geo_copilot.api.websocket import ConnectionManager

    worker_a, worker_b = ConnectionManager(), ConnectionManager()
    worker_a.conectar_bus()
    worker_b.conectar_bus()
    consulta = asyncio.create_task(asyncio.sleep(30))
    await worker_a.register_task("s1", consulta)
    assert await worker_b.cancelar_en_cualquier_proceso("s1")
    await _hasta(consulta.cancelled)


# --------------------------------------------------------------------------- consulta REST cancelable
@pytest.mark.asyncio
async def test_detener_para_una_consulta_hecha_por_rest(monkeypatch):
    """En la UI (que consulta por REST) «Detener» no paraba nada: solo se registraban las del WS."""
    from geo_copilot.api.models import QueryRequest
    from geo_copilot.api.routes import query as rutas
    from geo_copilot.api.websocket import connection_manager
    from geo_copilot.orchestrator.conversation import ConversationManager

    async def _nada(*_a, **_k):
        return None

    monkeypatch.setattr(rutas, "asegurar_sesion_actual", _nada)
    grafo = SimpleNamespace(process=lambda **_k: asyncio.sleep(30), llm=None)
    cm = ConversationManager()
    sesion = cm.create_session().session_id
    turno = asyncio.create_task(rutas.ejecutar_consulta(QueryRequest(query="trae los lotes", session_id=sesion),
                                                        grafo, cm))
    await _hasta(lambda: sesion in connection_manager.active_tasks)
    assert await connection_manager.cancelar_en_cualquier_proceso(sesion)
    resp = await asyncio.wait_for(turno, 3)
    assert resp.message == "Consulta detenida."
    assert await aprobaciones.almacen().turnos_en_curso(sesion) == []  # el turno no queda «en curso»


# --------------------------------------------------------------------------- producción sin Redis
@pytest.mark.asyncio
async def test_produccion_sin_redis_no_arranca():
    from geo_copilot.api import estado
    from geo_copilot.api.dependencies import _build_session_store

    prod = SimpleNamespace(environment="production", session_backend="redis",
                           redis_url="redis://127.0.0.1:1/0", session_timeout_minutes=60)
    with pytest.raises(estado.EstadoCompartidoNoDisponible):
        await estado.iniciar(prod)
    with pytest.raises(estado.EstadoCompartidoNoDisponible):
        _build_session_store(SimpleNamespace(**{**vars(prod), "session_backend": "memory"}))
    with pytest.raises(estado.EstadoCompartidoNoDisponible):
        _build_session_store(prod)  # redis pedido pero caído: antes caía a memoria en silencio

    dev = SimpleNamespace(environment="development", session_backend="memory", redis_url="redis://127.0.0.1:1/0")
    assert await estado.iniciar(dev) is None  # desarrollo: memoria del proceso, con aviso


@pytest.mark.asyncio
async def test_la_aprobacion_generada_en_otro_worker_llega_al_socket(redis_instalado, servidor, monkeypatch):
    """V5 con 2 workers: `send_approval_request` miraba solo el proceso local (is_connected) y NO
    enviaba si el socket estaba en el otro worker: la aprobación nunca se mostraba y expiraba."""
    from geo_copilot.api import websocket as ws

    recibidos: list[dict] = []

    class _Socket:
        async def accept(self):
            return None

        async def send_json(self, carga):
            recibidos.append(carga)

    worker_a, worker_b = ws.ConnectionManager(), ws.ConnectionManager()
    worker_a.conectar_bus(_cliente(servidor))
    worker_b.conectar_bus(_cliente(servidor))
    await worker_a.connect(_Socket(), "s1")  # el socket, en A
    assert await worker_b.is_connected("s1") and not await worker_b.esta_aqui("s1")

    monkeypatch.setattr(ws, "connection_manager", worker_b)  # la consulta (y su HITL) corre en B
    await ws.send_approval_request("s1", {"approval_id": "a1", "content": "SELECT 1"})
    await _hasta(lambda: recibidos)
    assert recibidos[0]["type"] == "approval_request" and recibidos[0]["data"]["approval_id"] == "a1"

    await worker_a.disconnect("s1")
    assert not await worker_b.is_connected("s1")  # la presencia se va con el socket
