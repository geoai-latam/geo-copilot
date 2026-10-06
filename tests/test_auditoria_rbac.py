"""F6 (S6.3, S6.4) — permisos por rol sobre las capacidades y la auditoría de lo que se hace.

Todo pasa por `capabilities.ejecutar` (el bucle del agente y los /run del mapa y del panel):
ahí se decide si el rol alcanza para el riesgo de la capacidad y se deja constancia.
"""

from __future__ import annotations

import pytest

from geo_copilot.platform import auditoria
from geo_copilot.platform.capabilities import Capability, ToolOutcome, ejecutar
from geo_copilot.platform.identidad.principal import Principal, fijar_principal, restaurar_principal

VISOR = Principal(sub="beto", org_id="acme", roles=frozenset({"viewer"}), nombre="Beto")
ANALISTA = Principal(sub="ana", org_id="acme", roles=frozenset({"analyst"}), nombre="Ana")
ADMIN = Principal(sub="carla", org_id="acme", roles=frozenset({"admin"}), nombre="Carla")
OTRA_ORG = Principal(sub="diego", org_id="beta", roles=frozenset({"admin"}), nombre="Diego")


@pytest.fixture
def registro():
    store = auditoria.AuditoriaEnMemoria()
    auditoria.instalar(store)
    yield store
    auditoria.instalar(None)


def _cap(riesgo: str, llamadas: list, *, falla: bool = False) -> Capability:
    async def executor(_g, _w, args):
        llamadas.append(args)
        if falla:
            raise RuntimeError("se cayó")
        return ToolOutcome(observation="hecho", success=True)

    return Capability(id=f"core.prueba_{riesgo}", tool_name=f"prueba_{riesgo}", description="d",
                      parameters={"type": "object", "properties": {}}, executor=executor, blurb="b",
                      risk=riesgo)  # type: ignore[arg-type]


async def _como(principal: Principal | None, cap: Capability, args: dict) -> ToolOutcome:
    t = fijar_principal(principal)
    try:
        return await ejecutar(cap, None, {"session_id": "s1"}, args)
    finally:
        restaurar_principal(t)


# ---------------------------------------------------------------------------
# Resumen de argumentos
# ---------------------------------------------------------------------------


def test_los_argumentos_se_guardan_resumidos_y_sin_secretos():
    r = auditoria.resumir({
        "request": "x" * 1000, "api_key": "sk-123", "Authorization": "Bearer abc",
        "aoi": {"type": "FeatureCollection", "features": [{}] * 4000},
        "geom": {"type": "Polygon", "coordinates": [[[0, 0]] * 5000]},
        "credenciales": {"usuario": "u", "password": "p"},
        "ids": list(range(500)), "n": 3,
    })
    assert r["api_key"] == r["Authorization"] == r["credenciales"] == "***"
    assert r["aoi"] == {"geometria": "FeatureCollection", "elementos": 4000}
    assert r["geom"] == {"geometria": "Polygon"}
    assert r["request"].endswith("(1000 caracteres)") and len(r["request"]) < 400
    assert r["ids"] == "[… 500 elementos]" and r["n"] == 3


# ---------------------------------------------------------------------------
# Permisos por rol (E6.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_un_visor_no_calcula_y_queda_constancia_de_la_denegacion(registro):
    llamadas: list = []
    out = await _como(VISOR, _cap("compute", llamadas), {"meters": 500})
    assert out.success is False and llamadas == []  # no se ejecutó NADA
    assert "requiere el rol «analyst»" in out.observation and "«viewer»" in out.observation
    assert out.facts == {"denegado": True, "riesgo": "compute", "rol": "viewer", "rol_requerido": "analyst"}
    (fila,) = registro.filas
    assert (fila["actor_sub"], fila["resultado"], fila["recurso"]) == ("beto", "denegado", "core.prueba_compute")
    assert fila["detalle"]["argumentos"] == {"meters": 500}


@pytest.mark.asyncio
@pytest.mark.parametrize("principal, riesgo, permitido", [
    (VISOR, "read", True), (VISOR, "external_egress", True), (VISOR, "compute", False), (VISOR, "write", False),
    (ANALISTA, "compute", True), (ANALISTA, "write", True),
    (ADMIN, "compute", True), (ADMIN, "raro", True), (ANALISTA, "raro", False),
])
async def test_matriz_de_roles_por_riesgo(registro, principal, riesgo, permitido):
    llamadas: list = []
    out = await _como(principal, _cap(riesgo, llamadas), {})
    assert out.success is permitido and bool(llamadas) is permitido


@pytest.mark.asyncio
async def test_lo_ejecutado_y_lo_que_falla_tambien_queda(registro):
    await _como(ANALISTA, _cap("compute", []), {"meters": 10})
    with pytest.raises(RuntimeError):
        await _como(ANALISTA, _cap("compute", [], falla=True), {})
    ok, error = registro.filas
    assert (ok["resultado"], ok["actor_nombre"], ok["session_id"]) == ("ok", "Ana", "s1")
    assert (error["resultado"], error["detalle"]["error"]) == ("error", "RuntimeError")


@pytest.mark.asyncio
async def test_sin_principal_el_sistema_ejecuta(registro):
    """Procesos del propio sistema (sin petición de un usuario): nada que comprobar."""
    llamadas: list = []
    out = await _como(None, _cap("compute", llamadas), {})
    assert out.success and llamadas and registro.filas[0]["actor_sub"] == "sistema"


