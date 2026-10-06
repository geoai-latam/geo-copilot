"""Tests para DiscoveryAgent (post-refactor agentic 2026-05-25).

El antiguo `test_discovery_agent.py` testeaba `_heuristic_classify`,
`_clean_query`, `_build_search_params` — todas eliminadas. La nueva
arquitectura es 100% LLM-driven: `_llm_build_hub_plan` produce el plan
completo y `discover()` lo ejecuta + retry refinado.

Estos tests cubren:
- DiscoveryAgent sin LLM falla con RuntimeError (no hay modo offline)
- _plan_to_search_params traduce plan LLM → argumentos de arcgis_search_items
- _catalog_context_for_llm expone entidades y zonas como contexto
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.data_agent.discovery import (
    DiscoveryAgent,
    DiscoveryHints,
    DiscoveryResponse,
    _catalog_context_for_llm,
    _plan_to_search_params,
)


class TestNoLLMFailsFast:
    """Sin LLM, el agente NO cae a heurísticas — falla con error claro."""

    @pytest.mark.asyncio
    async def test_discover_without_llm_raises(self):
        agent = DiscoveryAgent(llm_client=None)
        with pytest.raises(RuntimeError, match="requiere un LLM"):
            await agent.discover("busca ortofotos de bogota")

    @pytest.mark.asyncio
    async def test_empty_query_returns_exploratory(self):
        """Caso especial: query vacía no necesita LLM — devuelve hint UI."""
        agent = DiscoveryAgent(llm_client=None)
        resp = await agent.discover("")
        assert isinstance(resp, DiscoveryResponse)
        assert resp.intent == "exploratory"
        assert resp.items == []


class TestPlanToSearchParams:
    """Traducción del plan LLM a argumentos de arcgis_search_items."""

    def test_full_plan(self):
        plan_step = {
            "text_query": "Cerinza",
            "service_types": ["Image Service"],
            "source_any": ["Instituto Geográfico Agustín Codazzi"],
            "tags_any": None,
        }
        params = _plan_to_search_params(plan_step, hints=DiscoveryHints())
        assert params["text_query"] == "Cerinza"
        assert params["service_types"] == ["Image Service"]
        assert params["source_any"] == ["Instituto Geográfico Agustín Codazzi"]
        assert "tags_any" not in params  # None → no se incluye
        assert params["only_loadable"] is True

    def test_hints_override_plan(self):
        """hints.service_types del panel UI gana sobre el plan del LLM."""
        plan_step = {"text_query": "test", "service_types": ["Image Service"]}
        hints = DiscoveryHints(service_types=["Feature Service"])
        params = _plan_to_search_params(plan_step, hints=hints)
        assert params["service_types"] == ["Feature Service"]

    def test_modified_after_triggers_sort(self):
        plan_step = {"text_query": "x", "modified_after": "2025/01/01"}
        params = _plan_to_search_params(plan_step, hints=DiscoveryHints())
        assert params["modified_after"] == "2025/01/01"
        assert params["sort"] == "-modified"

    def test_empty_text_query_not_emitted(self):
        params = _plan_to_search_params({"text_query": ""}, hints=DiscoveryHints())
        assert "text_query" not in params


class TestCatalogContext:
    """El catálogo debe llegar al LLM para que tome decisiones informadas."""

    def test_colombia_region_includes_entities(self):
        ctx = _catalog_context_for_llm("colombia")
        assert "Colombia" in ctx
        assert "IGAC" in ctx
        assert "DANE" in ctx
        # Las zonas también
        assert "bogota" in ctx.lower() or "bogotá" in ctx.lower()

    def test_global_mode_no_bias(self):
        ctx = _catalog_context_for_llm("global")
        assert "GLOBAL" in ctx
        # No debe contener instrucciones de aplicar source_any
        assert "source_any" in ctx and "no apliques" in ctx.lower()

    def test_unknown_region_treated_as_global(self):
        ctx = _catalog_context_for_llm("xz_unknown")
        assert "GLOBAL" in ctx


class TestDiscoverWithMockedLLM:
    """Smoke test del flujo completo con LLM mockeado."""

    @pytest.mark.asyncio
    async def test_llm_plan_drives_search(self, monkeypatch):
        """El LLM devuelve un plan; el agente lo ejecuta y rankea."""
        # Mock LLM que devuelve un plan válido
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="""{
            "primary": {
                "text_query": "Cerinza",
                "service_types": ["Image Service"],
                "source_any": ["Instituto Geográfico Agustín Codazzi"]
            },
            "place_focus": "Cerinza",
            "intent_label": "imagery_focused",
            "alternatives": [],
            "reasoning": "test"
        }""")

        # Mock del servidor MCP de ArcGIS para no pegar contra el Hub real
        from geo_copilot.agents.data_agent import discovery
        mock_search = AsyncMock(return_value=([], []))
        monkeypatch.setattr(discovery, "buscar_en_hub", mock_search)

        agent = DiscoveryAgent(llm_client=mock_llm)
        resp = await agent.discover("busca ortofotos de cerinza")

        # el servidor recibió los params del plan del LLM
        assert mock_search.called
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["text_query"] == "Cerinza"
        assert "Image Service" in call_kwargs["service_types"]
        assert resp.intent == "imagery_focused"

    @pytest.mark.asyncio
    async def test_invalid_llm_plan_raises(self, monkeypatch):
        """LLM devolviendo JSON malformado → RuntimeError, sin fallback."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="not json at all")

        agent = DiscoveryAgent(llm_client=mock_llm)
        with pytest.raises(RuntimeError, match="plan válido"):
            await agent.discover("busca cualquier cosa")


