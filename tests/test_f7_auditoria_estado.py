"""F7 (auditoría): turnos con id, aprobaciones que se reclaman una vez, presencia por conexión y un
bus que no se atasca.

Cada test reproduce un fallo encontrado en la auditoría de F7 y asevera el comportamiento correcto:
la pre-aprobación de un turno reanudado no la consume otra consulta; una aprobación se procesa UNA
vez aunque respondan dos réplicas; la respuesta del humano no se pierde con el pub/sub; el cierre
tardío de un socket viejo no borra la presencia del nuevo; un fallo de Redis al avisar no tumba un
turno; un cliente lento no retrasa las aprobaciones de los demás; el resultado de un turno cuya
petición HTTP ya no existe llega por el WebSocket.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

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


@pytest_asyncio.fixture(loop_scope="function")
async def redis_instalado(servidor):
    """Bus y almacén de Redis como en producción; se restauran al terminar."""
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
    for c in clientes:
        await c.aclose()


async def _hasta(cond, segundos=3.0):
    fin = asyncio.get_event_loop().time() + segundos
    while not cond():
        if asyncio.get_event_loop().time() > fin:
            raise AssertionError("no ocurrió a tiempo")
        await asyncio.sleep(0.02)


def _huerfana(id_: str, sesion: str, sql: str, *, turno_id: str | None, ordinal: int = 1) -> dict[str, Any]:
    return {"id": id_, "session_id": sesion, "created_at": "2026-10-03T10:00:00", "action_type": "sql_execution",
            "title": "SQL", "description": "d", "details": {"sql": sql},
            "metadata": {"turno_id": turno_id, "ordinal": ordinal} if turno_id else {},
            "aviso": {"approval_id": id_, "content": sql, "content_sha256": content_digest(sql)}}


class _Socket:
    def __init__(self) -> None:
        self.recibidos: list[dict[str, Any]] = []
        self.cerrado = False

    async def accept(self) -> None:
        return None

    async def send_json(self, carga: dict[str, Any]) -> None:
        if self.cerrado:
            raise ConnectionError("cerrado")
        self.recibidos.append(carga)

    async def close(self, code: int = 1000) -> None:
        self.cerrado = True


@pytest.fixture
def canal_ws(monkeypatch):
    """Un ConnectionManager propio como el global, con el socket de la sesión `s1` en él."""
    from geo_copilot.api import websocket as ws

    cm, socket = ws.ConnectionManager(), _Socket()
    cm.active_connections["s1"] = socket
    monkeypatch.setattr(ws, "connection_manager", cm)
    return socket


def _estados(socket: _Socket, status: str) -> list[dict[str, Any]]:
    return [m["data"] for m in socket.recibidos if m["type"] == "status" and m["data"].get("status") == status]


def _resultados(socket: _Socket) -> list[dict[str, Any]]:
    return [m["data"] for m in socket.recibidos if m["type"] == "result" and m["data"].get("entrega") == "ws"]


# --------------------------------------------------------------------------- #2 · #75 · #22 · #53
@pytest.mark.asyncio
async def test_sin_turno_que_retomar_no_queda_preaprobacion_para_otra_consulta(redis_instalado, canal_ws):
    """#2: aprobar una huérfana sin turno registrado (la consulta entró por WS, o el registro caducó)
    dejaba el SQL aprobado vivo para la sesión: la siguiente pregunta lo ejecutaba como «modificado»."""
    from geo_copilot.api import estado

    await aprobaciones.almacen().guardar(
        _huerfana("h1", "s1", "DELETE FROM lotes WHERE municipio = 'X'", turno_id="t-perdido"), ttl_s=600)
    m = HITLManager(timeout=0.3)
    m.set_reanudador(estado.reanudar_turno)
    assert await m.approve("h1")
    aviso = _estados(canal_ws, "reanudacion")
    assert aviso and aviso[0]["reanudada"] is False and aviso[0]["approval_id"] == "h1"

    # otra consulta de la sesión pide aprobar OTRO SQL: se pregunta (aquí expira), nunca el DELETE
    token = aprobaciones.fijar_turno("t-nuevo")
    try:
        resp = await m.request_approval(HITLActionType.SQL_EXECUTION, "SQL", "d",
                                        details={"sql": "SELECT count(*) FROM rios"}, session_id="s1")
    finally:
        aprobaciones.soltar_turno(token)
    assert resp.status == HITLStatus.EXPIRED and resp.modified_content is None


@pytest.mark.asyncio
async def test_la_preaprobacion_solo_vale_para_esa_solicitud_del_turno(redis_instalado):
    """#2 (turno con dos SQL): se aprobó el SEGUNDO; el turno reanudado arranca de cero y su PRIMER
    SQL no se lo lleva — se pregunta. El segundo sí recibe lo aprobado. Y otro turno, nada."""
    await aprobaciones.almacen().preaprobar("t1", {"tipo": "sql_execution", "ordinal": 2, "contenido": "SQL2",
                                                   "huella": content_digest("SQL2")}, 600)
    m = HITLManager(timeout=0.3)

    async def pedir(turno: str, sql: str):
        token = aprobaciones.fijar_turno(turno)
        try:
            return [await m.request_approval(HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": s},
                                             session_id="s1") for s in sql]
        finally:
            aprobaciones.soltar_turno(token)

    otro = await pedir("t-otro", ["SQL2"])
    assert otro[0].status == HITLStatus.EXPIRED  # otro turno de la misma sesión: no es suya
    primero, segundo = await pedir("t1", ["SQL1-regenerado", "SQL2"])
    assert primero.status == HITLStatus.EXPIRED  # el 1.º se pregunta, no ejecuta SQL2
    assert segundo.status == HITLStatus.APPROVED  # el 2.º, lo aprobado


@pytest.mark.asyncio
async def test_una_preaprobacion_de_otra_llamada_mcp_no_se_ejecuta_sin_preguntar(redis_instalado):
    """#2: se aprobó la herramienta MCP A(X); el turno reanudado elige B(Y). Antes su 1.ª llamada
    EXTERNAL_API se llevaba la pre-aprobación como MODIFIED y el hub (que ignora
    `modified_content`) ejecutaba B(Y) sin que nadie la viera. Ahora se pregunta."""
    await aprobaciones.almacen().preaprobar("t1", {"tipo": "external_api", "ordinal": 1, "estado": "approved",
                                                   "contenido": "A(X)", "huella": content_digest("A(X)")}, 600)
    avisos: list[dict[str, Any]] = []

    async def notificar(_sesion, datos):
        avisos.append(datos)

    m = HITLManager(timeout=0.3, notification_callback=notificar)
    token = aprobaciones.fijar_turno("t1")
    try:
        resp = await m.request_approval(HITLActionType.EXTERNAL_API, "MCP", "d", preview="B(Y)", session_id="s1")
    finally:
        aprobaciones.soltar_turno(token)
    assert resp.status == HITLStatus.EXPIRED and resp.modified_content is None  # ni APPROVED ni MODIFIED
    assert len(avisos) == 1 and avisos[0]["content"] == "B(Y)" and avisos[0]["aprobado_antes"] == "A(X)"


@pytest.mark.asyncio
async def test_la_huerfana_modificada_vale_si_el_turno_vuelve_a_pedir_lo_que_se_vio(redis_instalado):
    """#2: el humano MODIFICÓ el SQL de la huérfana. El turno reanudado vuelve a pedir el SQL que él
    vio: recibe su respuesta tal cual (modificada, con lo que él escribió); la huella es la de lo
    mostrado, no la del texto modificado (que el LLM nunca regenera)."""
    visto = "SELECT * FROM lotes"
    await aprobaciones.almacen().guardar(_huerfana("h7", "s7", visto, turno_id="t7"), ttl_s=600)

    async def reanudar(solicitud, _r, pre):
        await aprobaciones.almacen().preaprobar(solicitud.metadata["turno_id"], pre, 600)

    m = HITLManager(timeout=1)
    m.set_reanudador(reanudar)
    assert await m.modify("h7", "SELECT * FROM lotes LIMIT 10")
    token = aprobaciones.fijar_turno("t7")
    try:
        resp = await asyncio.wait_for(m.request_approval(
            HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": visto}, session_id="s7"), 1)
    finally:
        aprobaciones.soltar_turno(token)
    assert resp.status == HITLStatus.MODIFIED and resp.modified_content == "SELECT * FROM lotes LIMIT 10"


@pytest.mark.asyncio
async def test_la_reanudacion_real_corre_el_turno_de_esa_aprobacion_como_su_usuario(
        redis_instalado, canal_ws, monkeypatch):
    """#53 · #75 · #38: `reanudar_turno` → `_correr` → `ejecutar_consulta` de verdad, con el turno tal
    como lo GUARDA una ejecución real de `ejecutar_consulta` (si query.py renombra una clave, esto
    falla). Se retoma el turno que pidió la aprobación (no otro de la sesión), como su usuario, con
    su id, una vez, sin volver a apuntar la consulta en el historial; la tarea vive en `_REANUDADOS`
    mientras corre; el turno tiene correlación y métrica; el cliente recibe aviso y resultado."""
    import json

    from geo_copilot.api import dependencies, estado
    from geo_copilot.api.models import QueryRequest
    from geo_copilot.api.routes import query as rutas
    from geo_copilot.orchestrator.conversation import ConversationManager
    from geo_copilot.platform import observabilidad
    from geo_copilot.platform.identidad.principal import (
        Principal,
        fijar_principal,
        principal_actual,
        restaurar_principal,
    )

    async def _nada(*_a, **_k):
        return None

    monkeypatch.setattr(rutas, "asegurar_sesion_actual", _nada)
    medidas: list[bool] = []
    monkeypatch.setattr(observabilidad, "registrar_turno", lambda _s, ok: medidas.append(ok))
    cm = ConversationManager()
    sesion = cm.create_session().session_id
    canal_ws.recibidos.clear()
    from geo_copilot.api import websocket as ws

    ws.connection_manager.active_connections[sesion] = canal_ws
    guardados: dict[str, dict[str, Any]] = {}
    corridas: list[dict[str, Any]] = []

    async def procesar(**k):
        tid = aprobaciones.turno_actual().id
        if not guardados.get(tid):  # 1.ª vez: el turno tal como lo registró ejecutar_consulta
            crudo = await aprobaciones.almacen()._r.get(f"geo:hitl:turno:{tid}")
            guardados[tid] = json.loads(crudo)
            return {"success": True, "message": ""}
        quien = principal_actual()
        corridas.append({"query": k["query"], "sub": quien.sub if quien else None, "turno": tid,
                         "en_reanudados": bool(estado._REANUDADOS),
                         "correlacion": observabilidad.correlacion()})
        return {"success": True, "message": ""}

    grafo = SimpleNamespace(process=procesar, llm=None)
    monkeypatch.setattr(dependencies, "get_app_state",
                        lambda: SimpleNamespace(agent_graph=grafo, conversation_manager=cm))
    token = fijar_principal(Principal(sub="ana", org_id="o1", roles=frozenset({"analista"}), nombre="Ana"))
    try:
        for tid, q in (("t-A", "trae los lotes de X"), ("t-B", "cuenta los ríos")):
            await rutas.ejecutar_consulta(QueryRequest(query=q, session_id=sesion), grafo, cm, turno_id=tid)
    finally:
        restaurar_principal(token)
    # el proceso «murió» con los dos turnos esperando aprobación: siguen registrados tal cual
    for tid in ("t-A", "t-B"):
        await aprobaciones.almacen().guardar_turno(tid, sesion, guardados[tid], ttl_s=600)
    await aprobaciones.almacen().guardar(_huerfana("hA", sesion, "SELECT 1", turno_id="t-A"), ttl_s=600)
    medidas.clear()

    m = HITLManager(timeout=1)
    m.set_reanudador(estado.reanudar_turno)
    assert await m.approve("hA")
    await _hasta(lambda: _resultados(canal_ws))
    await _hasta(lambda: not estado._REANUDADOS)

    assert [{k: c[k] for k in ("query", "sub", "turno")} for c in corridas] == [
        {"query": "trae los lotes de X", "sub": "ana", "turno": "t-A"}]
    assert corridas[0]["en_reanudados"], "la tarea reanudada no se guardó mientras corría"
    assert corridas[0]["correlacion"], "el turno reanudado corre sin id de correlación"
    assert medidas == [True]  # su métrica de turno, con el resultado bueno
    aviso = _estados(canal_ws, "reanudacion")[0]
    assert aviso["reanudada"] is True and aviso["turno_id"] == "t-A" and aviso["consulta"] == "trae los lotes de X"
    resultado = _resultados(canal_ws)[0]
    assert resultado["turno_id"] == "t-A" and resultado["reanudada"] is True and resultado["success"] is True
    assert resultado["respuesta"]["session_id"] == sesion
    assert await aprobaciones.almacen().turnos_en_curso(sesion) == ["t-B"]  # el otro turno sigue intacto
    # reanudada=True: la consulta ya estaba en el historial; no se apunta dos veces
    historial = [mm.content for mm in cm.get_session(sesion).get_last_messages(20) if mm.role.value == "user"]
    assert historial.count("trae los lotes de X") == 1


@pytest.mark.asyncio
async def test_dos_respuestas_a_la_misma_huerfana_reanudan_una_sola_vez(redis_instalado, monkeypatch):
    """#22: doble clic o dos réplicas — antes, dos `_correr` del mismo turno (doble coste y doble
    resultado). Las dos respuestas pasan `buscar` y `esperada` ANTES de que ninguna resuelva (la
    lectura de la concesión cede el bucle): solo quien reclama la solicitud sigue; la otra, False."""
    await aprobaciones.almacen().guardar(_huerfana("h2", "s2", "SELECT 2", turno_id="t2"), ttl_s=600)
    alm = aprobaciones.almacen()
    original = alm.esperada
    vistas: list[str] = []

    async def esperada_lenta(id_):
        vistas.append(id_)
        await asyncio.sleep(0.05)  # las dos llegan aquí antes de que ninguna reclame
        return await original(id_)

    monkeypatch.setattr(alm, "esperada", esperada_lenta)
    reanudadas: list[str] = []

    async def reanudar(solicitud, _respuesta, _pre):
        reanudadas.append(solicitud.id)

    a, b = HITLManager(timeout=1), HITLManager(timeout=1)
    a.set_reanudador(reanudar)
    b.set_reanudador(reanudar)
    resultados = await asyncio.gather(a.approve("h2"), b.approve("h2"))
    assert vistas == ["h2", "h2"]  # de verdad se intercalaron
    assert sorted(resultados) == [False, True]
    assert reanudadas == ["h2"]


@pytest.mark.asyncio
async def test_concesion_caducada_con_el_proceso_vivo_ejecuta_una_sola_vez(redis_instalado):
    """#22 (camino 2): A sigue esperando pero su concesión caducó (pausa larga, Redis lento); la
    respuesta llega a B, que la trata como huérfana y reanuda el turno con lo aprobado. A no la
    ejecuta: al despertar ya no puede reclamarla (antes la ejecutaban los dos)."""
    reanudadas: list[tuple[str, dict | None]] = []

    async def reanudar(solicitud, _respuesta, pre):
        reanudadas.append((solicitud.id, pre))

    a, b = HITLManager(timeout=1), HITLManager(timeout=1)
    b.set_reanudador(reanudar)
    token = aprobaciones.fijar_turno("t9")
    try:
        espera = asyncio.create_task(a.request_approval(
            HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": "UPDATE lotes SET x = 1"}, session_id="s9"))
    finally:
        aprobaciones.soltar_turno(token)
    await _hasta(lambda: a.get_pending_requests())
    id_ = a.get_pending_requests()[0].id
    await aprobaciones.almacen().soltar(id_)  # la concesión de A caduca

    assert await b.approve(id_)
    resp_a = await asyncio.wait_for(espera, 3)
    assert resp_a.status == HITLStatus.EXPIRED  # A no ejecuta nada
    assert len(reanudadas) == 1 and reanudadas[0][0] == id_
    assert reanudadas[0][1]["contenido"] == "UPDATE lotes SET x = 1"  # se ejecuta UNA vez, en el reanudado


# --------------------------------------------------------------------------- #23 · #54 · #24
@pytest.mark.asyncio
async def test_si_el_aviso_del_bus_se_pierde_el_que_espera_lee_la_respuesta_guardada(
        redis_instalado, monkeypatch):
    """#23: la respuesta solo viajaba por pub/sub; perdida (failover, corte), el turno expiraba
    aunque el humano había aprobado. Aquí el que espera NO escucha el bus: la lee del almacén."""
    monkeypatch.setattr(aprobaciones, "RENOVAR_S", 0.1)
    espera_en_a, responde_b = HITLManager(timeout=5), HITLManager(timeout=5)  # A sin `conectar_bus`
    tarea = asyncio.create_task(espera_en_a.request_approval(
        HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": "SELECT 1"}, session_id="s1"))
    await _hasta(lambda: espera_en_a.get_pending_requests())
    id_ = espera_en_a.get_pending_requests()[0].id
    assert await responde_b.approve(id_)  # espera el acuse: A la recoge en su siguiente ciclo
    resp = await asyncio.wait_for(tarea, 2)
    assert resp.status == HITLStatus.APPROVED


@pytest.mark.asyncio
async def test_si_expira_con_la_respuesta_ya_guardada_la_aplica(redis_instalado):
    """#23: el aviso del bus se perdió y A llegó a su límite antes del siguiente sondeo; B ya había
    guardado la respuesta. Antes A expiraba (EXPIRED) y B decía «Approved successfully»."""
    a, b = HITLManager(timeout=0.6), HITLManager(timeout=5)  # A sin bus y sin sondeo (RENOVAR_S=5 s)
    tarea = asyncio.create_task(a.request_approval(
        HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": "SELECT 1"}, session_id="s1"))
    await _hasta(lambda: a.get_pending_requests())
    id_ = a.get_pending_requests()[0].id
    respondio = asyncio.create_task(b.approve(id_))
    resp = await asyncio.wait_for(tarea, 3)
    assert resp.status == HITLStatus.APPROVED
    assert await asyncio.wait_for(respondio, 3) is True


@pytest.mark.asyncio
async def test_si_el_turno_termina_sin_recoger_la_respuesta_no_se_dice_aprobada(redis_instalado):
    """#23: B guardó la respuesta, pero el turno que la esperaba en A terminó sin aplicarla
    («Detener»). Antes B veía desaparecer la solicitud y respondía 200 «Approved successfully»."""
    a, b = HITLManager(timeout=5), HITLManager(timeout=5)  # A sin bus: no se entera por el aviso
    tarea = asyncio.create_task(a.request_approval(
        HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": "SELECT 1"}, session_id="s1"))
    await _hasta(lambda: a.get_pending_requests())
    id_ = a.get_pending_requests()[0].id
    respondio = asyncio.create_task(b.approve(id_))
    for _ in range(100):
        if await aprobaciones.almacen().respuesta(id_) is not None:
            break
        await asyncio.sleep(0.01)
    tarea.cancel()  # «Detener»
    with pytest.raises(asyncio.CancelledError):
        await tarea
    assert await asyncio.wait_for(respondio, 3) is False
    assert await aprobaciones.almacen().respuesta(id_) is None  # no queda nada colgando


@pytest.mark.asyncio
async def test_dos_respuestas_desde_dos_replicas_solo_cuenta_una(redis_instalado):
    """#22/#23: con el que espera vivo, un doble clic en dos réplicas devolvía True a las dos y la
    última respuesta publicada pisaba a la primera. Ahora solo la primera se guarda y se aplica."""
    a, b, c = HITLManager(timeout=5), HITLManager(timeout=5), HITLManager(timeout=5)
    a.conectar_bus()
    tarea = asyncio.create_task(a.request_approval(
        HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": "SELECT 1"}, session_id="s1"))
    await _hasta(lambda: a.get_pending_requests())
    id_ = a.get_pending_requests()[0].id
    resultados = await asyncio.gather(b.approve(id_), c.reject(id_, "no"))
    assert sorted(resultados) == [False, True]
    resp = await asyncio.wait_for(tarea, 3)
    assert resp.status == (HITLStatus.APPROVED if resultados[0] else HITLStatus.REJECTED)


@pytest.mark.asyncio
async def test_aprobar_mientras_la_concesion_del_muerto_sigue_viva_no_se_pierde(redis_instalado, monkeypatch):
    """#54: el worker que esperaba murió hace <15 s (su concesión sigue viva). Antes: se publicaba
    para nadie y la API decía «Approved successfully» sin hacer nada. Ahora, sin acuse y con la
    concesión caducada, se trata como huérfana y se reanuda."""
    monkeypatch.setattr(aprobaciones, "CONCESION_S", 1)
    monkeypatch.setattr(aprobaciones, "RENOVAR_S", 0.2)
    await aprobaciones.almacen().guardar(_huerfana("h5", "s5", "SELECT 5", turno_id="t5"), ttl_s=600)
    await aprobaciones.almacen().renovar("h5")  # concesión viva de un proceso que ya no existe
    reanudadas: list[tuple[str, dict | None]] = []

    async def reanudar(solicitud, _respuesta, pre):
        reanudadas.append((solicitud.id, pre))

    m = HITLManager(timeout=1)
    m.conectar_bus()
    m.set_reanudador(reanudar)
    assert await asyncio.wait_for(m.approve("h5"), 5)
    assert reanudadas and reanudadas[0][0] == "h5" and reanudadas[0][1]["contenido"] == "SELECT 5"


@pytest.mark.asyncio
async def test_cancelar_mientras_se_notifica_no_deja_la_concesion_renovandose(redis_instalado, monkeypatch):
    """#24: «Detener» justo mientras se notificaba la solicitud: el renovador seguía poniendo la
    concesión para siempre y la solicitud parecía esperada por alguien."""
    monkeypatch.setattr(aprobaciones, "RENOVAR_S", 0.05)
    nunca = asyncio.Event()

    async def notificar_colgado(_sesion, _datos):
        await nunca.wait()

    m = HITLManager(timeout=5, notification_callback=notificar_colgado)
    tarea = asyncio.create_task(m.request_approval(
        HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": "SELECT 1"}, session_id="s1"))
    await _hasta(lambda: m.get_pending_requests())
    id_ = m.get_pending_requests()[0].id
    await asyncio.sleep(0.1)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea
    await asyncio.sleep(0.2)  # varios ciclos del renovador, si siguiera vivo
    assert not await aprobaciones.almacen().esperada(id_)
    assert await aprobaciones.almacen().obtener(id_) is None and not m.get_pending_requests()


# --------------------------------------------------------------------------- #3 · #5 · #15 · #27
@pytest.mark.asyncio
async def test_cerrar_el_socket_viejo_no_borra_la_presencia_del_nuevo(redis_instalado, servidor):
    """#3/#5/#15: la pestaña se reconecta en B mientras A conserva el socket medio muerto; cuando A lo
    cierra, antes borraba la presencia de la sesión y el aviso de aprobación no se enviaba."""
    from geo_copilot.api import websocket as ws

    a, b, c = ws.ConnectionManager(), ws.ConnectionManager(), ws.ConnectionManager()
    for w in (a, b, c):
        w.conectar_bus(_cliente(servidor))
    viejo, nuevo = _Socket(), _Socket()
    await a.connect(viejo, "s1")
    await b.connect(nuevo, "s1")
    await a.disconnect("s1", viejo)
    assert await c.is_connected("s1")  # la de B sigue
    await b.disconnect("s1", nuevo)
    assert not await c.is_connected("s1")


@pytest.mark.asyncio
async def test_el_cierre_tardio_del_socket_viejo_no_quita_al_nuevo_del_mismo_proceso():
    from geo_copilot.api import websocket as ws

    cm, viejo, nuevo = ws.ConnectionManager(), _Socket(), _Socket()
    cm.active_connections["s1"] = viejo
    await cm.disconnect("s1", viejo)
    await cm.connect(nuevo, "s1")
    await cm.disconnect("s1", viejo)  # el finally tardío del endpoint viejo
    assert cm.active_connections.get("s1") is nuevo


@pytest.mark.asyncio
async def test_dos_handshakes_de_la_misma_sesion_no_dejan_un_renovador_huerfano(redis_instalado, servidor):
    """#27: el segundo connect pisaba el renovador del primero, que seguía vivo para siempre."""
    from geo_copilot.api import websocket as ws

    cm = ws.ConnectionManager()
    cm.conectar_bus(_cliente(servidor))
    primero, segundo = _Socket(), _Socket()
    await cm.connect(primero, "s1")
    renovador_primero = cm._renovadores["s1"]
    await cm.connect(segundo, "s1")
    await asyncio.sleep(0)
    assert renovador_primero.cancelled() and primero.cerrado
    await cm.disconnect("s1", segundo)
    assert not cm._renovadores and not await cm.is_connected("s1")


