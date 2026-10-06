"""Tests de ``build_router_system_prompt`` (regresión B3).

El template se renderizaba con ``str.format()``: cualquier ``{`` o ``}``
literal en los bloques runtime (p. ej. el ``json.dumps`` que
``_build_conversation_context`` inyecta como ejemplo de resultados
previos) rompía el render con KeyError/ValueError, y el ``except``
genérico del agente degradaba el routing en silencio.
"""

import json

from geo_copilot.agents.router_agent.prompts import (
    _SCHEMA_FALLBACK,
    build_router_system_prompt,
)


class TestBuildRouterSystemPrompt:
    def test_renders_with_plain_context(self):
        prompt = build_router_system_prompt(
            schema_info="tabla parcelas (geom, area)",
            conversation_context="Sin conversación previa.",
            services_context="",
        )
        assert "tabla parcelas (geom, area)" in prompt
        assert "Sin conversación previa." in prompt

    def test_braces_in_conversation_context_do_not_crash(self):
        """Regresión B3: json.dumps en el contexto contiene { }."""
        sample = {"nombre": "Suba", "area": 123.4, "tags": ["a", "b"]}
        context = f"Ejemplo: {json.dumps(sample, default=str)}"

        prompt = build_router_system_prompt(
            schema_info="x",
            conversation_context=context,
            services_context="",
        )

        # El JSON inyectado sobrevive tal cual (llaves incluidas).
        assert json.dumps(sample, default=str) in prompt

    def test_braces_in_all_runtime_blocks(self):
        prompt = build_router_system_prompt(
            schema_info='schema: {"tables": ["a"]}',
            conversation_context='SQL previo: SELECT \'{"k":1}\'::jsonb',
            services_context="servicio {raro} con llaves",
            external_data_context="geojson: {} vacío",
        )
        assert '{"tables": ["a"]}' in prompt
        assert "servicio {raro} con llaves" in prompt
        assert "geojson: {} vacío" in prompt

    def test_template_literal_json_examples_still_render(self):
        """Los ejemplos JSON propios del template ({{ }}) renderizan como { }."""
        prompt = build_router_system_prompt(
            schema_info="x",
            conversation_context="y",
            services_context="",
        )
        # El template define la salida esperada del LLM como JSON: tras el
        # render deben existir llaves simples (no dobles sin resolver).
        assert '"intent"' in prompt
        assert "{{" not in prompt

    def test_schema_fallback_applies_when_empty(self):
        prompt = build_router_system_prompt(
            schema_info="",
            conversation_context="y",
            services_context="",
        )
        assert _SCHEMA_FALLBACK in prompt