class TestAvisoDeAutoridad:
    """Rama arcgis-busqueda: la Malla Vial de la Secretaría de Movilidad (cuenta SecretariaMovilidad,
    créditos «Secretaría Distrital de Movilidad») salía con «no proviene de una cuenta institucional»
    porque la lista fija de cuentas no la conoce. Se avisa solo si no hay NADA que diga de quién es."""

    @staticmethod
    async def _aviso(monkeypatch, **hechos) -> bool:
        from geo_copilot.agents.data_agent import discovery
        from geo_copilot.agents.data_agent.hub_items import HubItem

        llm = MagicMock()
        llm.chat = AsyncMock(return_value=MagicMock(content='{"primary": {"text_query": "malla vial"}, '
                                                            '"intent_label": "topic_focused", "alternatives": []}'))
        item = HubItem(id="a", source="hub", org="x", title="Malla Vial", description="", service_type="FeatureServer",
                       service_url="https://s/x/FeatureServer", owner="cuenta_desconocida", **hechos)
        monkeypatch.setattr(discovery, "buscar_en_hub", AsyncMock(return_value=([item], [])))
        return (await DiscoveryAgent(llm_client=llm).discover("malla vial")).authority_warning

    @pytest.mark.asyncio
    async def test_con_creditos_declarados_no_se_avisa(self, monkeypatch):
        assert await self._aviso(monkeypatch, credits="Secretaría Distrital de Movilidad") is False

    @pytest.mark.asyncio
    async def test_sin_creditos_ni_cuenta_conocida_se_avisa(self, monkeypatch):
        assert await self._aviso(monkeypatch) is True


