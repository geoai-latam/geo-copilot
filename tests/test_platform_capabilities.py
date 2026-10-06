"""F1.1: autoconocimiento de plataforma — el router sabe (en decision-time)
qué NO puede hacer el sandbox y propone la alternativa PostGIS."""

from geo_copilot.agents.router_agent.agent import RouterAgent
from geo_copilot.core.formatters import format_platform_capabilities


def test_block_empty_when_sandbox_available():
    assert format_platform_capabilities(True) == ""


def test_block_warns_and_proposes_postgis_when_unavailable():
    out = format_platform_capabilities(False)
    assert "NO DISPONIBLES" in out
    assert "ST_Buffer" in out  # propone la alternativa PostGIS
    assert "POSIX" in out or "Linux" in out


def test_router_prompt_includes_platform_block_when_sandbox_down():
    ra = RouterAgent.__new__(RouterAgent)
    ctx = {
        "sandbox_available": False,
        "schema_info": "",
        "conversation_history": [],
        "found_services": [],
    }
    ed = ra._format_external_data_context(ctx)
    from geo_copilot.core.formatters import format_platform_capabilities as fpc
    blk = fpc(ctx.get("sandbox_available", True))
    combined = (f"{ed}\n\n{blk}".strip()) if blk else ed
    sysp = ra._build_system_prompt(
        schema_info="", conversation_context="", services_context="",
        external_data_context=combined,
    )
    # El LLM ve la limitación + la alternativa en su system prompt.
    assert "ST_Buffer" in sysp
    assert "NO DISPONIBLES" in sysp


def test_router_prompt_no_platform_block_when_available():
    ra = RouterAgent.__new__(RouterAgent)
    from geo_copilot.core.formatters import format_platform_capabilities as fpc
    assert fpc(True) == ""


class TestPythonAgentSandboxGuard:
    """F1.1 enforcement: el nodo python_agent corta EARLY si no hay sandbox,
    con mensaje honesto + alternativa PostGIS (sin generar código ni HITL)."""

    import pytest

    @pytest.mark.asyncio
    async def test_no_sandbox_short_circuits_with_postgis_alternative(self, monkeypatch):
        from unittest.mock import MagicMock

        import geo_copilot.agents.gis_agent.sandbox as sb
        from geo_copilot.orchestrator.nodes import python_agent

        monkeypatch.setattr(sb, "SANDBOX_AVAILABLE", False)

        graph = MagicMock()
        state = {
            "query": "hazle un buffer de 500m a la capa",
            "active_data_source": "external",
            "external_geojson": {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}],
            },
            "external_source_name": "Localidades",
            "session_id": "",
        }

        updates = await python_agent.run(graph, state)

        # Mensaje honesto + propone PostGIS, sin error opaco de sandbox.
        assert "PostGIS" in updates["final_response"] or "ST_Buffer" in updates["final_response"]
        assert "Linux" in updates["final_response"] or "Docker" in updates["final_response"]
        # No debió generar python_code (cortó antes).
        assert "python_code" not in updates or updates.get("python_code") is None


class TestServiciosConectados:
    """F3: lo que hay enchufado lo dice el hub; el router no lleva una lista fija."""

    def test_con_servicios_el_bloque_los_lista(self):
        from geo_copilot.core.formatters import format_platform_capabilities as fpc

        blk = fpc(True, servicios_conectados="SERVICIOS MCP CONECTADOS:\n  - imagery (disponible)")
        assert "imagery (disponible)" in blk and "CAPACIDADES DE LA PLATAFORMA" in blk

    def test_sin_servicios_prohibe_el_intent(self):
        from geo_copilot.core.formatters import format_platform_capabilities as fpc

        assert "NO elijas `connected_service`" in fpc(True, servicios_conectados="")

    def test_connected_service_va_al_bucle_react_en_cualquier_politica(self, monkeypatch):
        from unittest.mock import MagicMock

        from geo_copilot.orchestrator import graph as graph_mod

        g = graph_mod.GeoAgentGraph.__new__(graph_mod.GeoAgentGraph)
        for politica in ("off", "hybrid", "always"):
            monkeypatch.setattr(graph_mod, "get_settings", lambda p=politica: MagicMock(
                react_policy=p, react_mode=False, hitl_mode="blocking", enable_planning=True))
            assert g._route_from_router({"intent": "connected_service", "messages": []}) == "agent_loop"