@pytest.mark.asyncio
async def test_handshakes_concurrentes_no_dejan_renovadores_huerfanos(redis_instalado, servidor):
    """#27: con el socket anterior cerrándose despacio, dos handshakes A LA VEZ: el renovador del
    primero se creaba tras ese await y el segundo no lo veía — quedaba vivo para siempre, con una
    presencia fantasma (`is_connected` True sin socket)."""
    from geo_copilot.api import websocket as ws

    class _SocketLento(_Socket):
        async def close(self, code: int = 1000) -> None:
            await asyncio.sleep(0.2)
            self.cerrado = True

    def _renovadores_vivos() -> int:
        return sum(1 for t in asyncio.all_tasks()
                   if not t.done() and getattr(t.get_coro(), "__name__", "") == "_renovar_presencia")

    cm = ws.ConnectionManager()
    cm.conectar_bus(_cliente(servidor))
    x, primero, segundo = _SocketLento(), _Socket(), _Socket()
    await cm.connect(x, "s1")
    await asyncio.gather(cm.connect(primero, "s1"), cm.connect(segundo, "s1"))
    registrado = cm.active_connections["s1"]
    await asyncio.sleep(0)
    assert _renovadores_vivos() == 1
    await cm.disconnect("s1", registrado)
    await asyncio.sleep(0)
    assert _renovadores_vivos() == 0 and not cm._renovadores
    assert not await cm.is_connected("s1")