class TestJuicioDelLLM:
    """El LLM juzga los candidatos con sus hechos: ordena los que sirven y, si ninguno sirve, pide otra
    búsqueda (pendientes 3 y 4 tras la V5 de «malla vial Bogotá» y «ríos de Colombia»)."""

    PLAN = ('{"primary": {"text_query": "vías Bogotá"}, "place_focus": "Bogotá", '
            '"intent_label": "topic_focused", "alternatives": []}')

    @staticmethod
    def _item(i, titulo, **k):
        from geo_copilot.agents.data_agent.hub_items import HubItem

        return HubItem(id=i, source="arcgis_online", org="x", title=titulo, description="", service_type="FeatureServer",
                       service_url=f"https://s/{i}/FeatureServer", **k)

    async def _discover(self, monkeypatch, respuestas, busquedas):
        import re

        from geo_copilot.agents.data_agent import discovery

        cola = list(respuestas)

        async def chat(mensajes, **_):
            r = cola.pop(0)
            # «#Título» = el número que ese título tiene en la lista que el juez RECIBE (ordenada por hechos)
            for titulo in re.findall(r"#([^#]+)#", r):
                n = next(ln.split(".")[0] for ln in mensajes[-1].content.splitlines() if f". {titulo} ·" in ln)
                r = r.replace(f"#{titulo}#", n)
            return MagicMock(content=r)

        llm = MagicMock()
        llm.chat = chat
        pedidas = []

        async def buscar(**p):
            pedidas.append(p.get("text_query"))
            return busquedas[min(len(pedidas), len(busquedas)) - 1], []

        monkeypatch.setattr(discovery, "buscar_en_hub", buscar)
        monkeypatch.setattr(discovery, "_extract_region_from_query", AsyncMock(return_value=None))
        return await DiscoveryAgent(llm_client=llm).discover("vías de Bogotá"), pedidas

    @pytest.mark.asyncio
    async def test_ordena_los_que_sirven_primero_y_deja_el_resto_despues(self, monkeypatch):
        a = self._item("a", "Velocidades Bitcarrier Bogotá", views=50000)
        b = self._item("b", "Malla Vial Integral Bogota D_C", views=60000)
        c = self._item("c", "Carril SITP Bogotá", views=5000)
        r, pedidas = await self._discover(monkeypatch, [
            self.PLAN, '{"relevantes": [#Malla Vial Integral Bogota D_C#], "otra_busqueda": null, "razon": "La malla vial de Movilidad es la red vial."}',
        ], [[a, b, c]])
        assert r.items[0].id == "b" and {it.id for it in r.items} == {"a", "b", "c"}
        assert r.criterio.startswith("La malla vial") and r.relevantes == 1 and r.otras_busquedas == []
        assert pedidas == ["vías Bogotá"]

    @pytest.mark.asyncio
    async def test_si_ninguno_sirve_busca_otra_vez_y_juzga_todo_junto(self, monkeypatch):
        a = self._item("a", "Velocidades Bitcarrier Bogotá")
        b = self._item("b", "Malla Vial Integral Bogota D_C")
        r, pedidas = await self._discover(monkeypatch, [
            self.PLAN,
            '{"relevantes": [], "otra_busqueda": "malla vial Bogota", "razon": "Nada es la red vial."}',
            '{"relevantes": [#Malla Vial Integral Bogota D_C#], "otra_busqueda": null, "razon": "Con «malla vial» apareció la oficial."}',
        ], [[a], [b]])
        assert pedidas == ["vías Bogotá", "malla vial Bogota"]
        assert [it.id for it in r.items] == ["b", "a"] and r.otras_busquedas == ["malla vial Bogota"]

    @pytest.mark.asyncio
    async def test_no_repite_una_busqueda_ni_pasa_del_maximo(self, monkeypatch):
        from geo_copilot.agents.data_agent import discovery

        a = self._item("a", "Algo")
        ninguno = '{{"relevantes": [], "otra_busqueda": "{q}", "razon": "nada"}}'
        r, pedidas = await self._discover(monkeypatch, [
            self.PLAN, ninguno.format(q="uno"), ninguno.format(q="dos"), ninguno.format(q="tres"),
        ], [[a]])
        assert pedidas == ["vías Bogotá", "uno", "dos"][: 1 + discovery.MAX_REBUSQUEDAS]
        r, pedidas = await self._discover(monkeypatch, [self.PLAN, ninguno.format(q="Vías Bogotá")], [[a]])
        assert pedidas == ["vías Bogotá"]  # la misma búsqueda no se repite

    @pytest.mark.asyncio
    async def test_sin_juicio_queda_el_orden_por_hechos_y_no_se_inventa_criterio(self, monkeypatch):
        a = self._item("a", "Malla vial Bogotá", views=10)
        b = self._item("b", "Malla vial Bogotá oficial", views=60000, credits="Movilidad")
        r, _ = await self._discover(monkeypatch, [self.PLAN, "no es json"], [[a, b]])
        assert r.items[0].id == "b" and r.criterio is None and r.relevantes is None

    @pytest.mark.asyncio
    async def test_indices_fuera_de_rango_o_repetidos_se_ignoran(self, monkeypatch):
        a, b = self._item("a", "Uno"), self._item("b", "Dos")
        r, _ = await self._discover(monkeypatch, [
            self.PLAN, '{"relevantes": [#Dos#, #Dos#, 9, "x", 0], "otra_busqueda": null, "razon": "ok"}'], [[a, b]])
        assert r.relevantes == 1 and r.items[0].id == "b"
