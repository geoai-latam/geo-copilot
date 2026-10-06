"""Cobertura de los nodos del orquestador ``symbology`` y ``python_agent``
+ el helper ``_layer_context`` (Sesión C, diagnóstico 2026-06-19).

Hueco detectado: ``nodes/symbology`` (0-23%) y ``nodes/python_agent`` (26%)
sin cobertura real. La rama de retry de python_agent (líneas 117+) NUNCA se
ejecutaba en tests porque en Windows ``SANDBOX_AVAILABLE`` es False y el nodo
corta antes; aquí se parchea a True para ejercitar la ejecución + auto-corrección.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.orchestrator.nodes import _layer_context
from geo_copilot.orchestrator.nodes import python_agent as py_node
from geo_copilot.orchestrator.nodes import symbology as sym_node


def _fc(props: dict | None = None) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": props or {"v": 1},
            }
        ],
    }


def _resp(success: bool, data: dict | None = None, message: str = "") -> SimpleNamespace:
    return SimpleNamespace(success=success, data=data, message=message)


# =============================================================================
# _layer_context.resolve_active_layer — orden de preferencia
# =============================================================================
class TestResolveActiveLayer:
    def test_internal_wins(self):
        gj = _fc()
        state = {"geojson": gj, "active_source_name": "lotes"}
        out, name, kind = _layer_context.resolve_active_layer(state)
        assert out is gj and kind == "internal" and name == "lotes"

    def test_external_when_no_internal(self):
        gj = _fc()
        state = {"external_geojson": gj, "external_source_name": "hub"}
        out, name, kind = _layer_context.resolve_active_layer(state)
        assert out is gj and kind == "external" and name == "hub"

    def test_previous_when_no_current(self):
        gj = _fc()
        state = {"previous_geojson": gj}
        out, name, kind = _layer_context.resolve_active_layer(state)
        assert out is gj and kind == "previous"
        assert name == "capa cargada previamente"

    def test_none_when_empty(self):
        out, name, kind = _layer_context.resolve_active_layer({})
        assert out is None and kind == "none"


# =============================================================================
# nodes/symbology.run
# =============================================================================
class TestSymbologyNode:
    @pytest.mark.asyncio
    async def test_no_data_returns_failure(self):
        graph = MagicMock()
        updates = await sym_node.run(graph, {"query": "pinta", "session_id": ""})
        assert updates["messages"][0]["success"] is False
        assert "No hay datos" in updates["messages"][0]["content"]

    @pytest.mark.asyncio
    async def test_success_internal_propagates_symbology(self):
        graph = MagicMock()
        symbology = {"layer_title": "Lotes", "symbology_type": "single_symbol"}
        graph.symbology_agent.process = AsyncMock(return_value=_resp(True, symbology))

        state = {"query": "pinta de rojo", "geojson": _fc(), "session_id": ""}
        updates = await sym_node.run(graph, state)

        assert updates["symbology"] == symbology
        assert updates["layer_name"] == "Lotes"
        assert updates["messages"][0]["success"] is True
        # No re-emite geojson porque la fuente fue 'internal' (state.geojson).
        assert "geojson" not in updates

    @pytest.mark.asyncio
    async def test_success_on_previous_layer_reemits_geojson(self):
        """Restyle sobre capa heredada (skip data/gis): el nodo re-emite el
        geojson al state para que el frontend re-renderice con el nuevo estilo."""
        graph = MagicMock()
        gj = _fc()
        graph.symbology_agent.process = AsyncMock(
            return_value=_resp(True, {"layer_title": "Capa"})
        )
        state = {"query": "ponla verde", "previous_geojson": gj, "session_id": ""}
        updates = await sym_node.run(graph, state)

        assert updates["geojson"] is gj  # re-emitido
        assert updates["symbology"]["layer_title"] == "Capa"

    @pytest.mark.asyncio
    async def test_agent_failure_surfaces_message(self):
        graph = MagicMock()
        graph.symbology_agent.process = AsyncMock(
            return_value=_resp(False, None, message="no pude simbolizar")
        )
        state = {"query": "x", "geojson": _fc(), "session_id": ""}
        updates = await sym_node.run(graph, state)
        assert updates["messages"][0]["success"] is False
        assert updates["messages"][0]["content"] == "no pude simbolizar"

    @pytest.mark.asyncio
    async def test_exception_returns_error(self):
        graph = MagicMock()
        graph.symbology_agent.process = AsyncMock(side_effect=RuntimeError("boom"))
        state = {"query": "x", "geojson": _fc(), "session_id": ""}
        updates = await sym_node.run(graph, state)
        assert updates["error"] == "boom"
        assert updates["messages"] == []


# =============================================================================
# nodes/python_agent.run
# =============================================================================
class TestPythonAgentNode:
    @pytest.mark.asyncio
    async def test_no_data_returns_error(self):
        graph = MagicMock()
        state = {"query": "buffer 500m", "session_id": "", "active_data_source": "none"}
        updates = await py_node.run(graph, state)
        assert "No hay datos cargados" in updates["error"]

    @pytest.mark.asyncio
    async def test_sandbox_unavailable_is_honest(self, monkeypatch):
        monkeypatch.setattr(
            "geo_copilot.agents.gis_agent.sandbox.SANDBOX_AVAILABLE", False
        )
        graph = MagicMock()
        state = {
            "query": "buffer 500m",
            "session_id": "",
            "active_data_source": "internal",
            "geojson": _fc(),
        }
        updates = await py_node.run(graph, state)
        assert "Linux/Docker" in updates["final_response"]
        assert updates["messages"][0]["success"] is False

    @pytest.mark.asyncio
    async def test_success_first_attempt_internal(self, monkeypatch):
        monkeypatch.setattr(
            "geo_copilot.agents.gis_agent.sandbox.SANDBOX_AVAILABLE", True
        )
        graph = MagicMock()
        result_gj = _fc({"buffered": True})
        graph.python_agent.process = AsyncMock(
            return_value=_resp(
                True,
                {
                    "geojson": result_gj,
                    "code": "gdf.buffer(500)",
                    "result_count": 1,
                    "operation": "buffer",
                    "original_count": 1,
                },
            )
        )
        state = {
            "query": "buffer 500m",
            "session_id": "",
            "active_data_source": "internal",
            "geojson": _fc(),
            "autonomous_mode": False,
        }
        updates = await py_node.run(graph, state)

        assert updates["geojson"] is result_gj
        assert updates["active_data_source"] == "internal"
        assert updates["has_external_data"] is False
        assert updates["retry_count"] == 0
        assert updates["messages"][0]["success"] is True

    @pytest.mark.asyncio
    async def test_success_external_sets_external_geojson(self, monkeypatch):
        monkeypatch.setattr(
            "geo_copilot.agents.gis_agent.sandbox.SANDBOX_AVAILABLE", True
        )
        graph = MagicMock()
        result_gj = _fc({"ok": 1})
        graph.python_agent.process = AsyncMock(
            return_value=_resp(True, {"geojson": result_gj, "result_count": 1})
        )
        state = {
            "query": "centroides",
            "session_id": "",
            "active_data_source": "external",
            "external_geojson": _fc(),
            "autonomous_mode": False,
        }
        updates = await py_node.run(graph, state)

        assert updates["external_geojson"] is result_gj
        assert updates["has_external_data"] is True
        assert updates["active_data_source"] == "external"

    @pytest.mark.asyncio
    async def test_previous_layer_propagated_as_internal(self, monkeypatch):
        monkeypatch.setattr(
            "geo_copilot.agents.gis_agent.sandbox.SANDBOX_AVAILABLE", True
        )
        graph = MagicMock()
        result_gj = _fc({"ok": 1})
        graph.python_agent.process = AsyncMock(
            return_value=_resp(True, {"geojson": result_gj, "result_count": 1})
        )
        # Sin active_data_source internal/external pero con previous_geojson.
        state = {
            "query": "haz buffer a la capa",
            "session_id": "",
            "active_data_source": "none",
            "previous_geojson": _fc(),
            "autonomous_mode": False,
        }
        updates = await py_node.run(graph, state)

        # 'previous' se propaga como 'internal' y limpia external_geojson.
        assert updates["active_data_source"] == "internal"
        assert updates["external_geojson"] is None
        assert updates["has_external_data"] is False

    @pytest.mark.asyncio
    async def test_failure_after_retries_with_correction(self, monkeypatch):
        """Ejercita el bucle retry + CodeCorrector: el código falla, se corrige,
        vuelve a fallar y termina con error_context poblado."""
        monkeypatch.setattr(
            "geo_copilot.agents.gis_agent.sandbox.SANDBOX_AVAILABLE", True
        )
        # CodeCorrector devuelve un código nuevo (distinto) → habilita el retry.
        fake_corrector = MagicMock()
        fake_corrector.correct_code = AsyncMock(return_value="codigo_corregido()")
        monkeypatch.setattr(py_node, "CodeCorrector", lambda llm: fake_corrector)

        graph = MagicMock()
        graph.llm = MagicMock()
        # Siempre falla, con código+traceback en data para el corrector.
        graph.python_agent.process = AsyncMock(
            return_value=_resp(
                False,
                {"code": "codigo_malo()", "traceback": "ValueError: x"},
                message="ejecución falló",
            )
        )
        state = {
            "query": "buffer 500m",
            "session_id": "",
            "active_data_source": "internal",
            "geojson": _fc(),
            "autonomous_mode": True,
            "max_retries": 1,  # → 2 intentos
        }
        updates = await py_node.run(graph, state)

        assert "error" in updates
        assert updates["error_context"]["attempts"] == 2
        assert updates["error_context"]["all_errors"]  # se acumularon
        # El corrector se invocó al menos una vez entre intentos.
        fake_corrector.correct_code.assert_awaited()
        assert updates["messages"][0]["success"] is False
