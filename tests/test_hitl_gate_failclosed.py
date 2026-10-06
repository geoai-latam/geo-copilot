"""Los tres gates de HITL fallan CERRADOS (auditoría 2026-09-08, §1.1).

Los gates comprobaban ``REJECTED`` y ``MODIFIED``, y **todo lo demás caía a la
ejecución**. ``request_approval`` devuelve ``EXPIRED`` al vencer ``hitl_timeout``
—300 s por defecto—, así que bastaba **no contestar cinco minutos** para que el
código Python generado por el LLM se ejecutara sin que nadie lo aprobara.

Que era un bug y no un diseño lo prueba el propio repo: la ruta SQL viva sí lo
trataba (``orchestrator/nodes/gis_agent.py``) y ``docs/archive/legado/HITL.md`` documentaba
el manejo de ``EXPIRED`` como si existiera en ambas.

Los gates son ahora una ALLOWLIST: pasa ``APPROVED``, pasa ``MODIFIED``, y
cualquier otro estado —incluido el que se añada mañana— bloquea. Estos tests
cubren los tres sitios y, en cada uno, el control de que los dos estados
legítimos SÍ ejecutan: un gate que bloqueara todo también dejaría verdes las
aserciones de bloqueo.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.security.hitl import HITLResponse, HITLStatus

# EXPIRED es el que ocurre solo, sin que el usuario haga nada. PENDING entra
# como centinela del "estado nuevo" que antes se colaba por el `else`.
NO_APRUEBAN = [HITLStatus.EXPIRED, HITLStatus.REJECTED, HITLStatus.PENDING]

GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-74.08, 4.61]},
            "properties": {"id": 1},
        }
    ],
}


def _respuesta(status: HITLStatus, contenido=None) -> HITLResponse:
    return HITLResponse(request_id="req-1", status=status, modified_content=contenido)


# ----------------------------------------------------------------------
# 1 · PythonAgent.process — la ruta VIVA: ejecuta código del LLM.
# ----------------------------------------------------------------------
class TestPythonAgentNoEjecutaSinAprobacion:
    @staticmethod
    def _agente(monkeypatch, status: HITLStatus, modificado: str | None = None):
        import geo_copilot.agents.python_agent.agent as agent_mod

        monkeypatch.setattr(agent_mod.settings, "hitl_enabled", True)
        agente = agent_mod.PythonAgent(llm_client=MagicMock(), hitl_manager=MagicMock())
        agente.hitl_manager.request_approval = AsyncMock(
            return_value=_respuesta(status, modificado)
        )
        agente._generate_code = AsyncMock(return_value="resultado = gdf.buffer(100)")
        agente._execute_in_sandbox = AsyncMock(
            return_value={"success": False, "error": "no debería llegar aquí"}
        )
        return agente

    @staticmethod
    async def _procesar(agente):
        return await agente.process(
            "haz un buffer de 100 m",
            {"active_data_source": "external", "external_geojson": GEOJSON},
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", NO_APRUEBAN)
    async def test_estado_que_no_aprueba_no_llega_al_sandbox(self, monkeypatch, status):
        agente = self._agente(monkeypatch, status)

        respuesta = await self._procesar(agente)

        agente._execute_in_sandbox.assert_not_awaited()
        assert respuesta.success is False
        assert respuesta.data["approved"] is False
        assert respuesta.data["hitl_status"] == status.value

    @pytest.mark.asyncio
    async def test_expirado_lo_dice_en_lenguaje_de_persona(self, monkeypatch):
        # El humano que no contestó a tiempo tiene que poder distinguir un
        # vencimiento de un rechazo: son dos acciones distintas de su parte.
        agente = self._agente(monkeypatch, HITLStatus.EXPIRED)

        respuesta = await self._procesar(agente)

        assert "expirado" in respuesta.message.lower()

    @pytest.mark.asyncio
    async def test_aprobado_si_ejecuta(self, monkeypatch):
        agente = self._agente(monkeypatch, HITLStatus.APPROVED)

        await self._procesar(agente)

        agente._execute_in_sandbox.assert_awaited_once()
        assert agente._execute_in_sandbox.await_args[0][0] == "resultado = gdf.buffer(100)"

    @pytest.mark.asyncio
    async def test_modificado_ejecuta_el_codigo_del_usuario(self, monkeypatch):
        # Y la huella R0.8 se recalcula sobre ÉL: si no, el guard de integridad
        # abortaría la ejecución que el usuario acaba de autorizar.
        agente = self._agente(monkeypatch, HITLStatus.MODIFIED, "resultado = gdf.head(1)")

        await self._procesar(agente)

        agente._execute_in_sandbox.assert_awaited_once()
        assert agente._execute_in_sandbox.await_args[0][0] == "resultado = gdf.head(1)"


# ----------------------------------------------------------------------
# 2 · GISAgent.execute_query — mismo patrón, sobre SQL.
# ----------------------------------------------------------------------
class TestGisAgentNoEjecutaSqlSinAprobacion:
    SQL = "SELECT id FROM catastro.lotes LIMIT 10"

    @staticmethod
    def _agente(monkeypatch, status: HITLStatus, modificado: str | None = None):
        import geo_copilot.agents.gis_agent.agent as agent_mod

        monkeypatch.setattr(agent_mod.settings, "hitl_enabled", True)
        agente = agent_mod.GISAgent(
            llm_client=MagicMock(), hitl_manager=MagicMock(), db_connection=object()
        )
        # S0.2: con `enforce` por defecto, un GISAgent sin semantic layer tiene
        # la allowlist vacía y rechaza todo (falla cerrado). Este test prueba
        # otra cosa, así que declara las tablas que usa.
        agente.sql_validator.allowed_tables = lambda: {"catastro.lotes"}
        agente.hitl_manager.request_approval = AsyncMock(
            return_value=_respuesta(status, modificado)
        )
        agente._execute_sql = AsyncMock(return_value=([], None))
        return agente

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", NO_APRUEBAN)
    async def test_estado_que_no_aprueba_no_toca_la_base(self, monkeypatch, status):
        agente = self._agente(monkeypatch, status)

        resultado = await agente.execute_query(self.SQL)

        agente._execute_sql.assert_not_awaited()
        assert resultado["success"] is False
        assert status.value in resultado["message"]

    @pytest.mark.asyncio
    async def test_aprobado_si_ejecuta(self, monkeypatch):
        agente = self._agente(monkeypatch, HITLStatus.APPROVED)

        await agente.execute_query(self.SQL)

        agente._execute_sql.assert_awaited_once()
        assert agente._execute_sql.await_args[0][0] == self.SQL

    @pytest.mark.asyncio
    async def test_modificado_ejecuta_el_sql_del_usuario(self, monkeypatch):
        otro = "SELECT id FROM catastro.lotes LIMIT 1"
        agente = self._agente(monkeypatch, HITLStatus.MODIFIED, otro)

        await agente.execute_query(self.SQL)

        agente._execute_sql.assert_awaited_once()
        assert agente._execute_sql.await_args[0][0] == otro


# ----------------------------------------------------------------------
# 3 · PythonSandbox.execute — la trampa esperando al tercer llamador.
#     Hoy los dos vivos pasan require_approval=False, pero el default es True.
# ----------------------------------------------------------------------
class TestSandboxNoEjecutaSinAprobacion:
    CODIGO = "resultado = 1 + 1"

    @staticmethod
    def _sandbox(monkeypatch, status: HITLStatus, modificado: str | None = None):
        import geo_copilot.agents.gis_agent.sandbox as sandbox_mod

        monkeypatch.setattr(sandbox_mod.settings, "hitl_enabled", True)
        # El gate va DESPUÉS del guard de plataforma POSIX; sin esto el test
        # verificaría el rechazo por SO, no la aprobación.
        monkeypatch.setattr(sandbox_mod, "SANDBOX_AVAILABLE", True)
        caja = sandbox_mod.PythonSandbox(hitl_manager=MagicMock())
        caja.hitl_manager.request_approval = AsyncMock(
            return_value=_respuesta(status, modificado)
        )
        caja._run_in_subprocess = AsyncMock(return_value={"success": True, "stdout": ""})
        caja._run_in_docker = AsyncMock(return_value={"success": True, "stdout": ""})
        return caja

    @staticmethod
    def _ejecuciones(caja) -> int:
        return caja._run_in_subprocess.await_count + caja._run_in_docker.await_count

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", NO_APRUEBAN)
    async def test_estado_que_no_aprueba_no_ejecuta(self, monkeypatch, status):
        caja = self._sandbox(monkeypatch, status)

        resultado = await caja.execute(self.CODIGO, require_approval=True)

        assert self._ejecuciones(caja) == 0
        assert resultado["success"] is False
        assert status.value in resultado["error"]

    @pytest.mark.asyncio
    async def test_aprobado_si_ejecuta(self, monkeypatch):
        caja = self._sandbox(monkeypatch, HITLStatus.APPROVED)

        await caja.execute(self.CODIGO, require_approval=True)

        assert self._ejecuciones(caja) == 1

    @pytest.mark.asyncio
    async def test_modificado_ejecuta_el_codigo_del_usuario(self, monkeypatch):
        caja = self._sandbox(monkeypatch, HITLStatus.MODIFIED, "resultado = 2 + 2")

        await caja.execute(self.CODIGO, require_approval=True)

        assert self._ejecuciones(caja) == 1
        llamada = caja._run_in_subprocess.await_args or caja._run_in_docker.await_args
        assert llamada[0][0] == "resultado = 2 + 2"