@pytest.mark.asyncio
async def test_la_aprobacion_llega_a_la_otra_replica_aunque_aqui_haya_un_socket_muerto(redis_instalado, servidor):
    """#5: el socket local medio muerto se tragaba el aviso (send_json al búfer) y no se publicaba."""
    from geo_copilot.api import websocket as ws

    a, b = ws.ConnectionManager(), ws.ConnectionManager()
    a.conectar_bus(_cliente(servidor))
    b.conectar_bus(_cliente(servidor))
    muerto, vivo = _Socket(), _Socket()
    await a.connect(muerto, "s1")
    await b.connect(vivo, "s1")
    assert await a.send_message("s1", ws.WSMessage(type=ws.WSMessageType.APPROVAL_REQUEST,
                                                   data={"approval_id": "x"}))
    await _hasta(lambda: vivo.recibidos)
    assert vivo.recibidos[0]["data"] == {"approval_id": "x"}
    await asyncio.sleep(0.1)
    assert len(muerto.recibidos) == 1  # el emisor no se lo entrega dos veces a su propio socket


# --------------------------------------------------------------------------- #4 · #28 · #78
@pytest.mark.asyncio
async def test_un_fallo_de_redis_al_avisar_no_lanza(monkeypatch):
    """#4: `publicar` propagaba ConnectionError y un turno ya calculado devolvía 500."""
    from geo_copilot.api import websocket as ws

    class _BusCaido:
        async def publicar(self, *_a):
            raise ConnectionError("Redis no responde")

    monkeypatch.setattr(bus, "_bus", _BusCaido())
    cm = ws.ConnectionManager()
    assert await cm.send_message("s1", ws.WSMessage(type=ws.WSMessageType.RESULT, data={})) is False
    assert await cm.detener("s1") == "cancel_failed"  # #28: tampoco tumba el WebSocket