@pytest.mark.asyncio
async def test_el_camino_directo_al_sandbox_tambien_respeta_el_rol(registro):
    from geo_copilot.orchestrator.nodes import python_agent

    t = fijar_principal(VISOR)
    try:
        out = await python_agent.run(None, {"query": "analiza la autocorrelación", "session_id": "s1"})
    finally:
        restaurar_principal(t)
    assert "requieren el rol «analyst»" in out["final_response"]
    assert registro.filas[0]["resultado"] == "denegado"


@pytest.mark.asyncio
async def test_una_decision_hitl_queda_con_quien_la_tomo(registro):
    import asyncio

    from geo_copilot.security.hitl import HITLActionType, HITLManager, HITLStatus

    m = HITLManager(timeout=5)
    t = fijar_principal(ANALISTA)
    try:
        tarea = asyncio.create_task(m.request_approval(HITLActionType.SQL_EXECUTION, "Ejecutar SQL", "d",
                                                       preview="SELECT 1", session_id="s1"))
        while not m.get_pending_requests():
            await asyncio.sleep(0.01)
        rid = m.get_pending_requests()[0].id
    finally:
        restaurar_principal(t)
    t = fijar_principal(ADMIN)  # otra persona decide (p. ej. por el WebSocket)
    try:
        await m.approve(rid)
    finally:
        restaurar_principal(t)
    resp = await tarea
    assert resp.status == HITLStatus.APPROVED and resp.approved_by == "carla"
    solicitud, decision = registro.filas
    assert (solicitud["accion"], solicitud["actor_sub"], solicitud["resultado"]) == ("hitl.solicitar", "ana", "pendiente")
    assert (decision["accion"], decision["actor_sub"]) == ("hitl.aprobar", "carla")
    assert decision["detalle"]["vista_previa"] == "SELECT 1"


# ---------------------------------------------------------------------------
# La API de auditoría (E6.5): solo admin, solo su organización
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_la_auditoria_de_una_organizacion_no_se_ve_desde_otra(registro):
    await auditoria.registrar("consulta", "agente", "recibida", principal=ANALISTA, detalle={"consulta": "a"})
    await auditoria.registrar("consulta", "agente", "recibida", principal=OTRA_ORG, detalle={"consulta": "b"})
    acme = await registro.listar("acme")
    assert [f["actor_sub"] for f in acme] == ["ana"]


def test_la_api_de_auditoria_exige_admin(registro, monkeypatch):
    from fastapi.testclient import TestClient

    from geo_copilot.api import app as app_module
    from geo_copilot.api.auth import require_principal

    fastapi_app = app_module.create_app()
    with TestClient(fastapi_app) as c:
        auditoria.instalar(registro)  # el arranque instala la suya
        fastapi_app.dependency_overrides[require_principal] = lambda: _fijar(ANALISTA)
        assert c.get("/api/v1/auditoria").status_code == 403
        fastapi_app.dependency_overrides[require_principal] = lambda: _fijar(ADMIN)
        r = c.get("/api/v1/auditoria")
        assert r.status_code == 200 and r.json()["organizacion"] == "acme"
        assert c.get("/api/v1/auditoria/verificar").json() == {"integra": True, "encadenada": False}


def _fijar(p: Principal) -> Principal:
    fijar_principal(p)
    return p


def test_a_un_visor_no_se_le_ofrece_lo_que_se_le_negaria():
    """V3 F6 (E6.4): tras denegarle el buffer, las sugerencias le ofrecían «calcula el área de
    cada lote». Sugerencias y menú contextual solo con lo que SU rol puede ejecutar.

    Con capacidades propias del test (no depende de qué haya disponible en el entorno: en CI no
    hay workspace y las `ws_*` no están)."""
    from geo_copilot.platform.acciones import aplicables
    from geo_copilot.platform.capabilities import registry
    from geo_copilot.platform.sugerencias import _herramientas

    geo = {"aoi": {"accepts": ["geometry"], "geometry_types": ["Polygon"]}}
    calculo = Capability(id="core.prueba_calculo", tool_name="prueba_calculo_f6", description="d",
                         parameters={"type": "object", "properties": {"aoi": {}}}, executor=_cap("compute", []).executor,
                         blurb="Calcular algo (prueba F6)", risk="compute", geo_inputs=geo)
    lectura = Capability(id="core.prueba_lectura", tool_name="prueba_lectura_f6", description="d",
                         parameters={"type": "object", "properties": {"aoi": {}}}, executor=_cap("read", []).executor,
                         blurb="Leer algo (prueba F6)", risk="read", geo_inputs=geo)
    registry().register(calculo, replace=True)
    registry().register(lectura, replace=True)
    try:
        for principal, calcula in ((VISOR, False), (ANALISTA, True)):
            t = fijar_principal(principal)
            try:
                texto = _herramientas()
                assert "Leer algo (prueba F6)" in texto
                assert ("Calcular algo (prueba F6)" in texto) is calcula, principal.rol
                ofrecidas = {a["herramienta"] for a in aplicables("Polygon")}
                assert "prueba_lectura_f6" in ofrecidas
                assert ("prueba_calculo_f6" in ofrecidas) is calcula, (principal.rol, ofrecidas)
            finally:
                restaurar_principal(t)
    finally:
        registry().unregister("prueba_calculo_f6")
        registry().unregister("prueba_lectura_f6")
