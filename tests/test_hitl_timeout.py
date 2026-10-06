"""FIX-HITL-TIMEOUT — el reloj de cómputo no capa la espera humana de HITL.

Antes: query.py envolvía agent_graph.process en wait_for(total_execution_timeout
=120s), mientras la aprobación HITL suspende hasta hitl_timeout=300s. El wait_for
externo cancelaba la task → 504 engañoso + "Aprobar" 404. Ahora el presupuesto
del wait_for suma el de HITL cuando está activo.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from geo_copilot.api.routes.query import compute_process_timeout


def _settings(*, total, hitl_enabled, hitl_timeout):
    return SimpleNamespace(
        total_execution_timeout=total,
        hitl_enabled=hitl_enabled,
        hitl_timeout=hitl_timeout,
    )


class TestComputeProcessTimeout:
    def test_suma_presupuesto_hitl_cuando_activo(self):
        assert compute_process_timeout(_settings(total=120, hitl_enabled=True, hitl_timeout=300)) == 420

    def test_solo_computo_cuando_hitl_inactivo(self):
        assert compute_process_timeout(_settings(total=120, hitl_enabled=False, hitl_timeout=300)) == 120

    def test_defaults_reales_del_proyecto(self):
        # Con los defaults (120 cómputo, 300 HITL activo) la ventana humana cabe.
        eff = compute_process_timeout(_settings(total=120, hitl_enabled=True, hitl_timeout=300))
        assert eff > 300, "la ventana de aprobación humana (300s) debe caber en el presupuesto"


@pytest.mark.asyncio
class TestComportamientoReal:
    async def test_espera_lenta_no_se_cancela_con_hitl_activo(self):
        """Una task que tarda MÁS que el reloj de cómputo pero MENOS que
        cómputo+HITL completa sin cancelarse (simula una aprobación humana lenta)."""
        async def slow():
            await asyncio.sleep(0.2)  # > total(0.1), < total+hitl(0.4)
            return "aprobado"

        eff = compute_process_timeout(_settings(total=0.1, hitl_enabled=True, hitl_timeout=0.3))
        result = await asyncio.wait_for(slow(), timeout=eff)
        assert result == "aprobado"

    async def test_computo_colgado_sigue_acotado_sin_hitl(self):
        """Sin HITL, el reloj de cómputo sigue cortando un proceso colgado
        (no perdemos la guardia contra LLM/agente en bucle)."""
        async def hung():
            await asyncio.sleep(0.3)  # > total(0.1)
            return "no debería llegar"

        eff = compute_process_timeout(_settings(total=0.1, hitl_enabled=False, hitl_timeout=0.3))
        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await asyncio.wait_for(hung(), timeout=eff)