@pytest.mark.asyncio
async def test_el_turno_termina_aunque_no_se_pueda_cerrar_su_registro(monkeypatch):
    """#4: el `cerrar_turno` del finally lanzaba y tapaba el resultado ya calculado."""
    from geo_copilot.api.models import QueryRequest
    from geo_copilot.api.routes import query as rutas
    from geo_copilot.orchestrator.conversation import ConversationManager

    async def _nada(*_a, **_k):
        return None

    class _AlmacenRoto(aprobaciones.AprobacionesEnMemoria):
        async def cerrar_turno(self, turno_id):
            raise ConnectionError("Redis no responde")

    monkeypatch.setattr(rutas, "asegurar_sesion_actual", _nada)
    monkeypatch.setattr(aprobaciones, "_almacen", _AlmacenRoto())

    async def procesar(**_k):
        return {"success": True, "message": ""}

    cm = ConversationManager()
    sesion = cm.create_session().session_id
    resp = await rutas.ejecutar_consulta(QueryRequest(query="q", session_id=sesion),
                                         SimpleNamespace(process=procesar, llm=None), cm)
    assert resp.status == "completed"


@pytest.mark.asyncio
async def test_detener_dice_lo_que_paso_y_confirma_quien_cancela(redis_instalado):
    """#28: sin tarea en ningún worker se respondía «cancelled». Ahora el que pide dice
    `cancel_requested` y el `cancelled` lo manda quien de verdad canceló."""
    from geo_copilot.api import websocket as ws

    a, b = ws.ConnectionManager(), ws.ConnectionManager()
    a.conectar_bus()
    b.conectar_bus()
    socket = _Socket()
    b.active_connections["s1"] = socket
    consulta = asyncio.create_task(asyncio.sleep(30))
    await a.register_task("s1", consulta)
    assert await b.detener("s1") == "cancel_requested"
    await _hasta(consulta.cancelled)
    await _hasta(lambda: socket.recibidos)
    assert socket.recibidos[0]["data"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_un_cliente_lento_no_retrasa_las_respuestas_hitl(servidor):
    """#78: el lector del bus esperaba cada envío WS en serie; un cliente que no lee frenaba las
    aprobaciones y los «Detener» de todas las sesiones del proceso."""
    cliente_pub, cliente_sub = _cliente(servidor), _cliente(servidor)
    lector = bus.BusEnRedis(cliente_sub)
    atascado, hitl = asyncio.Event(), []

    async def ws_lento(_canal, _datos):
        await atascado.wait()

    async def respuesta(_canal, datos):
        hitl.append(datos)

    lector.suscribir("ws:", ws_lento)
    lector.suscribir("hitl:resp", respuesta)
    await lector.iniciar()
    emisor = bus.BusEnRedis(cliente_pub)
    try:
        await emisor.publicar("ws:lenta", {"n": 1})
        await emisor.publicar("hitl:resp", {"request_id": "r1"})
        await _hasta(lambda: hitl)
        assert hitl == [{"request_id": "r1"}]
    finally:
        atascado.set()
        await lector.cerrar()
        await cliente_pub.aclose()
        await cliente_sub.aclose()


# --------------------------------------------------------------------------- #25 · #26 · #55
@pytest.mark.asyncio
async def test_aprobar_por_websocket_una_solicitud_que_espera_otro_worker(redis_instalado, monkeypatch):
    """#25/#26/#55: `handle_approval` buscaba solo en la memoria del proceso."""
    from geo_copilot.api import websocket as ws

    worker_a, worker_b = HITLManager(timeout=5), HITLManager(timeout=5)
    worker_a.conectar_bus()
    worker_b.conectar_bus()
    espera = asyncio.create_task(worker_a.request_approval(
        HITLActionType.SQL_EXECUTION, "SQL", "d", details={"sql": "SELECT 1"}, session_id="s1"))
    await _hasta(lambda: worker_a.get_pending_requests())
    id_ = worker_a.get_pending_requests()[0].id

    cm, socket = ws.ConnectionManager(), _Socket()
    cm.active_connections["s1"] = socket
    monkeypatch.setattr(ws, "connection_manager", cm)
    await ws.handle_approval("s1", {"approval_id": id_, "action": "approve"}, SimpleNamespace(hitl_manager=worker_b))
    assert (await asyncio.wait_for(espera, 3)).status == HITLStatus.APPROVED
    assert socket.recibidos[-1]["data"]["status"] == "approved"


@pytest.mark.asyncio
async def test_una_accion_desconocida_por_websocket_se_dice_tal_cual(monkeypatch):
    """#25/#26: una acción desconocida respondía «not found or already processed» (engañoso: la
    aprobación existe y sigue pendiente). Se rechaza antes de buscarla, como la ruta REST."""
    from geo_copilot.api import websocket as ws

    cm, socket = ws.ConnectionManager(), _Socket()
    cm.active_connections["s1"] = socket
    monkeypatch.setattr(ws, "connection_manager", cm)
    buscadas: list[str] = []

    async def buscar(id_):
        buscadas.append(id_)

    await ws.handle_approval("s1", {"approval_id": "a1", "action": "foo"},
                             SimpleNamespace(hitl_manager=SimpleNamespace(buscar=buscar)))
    assert socket.recibidos[-1]["data"]["error"] == "Unknown action: foo"
    assert buscadas == []


# --------------------------------------------------------------------------- #14 · #38
@pytest.mark.asyncio
async def test_si_el_cliente_http_se_fue_la_respuesta_llega_por_el_websocket(canal_ws, monkeypatch):
    """#14: recargar con una aprobación pendiente y aprobarla desde la pestaña nueva ejecutaba la
    acción, pero la respuesta iba a la petición HTTP muerta: el chat se quedaba en «Consultando…»."""
    from geo_copilot.api.models import QueryRequest
    from geo_copilot.api.routes import query as rutas

    llamada: dict[str, Any] = {}

    async def ejecutar(qr, _g, _c, *, turno_id, **_k):
        llamada["turno_id"] = turno_id
        await asyncio.sleep(0.05)  # el cliente se va mientras el turno corre
        return rutas.QueryResponse(query_id="q1", session_id="s1", status="completed", message="Listo.",
                                   artifacts=[])

    monkeypatch.setattr(rutas, "ejecutar_consulta", ejecutar)

    async def recibir():
        return {"type": "http.disconnect"}

    peticion = SimpleNamespace(receive=recibir)
    resp = await rutas.process_query.__wrapped__(
        request=peticion, query_request=QueryRequest(query="trae los lotes", session_id="s1"),
        agent_graph=object(), conversation_manager=object())
    assert resp.message == "Listo."
    entregado = _resultados(canal_ws)[0]
    assert entregado["turno_id"] == llamada["turno_id"] and entregado["consulta"] == "trae los lotes"
    assert entregado["respuesta"]["message"] == "Listo." and entregado["reanudada"] is False


@pytest.mark.asyncio
async def test_con_el_cliente_http_presente_no_se_duplica_por_el_websocket(canal_ws, monkeypatch):
    from geo_copilot.api.models import QueryRequest
    from geo_copilot.api.routes import query as rutas

    async def ejecutar(*_a, **_k):
        return rutas.QueryResponse(query_id="q1", session_id="s1", status="completed", message="ok", artifacts=[])

    monkeypatch.setattr(rutas, "ejecutar_consulta", ejecutar)
    nunca = asyncio.Event()

    async def recibir():
        await nunca.wait()

    await rutas.process_query.__wrapped__(
        request=SimpleNamespace(receive=recibir), query_request=QueryRequest(query="q", session_id="s1"),
        agent_graph=object(), conversation_manager=object())
    assert _resultados(canal_ws) == []


@pytest.mark.asyncio
async def test_todo_turno_tiene_correlacion_y_metrica(monkeypatch):
    """#38: los turnos del WebSocket y los reanudados no tenían métrica ni id de correlación."""
    from geo_copilot.api.routes import query as rutas
    from geo_copilot.platform import observabilidad

    medidas: list[bool] = []
    monkeypatch.setattr(observabilidad, "registrar_turno", lambda _s, ok: medidas.append(ok))
    assert observabilidad.correlacion() is None
    async with rutas.turno_observado("s1", "ws") as obs:
        dentro = observabilidad.correlacion()
        obs["ok"] = True
    assert dentro and observabilidad.correlacion() is None
    assert medidas == [True]


@pytest.mark.asyncio
async def test_la_consulta_por_websocket_corre_con_correlacion_y_metrica(canal_ws, monkeypatch):
    """#38: `handle_query` del WebSocket — el grafo ve un id de correlación, la métrica registra el
    resultado (también el fallo) y la correlación queda restaurada al salir."""
    from geo_copilot.api import websocket as ws
    from geo_copilot.platform import observabilidad

    medidas: list[bool] = []
    monkeypatch.setattr(observabilidad, "registrar_turno", lambda _s, ok: medidas.append(ok))
    vistas: list[str | None] = []

    async def bien(**_k):
        vistas.append(observabilidad.correlacion())
        return {"success": True}

    async def mal(**_k):
        vistas.append(observabilidad.correlacion())
        raise RuntimeError("el LLM no responde")

    async def pausa(**_k):
        return {"success": False, "requires_approval": True, "pending_approval_id": "a1"}

    for proceso in (bien, mal, pausa):
        await ws.handle_query("s1", {"query": "trae los lotes"},
                              SimpleNamespace(agent_graph=SimpleNamespace(process=proceso)))
    assert all(vistas) and len(vistas) == 2
    assert medidas == [True, False, True]  # una pausa para aprobar no es un fallo del turno
    assert observabilidad.correlacion() is None

    token = observabilidad.fijar_correlacion("de-la-peticion")
    try:
        await ws.handle_query("s1", {"query": "q"}, SimpleNamespace(agent_graph=SimpleNamespace(process=bien)))
        assert vistas[-1] == "de-la-peticion"  # no pisa la que ya había
        assert observabilidad.correlacion() == "de-la-peticion"
    finally:
        token.var.reset(token)


@pytest.mark.asyncio
async def test_v5_el_turno_que_espero_a_una_persona_entrega_tambien_por_ws():
    """V5 F7: recargar con la aprobación pendiente y aprobarla dejaba el resultado en una petición
    HTTP que el servidor creía viva (el proxy no propagó el cierre): nunca llegaba al chat."""
    import asyncio as _asyncio

    from geo_copilot.api.routes.query import _entregar_tambien_por_ws
    from geo_copilot.platform import observabilidad

    vivo: _asyncio.Future[bool] = _asyncio.get_running_loop().create_future()  # el cliente no se fue
    with observabilidad.contar_espera_humana():
        sin_aprobacion = observabilidad.espera_del_turno()
        assert not _entregar_tambien_por_ws(vivo, sin_aprobacion)
        observabilidad.registrar_espera_hitl(4.2)        # la persona decidió (aprobó, rechazó o caducó)
        assert _entregar_tambien_por_ws(vivo, observabilidad.espera_del_turno())
    assert observabilidad.espera_del_turno() is None      # fuera del turno no queda cuenta
    se_fue: _asyncio.Future[bool] = _asyncio.get_running_loop().create_future()
    se_fue.set_result(True)
    assert _entregar_tambien_por_ws(se_fue, None)         # el caso de siempre: el cliente se desconectó
    vivo.cancel()
