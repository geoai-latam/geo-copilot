"""F2.2: parametrización de session_region (vs Colombia hardcodeada).

VALIDACIÓN CRÍTICA: una consulta sobre otra región NO se ancla en silencio a
Colombia. El catálogo solo define `colombia` y `global`; una región explícita
sin catálogo (p.ej. 'peru') debe degradar a búsqueda GLOBAL/neutral, NUNCA
colapsar a la región por defecto.

Distinción clave que se prueba:
  - region=None  (sin especificar)        → default CONFIGURADO (correcto, no es hardcode)
  - region='peru'(explícito, sin catálogo)→ 'global' neutral (el kill switch)
"""

import pytest

from geo_copilot.agents.data_agent.catalogo_regiones import (
    REGIONS,
    get_active_region,
)
from geo_copilot.agents.data_agent.discovery import (
    DiscoveryHints,
    _catalog_context_for_llm,
    _resolve_region,
)
from geo_copilot.agents.router_agent.prompts import (
    build_router_system_prompt,
    format_region_sources,
)
from geo_copilot.api.models import QueryRequest


# ---------------------------------------------------------------------------
# Kill switch: _resolve_region
# ---------------------------------------------------------------------------
def test_resolve_region_known_key():
    assert _resolve_region(DiscoveryHints(region="colombia")) == "colombia"


def test_resolve_region_global_mode():
    assert _resolve_region(DiscoveryHints(global_mode=True)) == "global"
    assert _resolve_region(DiscoveryHints(region="global")) == "global"


def test_resolve_region_explicit_unknown_goes_global_not_colombia():
    # CRÍTICO: el usuario pidió EXPLÍCITAMENTE 'peru' (sin catálogo). Debe ir a
    # 'global' neutral, jamás a la región por defecto (colombia).
    assert "peru" not in REGIONS  # premisa: no hay catálogo Perú
    resolved = _resolve_region(DiscoveryHints(region="peru"))
    assert resolved == "global"
    assert resolved != "colombia"


def test_resolve_region_absent_uses_configured_default():
    # Sin especificar región → default configurado del despliegue (NO regresión).
    assert _resolve_region(DiscoveryHints()) == get_active_region()


# ---------------------------------------------------------------------------
# Contexto LLM del catálogo: neutral cuando no hay región
# ---------------------------------------------------------------------------
def test_catalog_context_unknown_region_is_neutral():
    ctx = _catalog_context_for_llm("peru")
    assert "GLOBAL" in ctx
    assert "ANCLAJE GEOGRÁFICO OBLIGATORIO" not in ctx
    assert "Colombia" not in ctx


def test_catalog_context_global_is_neutral():
    ctx = _catalog_context_for_llm("global")
    assert "Colombia" not in ctx


def test_catalog_context_explicit_colombia_anchors():
    # Cuando SÍ se pide colombia explícito, ancla (comportamiento correcto).
    ctx = _catalog_context_for_llm("colombia")
    assert "Colombia" in ctx
    assert "ANCLAJE" in ctx


# ---------------------------------------------------------------------------
# Router prompt: fuentes externas según región (de-hardcode)
# ---------------------------------------------------------------------------
def test_region_sources_unknown_region_neutral():
    # CRÍTICO: sesión 'peru' → el router NO presenta IGAC/datos.gov.co/Colombia.
    block = format_region_sources("peru")
    assert "IGAC" not in block
    assert "datos.gov.co" not in block
    assert "Colombia" not in block


def test_region_sources_global_neutral():
    block = format_region_sources("global")
    assert "Colombia" not in block and "IGAC" not in block


def test_region_sources_absent_uses_configured_default():
    # None → región configurada (colombia en este despliegue): sin regresión,
    # se mantienen las fuentes colombianas.
    block = format_region_sources(None)
    if get_active_region() == "colombia":
        assert "Colombia" in block


def test_router_prompt_peru_session_not_colombia_anchored():
    # End-to-end del prompt: una sesión 'peru' no fija fuentes colombianas.
    prompt = build_router_system_prompt(
        schema_info="x",
        conversation_context="y",
        services_context="",
        session_region="peru",
    )
    # El bloque de fuentes externas es neutral; no nombra entidades colombianas.
    assert "IGAC" not in prompt
    assert "Catastro Bogotá" not in prompt


# ---------------------------------------------------------------------------
# Plumbing: el campo existe y degrada con gracia
# ---------------------------------------------------------------------------
def test_query_request_accepts_session_region():
    req = QueryRequest(query="hola", session_region="peru")
    assert req.session_region == "peru"


def test_query_request_session_region_optional():
    req = QueryRequest(query="hola")
    assert req.session_region is None


def test_graph_state_and_process_have_session_region():
    from geo_copilot.orchestrator.graph import GeoAgentGraph, GraphState

    assert "session_region" in GraphState.__annotations__
    import inspect

    sig = inspect.signature(GeoAgentGraph.process)
    assert "session_region" in sig.parameters


# ---------------------------------------------------------------------------
# Narrativa: default neutral (sin "de Colombia")
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_narrative_default_context_is_neutral():
    from geo_copilot.agents.insights_agent.narrative_generator import (
        NarrativeGenerator,
    )

    captured = {}

    class _FakeResp:
        content = "narrativa de prueba"

    class _FakeLLM:
        async def chat(self, messages):
            captured["content"] = messages[0].content
            return _FakeResp()

    gen = NarrativeGenerator(llm_client=_FakeLLM())
    await gen._generate_with_llm(
        analysis_type="generic",
        data={"count": 3},
        context=None,  # sin contexto → default
        style=gen.default_style,
    )
    assert "de Colombia" not in captured["content"]
    assert "territoriales de Colombia" not in captured["content"]
