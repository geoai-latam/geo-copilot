"""Fase 2 del plan de remediación agéntica (T8–T16).

Cubre:
- R2.2: el generador SQL falla honesto sin LLM / ante error del LLM (el
  fallback template fue eliminado) — complementa test_sql_sanitization.
- R2.3: symbology marca la degradación ([DEGRADED]) cuando el diseñador falla.
- R2.4: la narrativa template declara la degradación cuando el LLM falla.
- R2.6: get_active_geojson sin cascada adivinadora.
- R3.1: validación SQL + LIMIT-cap robusto en el punto único de ejecución.
- R3.2: execute_sql_with_pool eliminado.
- R3.4: read_csv y compañía bloqueados en el sandbox.
- R3.5: el error del planner llega sanitizado.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.gis_agent.agent import _tope_sql


# ---------------------------------------------------------------------------
# R2.3 — symbology: degradación marcada
# ---------------------------------------------------------------------------
class TestSymbologyDegradacionMarcada:
    @pytest.mark.asyncio
    async def test_llm_falla_marca_degraded(self):
        from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("boom"))
        agent = SymbologyAgent(llm_client=llm)
        design = await agent._llm_design_symbology(
            query="colorea por uso",
            field_analysis={"uso": {"data_type": "categorical", "count": 10}},
            primary_geom="Polygon",
            sample_props=[{"uso": "residencial"}],
        )
        assert "[DEGRADED]" in design["reasoning"]
        assert design["symbology_type"] == "single_symbol"

    @pytest.mark.asyncio
    async def test_sin_llm_marca_degraded(self):
        from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

        agent = SymbologyAgent(llm_client=MagicMock())
        agent.llm_client = None  # el ctor crea un LLM real si le pasas None
        design = await agent._llm_design_symbology(
            query="q", field_analysis={}, primary_geom="Point", sample_props=[],
        )
        assert "[DEGRADED]" in design["reasoning"]

    @pytest.mark.asyncio
    async def test_json_invalido_marca_degraded(self):
        from geo_copilot.agents.symbology_agent.agent import SymbologyAgent

        llm = MagicMock()
        llm.chat = AsyncMock(return_value=MagicMock(content="no soy json"))
        agent = SymbologyAgent(llm_client=llm)
        design = await agent._llm_design_symbology(
            query="q", field_analysis={}, primary_geom="Point", sample_props=[],
        )
        assert "[DEGRADED]" in design["reasoning"]


# ---------------------------------------------------------------------------
# R2.4 — narrativa: fallback declarado
# ---------------------------------------------------------------------------
class TestNarrativaFallbackDeclarado:
    @pytest.mark.asyncio
    async def test_llm_falla_prefija_aviso(self):
        from geo_copilot.agents.insights_agent.narrative_generator import (
            NarrativeGenerator,
        )

        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=RuntimeError("boom"))
        gen = NarrativeGenerator(llm_client=llm)
        result = await gen.generate_narrative(
            "aggregation", {"stats": {"feature_count": 5}}, use_llm=True
        )
        assert result["generated_with"] == "template"
        assert result["narrative"].startswith("(Resumen de datos")

    @pytest.mark.asyncio
    async def test_template_directo_sin_aviso(self):
        from geo_copilot.agents.insights_agent.narrative_generator import (
            NarrativeGenerator,
        )

        gen = NarrativeGenerator(llm_client=None)
        result = await gen.generate_narrative(
            "aggregation", {"stats": {"feature_count": 5}}, use_llm=False
        )
        # Modo template pedido a propósito: sin aviso de degradación.
        assert not result["narrative"].startswith("(Resumen de datos")


# ---------------------------------------------------------------------------
# R2.6 — get_active_geojson honesto
# ---------------------------------------------------------------------------
class TestActiveGeojsonHonesto:
    def _ctx(self):
        from geo_copilot.orchestrator.conversation import ConversationContext
        return ConversationContext(session_id="t1")

    def test_external_declarado_sin_datos_devuelve_none(self):
        from geo_copilot.orchestrator.conversation import DataSource
        ctx = self._ctx()
        ctx._state.active_data_source = DataSource.EXTERNAL
        ctx._state.last_geojson = {"type": "FeatureCollection", "features": [{"x": 1}]}
        # ANTES: devolvía la capa interna (cascada). AHORA: None honesto.
        assert ctx.get_active_geojson() is None

    def test_sin_fuente_activa_devuelve_none(self):
        ctx = self._ctx()
        ctx._state.last_geojson = {"type": "FeatureCollection", "features": [{"x": 1}]}
        ctx._variables["external_geojson"] = {"type": "FeatureCollection", "features": []}
        # Sin fuente declarada NO se "devuelve lo que haya".
        assert ctx.get_active_geojson() is None

    def test_internal_con_datos_funciona(self):
        from geo_copilot.orchestrator.conversation import DataSource
        ctx = self._ctx()
        ctx._state.active_data_source = DataSource.INTERNAL
        gj = {"type": "FeatureCollection", "features": [{"x": 1}]}
        ctx._state.last_geojson = gj
        assert ctx.get_active_geojson() is gj


# ---------------------------------------------------------------------------
# R3.1 — validación + LIMIT cap robusto en _execute_sql
# ---------------------------------------------------------------------------
class _FakeConn:
    def __init__(self, recorder):
        self._rec = recorder

    def transaction(self, readonly=False):
        self._rec["readonly"] = readonly
        return _FakeCM(self)

    async def execute(self, sql):
        self._rec.setdefault("execs", []).append(sql)

    async def fetch(self, sql):
        self._rec["fetched_sql"] = sql
        return []

    async def fetchval(self, sql):
        # R0.6 (AUD-04): declara que `gis_readonly` está disponible. Antes este
        # doble no tenía `fetchval`; el AttributeError se tragaba y el código
        # continuaba con privilegios plenos, así que estos tests validaban los
        # guardrails sobre el camino DEGRADADO sin que nadie lo notara.
        return True


class _FakeCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False


class _FakePool:
    def __init__(self, recorder):
        self._rec = recorder

    def acquire(self):
        return _FakeCM(_FakeConn(self._rec))


def _gis_agent_with_fake_pool(recorder):
    from geo_copilot.agents.gis_agent.agent import GISAgent
    agent = GISAgent(llm_client=MagicMock())
    agent.db_pool = _FakePool(recorder)
    # S0.2: con `enforce` por defecto, un GISAgent sin semantic layer tiene
    # la allowlist vacía y rechaza todo (falla cerrado). Este test prueba
    # otra cosa, así que declara las tablas que usa.
    agent.sql_validator.allowed_tables = lambda: {"public.t"}
    return agent


# H19 (V5 F2): el tope sale de SQL_RESULT_LIMIT (10.000 por defecto), no de un
# 1000 escrito a mano. Se compara el FINAL exacto: "LIMIT 1000" era subcadena de
# "LIMIT 10000" y estos tests pasaban por casualidad.
class TestExecuteSqlGuardrails:
    @pytest.mark.asyncio
    async def test_update_rechazado_antes_de_tocar_bd(self):
        rec: dict = {}
        agent = _gis_agent_with_fake_pool(rec)
        with pytest.raises(ValueError, match="validador"):
            await agent._execute_sql("UPDATE catastro.lotes SET x = 1")
        assert "fetched_sql" not in rec  # nunca llegó a la BD

    @pytest.mark.asyncio
    async def test_sin_limit_se_envuelve_con_cap(self):
        rec: dict = {}
        agent = _gis_agent_with_fake_pool(rec)
        await agent._execute_sql("SELECT a FROM t")
        assert "SELECT * FROM (" in rec["fetched_sql"]
        assert rec["fetched_sql"].rstrip().rstrip(";").endswith(f"LIMIT {_tope_sql()}")

    @pytest.mark.asyncio
    async def test_limit_solo_en_subquery_no_evade_el_cap(self):
        # BYPASS VIEJO: la regex \bLIMIT\s+(\d+) veía el LIMIT anidado y no
        # capaba el exterior. Ahora la query se envuelve entera.
        rec: dict = {}
        agent = _gis_agent_with_fake_pool(rec)
        sql = "SELECT * FROM (SELECT a FROM t LIMIT 999999) sub"
        await agent._execute_sql(sql)
        assert rec["fetched_sql"].rstrip().endswith(f"LIMIT {_tope_sql()}")
        assert "AS _capped" in rec["fetched_sql"]

    @pytest.mark.asyncio
    async def test_limit_exterior_razonable_se_respeta(self):
        rec: dict = {}
        agent = _gis_agent_with_fake_pool(rec)
        await agent._execute_sql("SELECT a FROM t LIMIT 50")
        assert "LIMIT 50" in rec["fetched_sql"]
        assert "AS _capped" not in rec["fetched_sql"]

    @pytest.mark.asyncio
    async def test_limit_exterior_excesivo_se_capa(self):
        rec: dict = {}
        agent = _gis_agent_with_fake_pool(rec)
        await agent._execute_sql("SELECT a FROM t LIMIT 500000;")
        assert rec["fetched_sql"].rstrip().rstrip(";").endswith(f"LIMIT {_tope_sql()}")
        assert "500000" not in rec["fetched_sql"]

    @pytest.mark.asyncio
    async def test_readonly_transaction_sigue_activa(self):
        rec: dict = {}
        agent = _gis_agent_with_fake_pool(rec)
        await agent._execute_sql("SELECT a FROM t LIMIT 5")
        assert rec["readonly"] is True


# ---------------------------------------------------------------------------
# R3.2 — la pistola cargada fue eliminada
# ---------------------------------------------------------------------------
def test_execute_sql_with_pool_eliminado():
    from geo_copilot.agents.gis_agent.agent import GISAgent
    assert not hasattr(GISAgent, "execute_sql_with_pool")


# ---------------------------------------------------------------------------
# R3.4 — read_* bloqueados en el sandbox
# ---------------------------------------------------------------------------
class TestSandboxReadRemotoBloqueado:
    @pytest.fixture
    def sb(self):
        from geo_copilot.agents.gis_agent.sandbox import PythonSandbox
        return PythonSandbox()

    def _safe(self, sb, code):
        return sb.analyze_security(code)["is_safe"]

    def test_read_csv_url_bloqueado(self, sb):
        assert not self._safe(sb, "import pandas as pd\npd.read_csv('http://evil/x.csv')")

    def test_read_json_read_html_bloqueados(self, sb):
        assert not self._safe(sb, "import pandas as pd\npd.read_json('http://e/x')")
        assert not self._safe(sb, "import pandas as pd\npd.read_html('http://e')")

    def test_analitica_normal_sigue_pasando(self, sb):
        assert self._safe(sb, (
            "import geopandas as gpd\nfrom sklearn.cluster import DBSCAN\n"
            "import numpy as np\nlab = DBSCAN(eps=1).fit_predict(np.array([[0,0]]))\n"
        ))


# ---------------------------------------------------------------------------
# R3.5 — error del planner sanitizado
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_error_planner_no_fuga_detalle():
    from geo_copilot.orchestrator.planner import PlannerAgent

    llm = MagicMock()
    llm.chat = AsyncMock(
        side_effect=RuntimeError("FATAL: relation \"catastro.secreta\" C:\\ruta\\interna")
    )
    agent = PlannerAgent(llm_client=llm)
    resp = await agent.process("algo compuesto", {})
    assert resp.success is False
    assert "catastro.secreta" not in resp.message
    assert "C:\\ruta" not in resp.message
