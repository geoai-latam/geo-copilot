"""R0.8 (auditoría 2026-07-26, AUD-15) — integridad del artefacto aprobado.

El panel de aprobación construía su ``content`` como
``request.preview or details["sql"]``. Como ``preview`` venía truncado a 500
caracteres, el SQL completo NUNCA llegaba a la UI: el humano aprobaba un
fragmento y ``_execute_sql`` ejecutaba la variable completa. El artefacto
autorizado y el ejecutado eran objetos distintos.
"""

from __future__ import annotations

import pytest


class TestArtefactoIntegroEnHITL:
    """R0.8 (auditoría 2026-07-26, AUD-15).

    El panel construía su `content` como `request.preview or details["sql"]`, y
    como `preview` venía truncado a 500 caracteres, el SQL completo NUNCA
    llegaba a la UI. El humano aprobaba un fragmento; `_execute_sql` ejecutaba
    la variable completa. Bastaba con que la cola —invisible— cambiara la tabla
    objetivo. El control no se evadía: se engañaba con su propia presentación.
    """

    @pytest.mark.asyncio
    async def test_el_panel_recibe_el_sql_completo_no_el_preview(self):
        from geo_copilot.security.hitl import (
            HITLActionType,
            HITLManager,
            HITLRequest,
            content_digest,
        )

        # SQL de 800 caracteres cuyos últimos 300 cambian la tabla objetivo.
        cola = " UNION SELECT rolpassword FROM pg_authid"
        sql = "SELECT " + ("a, " * 250) + "b FROM catastro.lotes" + cola
        assert len(sql) > 500

        capturado: dict = {}

        async def _cb(session_id, payload):
            capturado.update(payload)

        mgr = HITLManager()
        mgr._notification_callback = _cb
        req = HITLRequest(
            action_type=HITLActionType.SQL_EXECUTION,
            title="t",
            description="d",
            details={"sql": sql},
            preview=sql[:500],
            session_id="s1",
        )
        await mgr._notify_pending_request(req)

        assert capturado["content"] == sql, "el panel debe recibir el artefacto ÍNTEGRO"
        assert cola in capturado["content"], "la cola peligrosa no puede quedar invisible"
        assert capturado["content_sha256"] == content_digest(sql)

    def test_la_huella_distingue_artefactos(self):
        from geo_copilot.security.hitl import content_digest

        a = "SELECT 1 FROM lotes"
        b = "SELECT 1 FROM auth.usuarios"
        assert content_digest(a) != content_digest(b)
        assert content_digest(a) == content_digest(a)
        assert content_digest(None) == content_digest("")
