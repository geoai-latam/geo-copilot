"""
Tests A2A — validan que la comunicación cross-agent es REAL.

"Real" significa que un agente, mid-execution, invoca un método público de
otro agente y el resultado de esa llamada CAMBIA el comportamiento o la
salida del agente origen. No es "lectura del state compartido", no es
"prompt con contexto extendido" — es una llamada Python explícita
mediada por el ``AgentHub`` con telemetría.

Casos cubiertos:

1. ``test_lookup_entity_exact_match`` — el caso base: existe, fuzzy no
   se dispara.
2. ``test_lookup_entity_alias`` — match por alias.
3. ``test_lookup_entity_fuzzy_typo_returns_suggestion`` — el caso clave:
   ``constsrucciones`` → DataAgent sugiere ``construcciones``.
4. ``test_lookup_entity_unknown_returns_empty_suggestions`` — no hay
   match razonable: devuelve sin sugerencias para que el caller no
   adivine.
5. ``test_agent_hub_records_call_telemetry`` — el hub deja log de cada
   call con caller/target/method/duración. Permite que producción
   audite quién consulta a quién.
6. ``test_agent_hub_rejects_loops`` — A→B→A se detecta y rechaza para
   no enredar.
7. ``test_agent_hub_handles_missing_target`` — call a target no
   registrado retorna fallo explícito sin crash.
8. ``test_sql_generator_uses_a2a_suggestion_in_prompt`` — el caso
   end-to-end: cuando el SQLGenerator pide validar una entidad con
   typo, el hint con la sugerencia llega al system prompt del LLM.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from geo_copilot.agents.data_agent import DataAgent
from geo_copilot.agents.gis_agent import GISAgent
from geo_copilot.agents.gis_agent.sql_generator import SQLGenerator
from geo_copilot.agents.insights_agent import InsightsAgent
from geo_copilot.agents.python_agent import PythonAgent
from geo_copilot.agents.symbology_agent import SymbologyAgent
from geo_copilot.orchestrator.agent_hub import AgentHub
from geo_copilot.semantic.layer import SemanticLayer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def semantic_yaml():
    """YAML con 3 entidades — construcciones, lotes, vias — y aliases."""
    config = {
        "version": "1.0",
        "entities": {
            "construcciones": {
                "description": "Edificaciones registradas en el catastro",
                "aliases": ["construccion", "edificio", "edificios"],
                "table": "construcciones",
                "schema": "catastro",
                "geometry_column": "geom",
                "geometry_type": "MULTIPOLYGON",
                "srid": 4326,
                "fields": {
                    "id": {"column": "id", "type": "integer", "primary_key": True},
                    "uso_suelo": {"column": "uso_suelo", "type": "string"},
                    "area_m2": {"column": "area_m2", "type": "float"},
                },
            },
            "lotes": {
                "description": "Predios catastrales",
                "aliases": ["lote", "predio", "predios"],
                "table": "lotes",
                "schema": "catastro",
                "geometry_column": "geom",
                "geometry_type": "MULTIPOLYGON",
                "srid": 4326,
                "fields": {
                    "id": {"column": "id", "type": "integer", "primary_key": True},
                },
            },
            "vias": {
                "description": "Red vial",
                "aliases": ["via", "carretera", "calle"],
                "table": "vias",
                "schema": "infraestructura",
                "geometry_column": "geom",
                "geometry_type": "MULTILINESTRING",
                "srid": 4326,
                "fields": {
                    "id": {"column": "id", "type": "integer", "primary_key": True},
                },
            },
        },
        "relationships": {},
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False, encoding="utf-8",
    ) as f:
        yaml.dump(config, f)
        path = f.name
    yield path
    Path(path).unlink()


@pytest.fixture
def semantic_layer(semantic_yaml):
    return SemanticLayer(semantic_yaml)


@pytest.fixture
def data_agent(semantic_layer):
    # llm_client=None para no requerir LLM real.
    agent = DataAgent(semantic_layer=semantic_layer, llm_client=MagicMock())
    return agent


@pytest.fixture
def hub(data_agent):
    h = AgentHub()
    h.register("data_agent", data_agent)
    return h


# ---------------------------------------------------------------------------
# DataAgent.lookup_entity — unit tests del método A2A
# ---------------------------------------------------------------------------


class TestLookupEntity:
    """Capacidad pública del DataAgent que otros agentes invocan vía hub."""

    @pytest.mark.asyncio
    async def test_lookup_entity_exact_match(self, data_agent):
        """Match exacto por nombre canónico."""
        r = await data_agent.lookup_entity("construcciones")
        assert r.exists is True
        assert r.canonical_name == "construcciones"
        assert r.table == "catastro.construcciones"
        assert "uso_suelo" in r.fields
        assert r.suggestions == []

    @pytest.mark.asyncio
    async def test_lookup_entity_alias(self, data_agent):
        """Match por alias — devuelve nombre canónico."""
        r = await data_agent.lookup_entity("edificio")
        assert r.exists is True
        assert r.canonical_name == "construcciones"

    @pytest.mark.asyncio
    async def test_lookup_entity_case_insensitive(self, data_agent):
        """Match canónico sin importar case del input."""
        r = await data_agent.lookup_entity("CONSTRUCCIONES")
        assert r.exists is True
        assert r.canonical_name == "construcciones"

    @pytest.mark.asyncio
    async def test_lookup_entity_fuzzy_typo_returns_suggestion(self, data_agent):
        """**El caso clave de A2A real**: typo → sugerencia."""
        r = await data_agent.lookup_entity("constsrucciones")  # typo
        assert r.exists is False
        # La sugerencia más cercana debe ser 'construcciones'.
        assert "construcciones" in r.suggestions, (
            f"esperaba sugerencia 'construcciones' para typo "
            f"'constsrucciones', got {r.suggestions}"
        )

    @pytest.mark.asyncio
    async def test_lookup_entity_unknown_returns_empty_suggestions(self, data_agent):
        """Entidad totalmente desconocida → sin sugerencias bobas."""
        r = await data_agent.lookup_entity("zxqwertyuiop")
        assert r.exists is False
        assert r.suggestions == []

    @pytest.mark.asyncio
    async def test_lookup_entity_no_semantic_layer(self):
        """Sin semantic layer, devuelve exists=False sin crash."""
        agent = DataAgent(semantic_layer=None, llm_client=MagicMock())
        r = await agent.lookup_entity("cualquier_cosa")
        assert r.exists is False
        assert r.suggestions == []

    @pytest.mark.asyncio
    async def test_lookup_entity_empty_string(self, data_agent):
        """Defensa de entrada: string vacío no crashea."""
        r = await data_agent.lookup_entity("")
        assert r.exists is False
        assert r.suggestions == []

    @pytest.mark.asyncio
    async def test_lookup_entity_whitespace_only(self, data_agent):
        """Defensa de entrada: solo espacios trata como empty."""
        r = await data_agent.lookup_entity("   ")
        assert r.exists is False
        assert r.query == ""  # strip aplicado

    @pytest.mark.asyncio
    async def test_lookup_entity_none_does_not_crash(self, data_agent):
        """Defensa de entrada: None no crashea (caller pasa por accidente)."""
        # type: ignore[arg-type] — testeo intencional de input inválido.
        r = await data_agent.lookup_entity(None)  # type: ignore[arg-type]
        assert r.exists is False
        assert r.suggestions == []

    @pytest.mark.asyncio
    async def test_lookup_entity_trim_whitespace(self, data_agent):
        """Espacios envolventes se ignoran — query=construcciones igual."""
        r = await data_agent.lookup_entity("  construcciones  ")
        assert r.exists is True
        assert r.canonical_name == "construcciones"

    @pytest.mark.asyncio
    async def test_lookup_entity_cutoff_clamped_above_1(self, data_agent):
        """suggestion_cutoff > 1.0 se clampa a 1.0 (no crash en difflib)."""
        # Con cutoff=1.0, solo match perfecto: 'construc' NO matches.
        r = await data_agent.lookup_entity("construc", suggestion_cutoff=1.5)
        assert r.exists is False
        # Sin perfect match, suggestions vacías a cutoff=1.0.
        assert r.suggestions == []

    @pytest.mark.asyncio
    async def test_lookup_entity_cutoff_clamped_below_0(self, data_agent):
        """suggestion_cutoff < 0 se clampa a 0 (difflib acepta todo)."""
        # cutoff=0 → cualquier match ≥0 score se considera. Typo lejano
        # debe devolver al menos 1 sugerencia.
        r = await data_agent.lookup_entity("constr", suggestion_cutoff=-1.0)
        assert r.exists is False
        # Algún match debe aparecer (no necesariamente construcciones,
        # pero la lista no debe estar vacía si hay similitud >0).
        assert len(r.suggestions) >= 1

    @pytest.mark.asyncio
    async def test_lookup_entity_max_suggestions_clamped(self, data_agent):
        """max_suggestions clamped a [1, 10]; respetar el límite efectivo."""
        # Pedimos 100 — debería clampar a 10. Con solo 3 entidades en el
        # fixture, devolverá ≤3 igual.
        r = await data_agent.lookup_entity("constr", max_suggestions=100)
        assert len(r.suggestions) <= 3  # límite real del fixture
        # Pedimos 0 — clampa a 1.
        r = await data_agent.lookup_entity("constr", max_suggestions=0)
        # No verificamos que sea exactamente 1 (depende de matches),
        # pero NO debe crashear.

    @pytest.mark.asyncio
    async def test_lookup_entity_uses_public_semantic_api(self, data_agent):
        """Confirmar que lookup_entity NO accede a ``_entities`` privado.
        Espiamos ``list_entities`` y ``get_entity`` para asegurar que
        usamos solo la API pública del SemanticLayer.
        """
        from unittest.mock import patch
        original_list = data_agent.semantic_layer.list_entities
        with patch.object(
            data_agent.semantic_layer, "list_entities",
            side_effect=original_list,
        ) as mock_list:
            await data_agent.lookup_entity("typoxyz")  # path fuzzy
            # Si llegamos al paso fuzzy, list_entities() DEBE haberse llamado.
            assert mock_list.call_count >= 1


# ---------------------------------------------------------------------------
# AgentHub — telemetría, anti-loop, error handling
# ---------------------------------------------------------------------------


class TestAgentHub:
    """El hub debe ser observable y robusto contra mal uso."""

    @pytest.mark.asyncio
    async def test_call_records_telemetry(self, hub):
        """Cada call queda registrado en call_log con metadata útil."""
        ok, result = await hub.call(
            caller="gis_agent",
            target="data_agent",
            method="lookup_entity",
            name="construcciones",
        )
        assert ok is True
        assert result.exists is True

        assert len(hub.call_log) == 1
        rec = hub.call_log[0]
        assert rec.caller == "gis_agent"
        assert rec.target == "data_agent"
        assert rec.method == "lookup_entity"
        assert rec.success is True
        assert rec.error is None
        assert rec.duration_ms >= 0
        assert "name=" in rec.args_summary

    @pytest.mark.asyncio
    async def test_call_to_missing_target_returns_failure(self, hub):
        """Target no registrado → (False, error_msg), sin crash."""
        ok, result = await hub.call(
            caller="x", target="nonexistent_agent", method="foo",
        )
        assert ok is False
        assert "no registrado" in result
        # También se loguea para auditoría.
        assert len(hub.call_log) == 1
        assert hub.call_log[0].success is False

    @pytest.mark.asyncio
    async def test_call_to_missing_method_returns_failure(self, hub):
        """Target registrado pero método inexistente → fallo explícito."""
        ok, result = await hub.call(
            caller="x", target="data_agent", method="no_existe_este_metodo",
        )
        assert ok is False
        assert "no existe" in result

    @pytest.mark.asyncio
    async def test_call_method_that_raises_is_captured(self, hub):
        """Si el método invocado raisea, el hub lo captura sin crash."""
        # Inyectar un agente que rompe a propósito.
        class BoomAgent:
            async def explode(self):
                raise RuntimeError("boom")
        hub.register("boom", BoomAgent())

        ok, result = await hub.call(caller="x", target="boom", method="explode")
        assert ok is False
        assert "boom" in result
        rec = hub.call_log[-1]
        assert rec.success is False
        assert rec.error and "boom" in rec.error

    @pytest.mark.asyncio
    async def test_hub_rejects_loops(self, hub):
        """A → B → A debe rechazarse para no enredar."""
        # Agente que llama de vuelta a 'starter'.
        class CircularAgent:
            def __init__(self, hub):
                self.hub = hub
            async def bounce_back(self):
                return await self.hub.call(
                    caller="circular", target="starter", method="kick",
                )
        circular = CircularAgent(hub)
        hub.register("circular", circular)

        class StarterAgent:
            def __init__(self, hub):
                self.hub = hub
            async def kick(self):
                return await self.hub.call(
                    caller="starter", target="circular", method="bounce_back",
                )
        starter = StarterAgent(hub)
        hub.register("starter", starter)

        # starter.kick() llama a circular.bounce_back() que intenta llamar de
        # vuelta a starter.kick() — el hub debe atrapar el loop.
        ok, result = await hub.call(
            caller="test", target="starter", method="kick",
        )
        # El call exterior puede o no tener éxito según la implementación
        # del inner, pero AL MENOS UNA entrada del log debe marcar loop.
        loop_detected = any(
            r.success is False and r.error and "loop" in r.error
            for r in hub.call_log
        )
        assert loop_detected, f"esperaba que el hub detectara loop, log={hub.call_log}"

    @pytest.mark.asyncio
    async def test_recent_calls_limit(self, hub):
        """``recent_calls(n)`` devuelve las últimas n entradas."""
        for i in range(5):
            await hub.call(
                caller=f"c{i}", target="data_agent", method="lookup_entity",
                name=f"x{i}",
            )
        recent = hub.recent_calls(limit=3)
        assert len(recent) == 3
        assert [r.caller for r in recent] == ["c2", "c3", "c4"]


# ---------------------------------------------------------------------------
# Integración end-to-end: SQLGenerator usa A2A para detectar typo
# ---------------------------------------------------------------------------


class TestSQLGeneratorA2AIntegration:
    """End-to-end: el SQLGenerator consulta al DataAgent (vía hub) ANTES
    de generar SQL y la sugerencia A2A llega al prompt del LLM.

    Esto valida que A2A es REAL: si lo desactivamos (sin hub) el prompt no
    contiene los hints; con hub, sí.
    """

    @pytest.mark.asyncio
    async def test_a2a_suggestion_lands_in_system_prompt(self, semantic_layer, hub):
        """Caso clave. Query con typo + entidades=['constsrucciones'] →
        el system prompt del LLM debe contener la sugerencia 'construcciones'.
        """
        captured_messages: list = []

        async def fake_chat(messages, **kwargs):
            captured_messages.extend(messages)
            # Respuesta dummy del LLM — solo nos importa el prompt que recibió.
            mock_response = MagicMock()
            mock_response.content = "SELECT 1;"
            return mock_response

        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=fake_chat)

        gen = SQLGenerator(
            semantic_layer=semantic_layer, llm_client=llm, agent_hub=hub,
        )
        a2a_log: list = []
        await gen.generate(
            query="cuéntame las constsrucciones",
            entities=["constsrucciones"],
            context={"limit": 100},
            a2a_log=a2a_log,
        )

        # 1. El A2A se ejecutó: hay log entry.
        assert len(a2a_log) == 1, f"esperaba 1 A2A call, got {a2a_log}"
        log_entry = a2a_log[0]
        assert log_entry["from"] == "sql_generator"
        assert log_entry["to"] == "data_agent"
        assert log_entry["method"] == "lookup_entity"
        assert log_entry["query"] == "constsrucciones"
        assert log_entry["exists"] is False
        assert "construcciones" in log_entry["suggestions"]

        # 2. El system prompt del LLM contiene la sugerencia.
        system_msgs = [m for m in captured_messages if m.role == "system"]
        assert len(system_msgs) == 1
        system_content = system_msgs[0].content
        assert "constsrucciones" in system_content
        assert "construcciones" in system_content
        assert "DataAgent" in system_content, (
            "el prompt debe mencionar al DataAgent para que el LLM entienda "
            "que viene de validación cross-agent"
        )

    @pytest.mark.asyncio
    async def test_no_hub_no_a2a_block_in_prompt(self, semantic_layer):
        """Sin hub configurado, el prompt NO debe traer el bloque A2A —
        prueba contraprueba: confirma que el cambio es atribuible al hub.
        """
        captured_messages: list = []

        async def fake_chat(messages, **kwargs):
            captured_messages.extend(messages)
            mock_response = MagicMock()
            mock_response.content = "SELECT 1;"
            return mock_response

        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=fake_chat)

        gen = SQLGenerator(
            semantic_layer=semantic_layer, llm_client=llm, agent_hub=None,
        )
        a2a_log: list = []
        await gen.generate(
            query="cuéntame las constsrucciones",
            entities=["constsrucciones"],
            context={"limit": 100},
            a2a_log=a2a_log,
        )

        # No hubo A2A: log vacío y prompt sin bloque A2A.
        assert a2a_log == []
        system_msgs = [m for m in captured_messages if m.role == "system"]
        assert "VALIDACIÓN A2A" not in system_msgs[0].content
        # La palabra 'constsrucciones' tampoco debería aparecer en el system
        # prompt — sí podría estar en el user prompt pero NO destacada como
        # corrección.
        assert "DataAgent sugiere" not in system_msgs[0].content

    @pytest.mark.asyncio
    async def test_a2a_existing_entity_does_not_inject_correction(
        self, semantic_layer, hub,
    ):
        """Para entidades que SÍ existen, el bloque A2A no debe sugerir
        correcciones (sí confirma la existencia)."""
        captured_messages: list = []

        async def fake_chat(messages, **kwargs):
            captured_messages.extend(messages)
            mock_response = MagicMock()
            mock_response.content = "SELECT 1;"
            return mock_response

        llm = MagicMock()
        llm.chat = AsyncMock(side_effect=fake_chat)

        gen = SQLGenerator(
            semantic_layer=semantic_layer, llm_client=llm, agent_hub=hub,
        )
        a2a_log: list = []
        await gen.generate(
            query="todas las construcciones",
            entities=["construcciones"],
            context={"limit": 100},
            a2a_log=a2a_log,
        )

        assert len(a2a_log) == 1
        assert a2a_log[0]["exists"] is True
        assert a2a_log[0]["suggestions"] == []

        system_content = [m for m in captured_messages if m.role == "system"][0].content
        # NO debe sugerir correcciones, pero sí confirmar la existencia.
        assert "construcciones" in system_content
        assert "NO EXISTE" not in system_content


# ---------------------------------------------------------------------------
# A2A capability #2: InsightsAgent.evaluate_visualization_fit
# Validar que SymbologyAgent consulta y respeta el override.
# ---------------------------------------------------------------------------


class TestEvaluateVisualizationFit:
    """Reglas determinísticas: ¿la viz elegida encaja con los datos?"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("viz,geom,expected_appropriate", [
        ("heatmap", "Point", True),
        ("heatmap", "Polygon", False),
        ("cluster", "Point", True),
        ("cluster", "Polygon", False),
        ("choropleth", "Polygon", True),
        ("choropleth", "Point", False),
        ("graduated_symbols", "Point", True),
        ("graduated_symbols", "Polygon", False),
        ("single_symbol", "Point", True),
        ("single_symbol", "Polygon", True),
    ])
    async def test_geometry_compatibility(self, viz, geom, expected_appropriate):
        agent = InsightsAgent(llm_client=False)
        result = await agent.evaluate_visualization_fit(
            feature_count=200,
            geometry_type=geom,
            viz_type=viz,
            numeric_field_count=3,
            categorical_field_count=2,
        )
        assert result["appropriate"] is expected_appropriate, result

    @pytest.mark.asyncio
    async def test_heatmap_with_few_points_rejected(self):
        agent = InsightsAgent(llm_client=False)
        r = await agent.evaluate_visualization_fit(
            feature_count=5, geometry_type="Point", viz_type="heatmap",
            numeric_field_count=1,
        )
        assert r["appropriate"] is False
        assert "densidad" in r["reason"].lower() or "poco" in r["reason"].lower()
        assert r["alternative"] in {"graduated_symbols", "point_map"}

    @pytest.mark.asyncio
    async def test_cluster_with_few_points_suggests_point_map(self):
        agent = InsightsAgent(llm_client=False)
        r = await agent.evaluate_visualization_fit(
            feature_count=10, geometry_type="Point", viz_type="cluster",
        )
        assert r["appropriate"] is False
        assert r["alternative"] == "point_map"

    @pytest.mark.asyncio
    async def test_choropleth_without_numeric_field_rejected(self):
        agent = InsightsAgent(llm_client=False)
        r = await agent.evaluate_visualization_fit(
            feature_count=50, geometry_type="Polygon", viz_type="choropleth",
            numeric_field_count=0, categorical_field_count=3,
        )
        assert r["appropriate"] is False
        assert r["alternative"] == "unique_values"

    @pytest.mark.asyncio
    async def test_feature_count_zero_rejects_everything(self):
        """0 features: ningún viz aplica."""
        agent = InsightsAgent(llm_client=False)
        for viz in ["heatmap", "cluster", "choropleth", "graduated_colors"]:
            r = await agent.evaluate_visualization_fit(
                feature_count=0, geometry_type="Polygon", viz_type=viz,
                numeric_field_count=5,
            )
            assert r["appropriate"] is False, f"{viz} with 0 features"
            assert "feature" in r["reason"].lower() or "datos" in r["reason"].lower()

    @pytest.mark.asyncio
    async def test_feature_count_negative_rejected(self):
        """Negative count se trata como sin datos."""
        agent = InsightsAgent(llm_client=False)
        r = await agent.evaluate_visualization_fit(
            feature_count=-5, geometry_type="Point", viz_type="heatmap",
        )
        assert r["appropriate"] is False

    @pytest.mark.asyncio
    async def test_feature_count_as_string_coerced(self):
        """Caller pasa string en lugar de int — coerciona sin crash."""
        agent = InsightsAgent(llm_client=False)
        r = await agent.evaluate_visualization_fit(
            feature_count="100",  # type: ignore[arg-type]
            geometry_type="Point", viz_type="heatmap",
        )
        # 100 puntos: heatmap apropiado.
        assert r["appropriate"] is True

    @pytest.mark.asyncio
    async def test_feature_count_garbage_returns_inappropriate(self):
        """Caller pasa string no numérico — rechazo limpio."""
        agent = InsightsAgent(llm_client=False)
        r = await agent.evaluate_visualization_fit(
            feature_count="not_a_number",  # type: ignore[arg-type]
            geometry_type="Point", viz_type="heatmap",
        )
        assert r["appropriate"] is False
        assert "inválido" in r["reason"].lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("viz_input", [
        "HEATMAP", "Heatmap", "  heatmap  ", "HEATmap",
    ])
    async def test_viz_type_case_insensitive(self, viz_input):
        """**CRÍTICO** — LLM puede devolver case mixto. NO debe bypassar
        el check sobre pocos puntos."""
        agent = InsightsAgent(llm_client=False)
        r = await agent.evaluate_visualization_fit(
            feature_count=5, geometry_type="Point", viz_type=viz_input,
        )
        # 5 puntos → heatmap inadecuado independiente del case del input.
        assert r["appropriate"] is False
        assert r["alternative"] is not None

    @pytest.mark.asyncio
    async def test_unknown_viz_type_rejected(self):
        """viz_type fuera del set conocido NO debe pasar como OK."""
        agent = InsightsAgent(llm_client=False)
        r = await agent.evaluate_visualization_fit(
            feature_count=100, geometry_type="Polygon", viz_type="xyz_invented",
            numeric_field_count=3,
        )
        assert r["appropriate"] is False
        assert "reconocido" in r["reason"].lower() or "válidos" in r["reason"].lower()
        assert r["alternative"] == "single_symbol"


class TestSymbologyA2AOverride:
    """Integración: el SymbologyAgent acepta el override del InsightsAgent."""

    @pytest.mark.asyncio
    async def test_symbology_degrades_heatmap_when_insights_rejects(self):
        """**Caso clave A2A real #2**: el LLM eligió heatmap AUTÓNOMAMENTE
        (la query NO nombra "mapa de calor") para 5 puntos. InsightsAgent dice
        "no encaja, usa graduated_symbols". El design final tiene
        ``symbology_type='graduated_symbols'`` (no heatmap).

        Nota: la degradación A2A solo aplica cuando el tipo fue elección
        autónoma del LLM. Si el usuario lo pide TEXTUALMENTE, el intent explícito
        manda y NO se degrada — ver ``test_explicit_heatmap_intent_overrides_a2a``.
        """
        insights = InsightsAgent(llm_client=False)
        hub = AgentHub()
        hub.register("insights_agent", insights)

        # LLM mockeado: elige heatmap aunque haya solo 5 puntos.
        sym = SymbologyAgent(llm_client=False, agent_hub=hub)

        async def fake_llm_design(*args, **kwargs):
            return {
                "symbology_type": "heatmap",
                "classification_field": None,
                "classification_method": None,
                "color_scheme": "viridis",
                "num_classes": 5,
                "label_field": None,
                "reasoning": "la distribución sugiere densidad",
            }
        sym._llm_design_symbology = fake_llm_design  # type: ignore[method-assign]

        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point",
                                                 "coordinates": [-74, 4]},
                 "properties": {"id": i, "val": float(i)}}
                for i in range(5)
            ],
        }
        # Query AUTÓNOMA: no nombra el tipo de viz, así que el LLM lo eligió solo.
        analysis = await sym.analyze_data(geojson, query="muéstrame la distribución")

        design = analysis["design"]
        assert design["symbology_type"] == "graduated_symbols", (
            f"esperaba override A2A heatmap→graduated_symbols, "
            f"got {design['symbology_type']}"
        )
        assert "A2A" in (design.get("reasoning") or "")

        # Telemetría: hub registró el call.
        a2a_calls = [
            c for c in hub.call_log
            if c.target == "insights_agent"
            and c.method == "evaluate_visualization_fit"
        ]
        assert len(a2a_calls) == 1
        assert a2a_calls[0].success is True

    @pytest.mark.asyncio
    async def test_explicit_heatmap_intent_overrides_a2a(self):
        """**Instrucción literal MANDA** (bug #12 audit 2026-06-15, contrato
        R5.2/A3): si el usuario pide TEXTUALMENTE "mapa de calor", quien lo
        detecta es el LLM del diseño (marca ``explicit_user_request``) y el
        juez A2A NO degrada esa elección aunque el fit por densidad diga otra
        cosa. (El override por substring viejo fue eliminado en Fase 3 — era
        ciego a negaciones.)
        """
        insights = InsightsAgent(llm_client=False)
        hub = AgentHub()
        hub.register("insights_agent", insights)
        sym = SymbologyAgent(llm_client=False, agent_hub=hub)

        async def fake_llm_design(*args, **kwargs):
            # Simula al diseñador LLM aplicando la regla INSTRUCCIÓN LITERAL.
            return {
                "symbology_type": "heatmap",
                "classification_field": None,
                "classification_method": None,
                "color_scheme": "viridis",
                "num_classes": 5,
                "label_field": None,
                "explicit_user_request": True,
                "reasoning": "el usuario pidió 'mapa de calor' textualmente",
            }
        sym._llm_design_symbology = fake_llm_design  # type: ignore[method-assign]

        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point",
                                                 "coordinates": [-74, 4]},
                 "properties": {"id": i, "val": float(i)}}
                for i in range(5)
            ],
        }
        analysis = await sym.analyze_data(geojson, query="conviértelo en un mapa de calor")

        # Pese a solo 5 puntos, la instrucción literal gana (el A2A se salta).
        assert analysis["design"]["symbology_type"] == "heatmap", (
            "el usuario pidió 'mapa de calor' textualmente: debe ganar sobre el A2A"
        )

    @pytest.mark.asyncio
    async def test_explicit_cluster_intent_overrides_a2a(self):
        """Variante cluster: el diseñador marca la instrucción literal y el
        juez A2A no la degrada, aunque 30 puntos dispersos normalmente no
        ameriten cluster.
        """
        insights = InsightsAgent(llm_client=False)
        hub = AgentHub()
        hub.register("insights_agent", insights)
        sym = SymbologyAgent(llm_client=False, agent_hub=hub)

        async def fake_llm_design(*args, **kwargs):
            return {
                "symbology_type": "cluster",
                "classification_field": None,
                "classification_method": None,
                "color_scheme": "viridis",
                "num_classes": 5,
                "label_field": None,
                "explicit_user_request": True,
                "reasoning": "el usuario pidió 'agrúpalas en cluster' textualmente",
            }
        sym._llm_design_symbology = fake_llm_design  # type: ignore[method-assign]

        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point",
                                                 "coordinates": [-74, 4]},
                 "properties": {"id": i}}
                for i in range(30)
            ],
        }
        analysis = await sym.analyze_data(geojson, query="agrúpalas en cluster")
        assert analysis["design"]["symbology_type"] == "cluster"

    @pytest.mark.asyncio
    async def test_symbology_preserves_choice_when_insights_approves(self):
        """Si InsightsAgent dice "OK", el design queda intacto."""
        insights = InsightsAgent(llm_client=False)
        hub = AgentHub()
        hub.register("insights_agent", insights)
        sym = SymbologyAgent(llm_client=False, agent_hub=hub)

        async def fake_llm_design(*args, **kwargs):
            return {
                "symbology_type": "heatmap",
                "classification_field": None,
                "classification_method": None,
                "color_scheme": "viridis",
                "num_classes": 5,
                "label_field": None,
                "reasoning": "mucha densidad de puntos",
            }
        sym._llm_design_symbology = fake_llm_design  # type: ignore[method-assign]

        # 200 puntos → heatmap apropiado.
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [-74, 4]},
                 "properties": {"id": i}}
                for i in range(200)
            ],
        }
        analysis = await sym.analyze_data(geojson, query="mapa de calor")
        assert analysis["design"]["symbology_type"] == "heatmap"
        assert "A2A" not in (analysis["design"].get("reasoning") or "")

    @pytest.mark.asyncio
    async def test_symbology_without_hub_skips_a2a(self):
        """Contraprueba: sin hub, no hay A2A — el LLM decide solo."""
        sym = SymbologyAgent(llm_client=False, agent_hub=None)

        async def fake_llm_design(*args, **kwargs):
            return {
                "symbology_type": "heatmap",
                "classification_field": None,
                "classification_method": None,
                "color_scheme": "viridis",
                "num_classes": 5,
                "label_field": None,
                "reasoning": "intencional aunque sean pocos",
            }
        sym._llm_design_symbology = fake_llm_design  # type: ignore[method-assign]

        geojson = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [-74, 4]},
                 "properties": {"id": i}}
                for i in range(3)  # 3 puntos — heatmap horrible.
            ],
        }
        analysis = await sym.analyze_data(geojson, query="heatmap")
        # Sin hub, no hay override — el design queda como lo eligió el LLM.
        assert analysis["design"]["symbology_type"] == "heatmap"


# ---------------------------------------------------------------------------
# A2A capability #3: SymbologyAgent.suggest_palette_for
# Validar que InsightsAgent consulta y resultado llega a result["theme"].
# ---------------------------------------------------------------------------


class TestSuggestPaletteFor:
    """Reglas determinísticas: paleta según data_type/purpose/geometry."""

    @pytest.mark.asyncio
    async def test_numeric_continuous_map_points_returns_plasma(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(
            data_type="numeric_continuous",
            purpose="map",
            geometry_type="Point",
        )
        assert r["color_scheme"] == "plasma"
        assert r["is_sequential"] is True
        assert len(r["palette_hex"]) == 5
        assert r["palette_hex"][0].startswith("#")

    @pytest.mark.asyncio
    async def test_numeric_continuous_map_polygons_returns_viridis(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(
            data_type="numeric_continuous",
            purpose="map",
            geometry_type="Polygon",
        )
        assert r["color_scheme"] == "viridis"

    @pytest.mark.asyncio
    async def test_numeric_continuous_chart_returns_blues(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(
            data_type="numeric_continuous", purpose="chart",
        )
        assert r["color_scheme"] == "Blues"
        assert r["is_sequential"] is True

    @pytest.mark.asyncio
    async def test_diverging_map_returns_rdylgn(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(data_type="diverging", purpose="map")
        assert r["color_scheme"] == "RdYlGn"
        assert r["is_diverging"] is True

    @pytest.mark.asyncio
    async def test_diverging_chart_returns_rdbu(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(data_type="diverging", purpose="chart")
        assert r["color_scheme"] == "RdBu"

    @pytest.mark.asyncio
    async def test_categorical_few_classes_returns_set2(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(data_type="categorical", num_classes=5)
        assert r["color_scheme"] == "Set2"
        assert r["is_qualitative"] is True
        assert len(r["palette_hex"]) == 5

    @pytest.mark.asyncio
    async def test_categorical_many_classes_returns_paired(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(data_type="categorical", num_classes=10)
        assert r["color_scheme"] == "Paired"
        assert len(r["palette_hex"]) == 10

    @pytest.mark.asyncio
    async def test_boolean_returns_two_divergent_colors(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(data_type="boolean")
        assert r["color_scheme"] == "RdYlGn"
        assert r["is_diverging"] is True
        assert len(r["palette_hex"]) == 2

    @pytest.mark.asyncio
    async def test_num_classes_clamped_to_max_12(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(data_type="categorical", num_classes=99)
        assert r["num_classes"] == 12
        assert len(r["palette_hex"]) == 12

    @pytest.mark.asyncio
    async def test_unknown_data_type_falls_back_to_blues(self):
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(data_type="xyzwtf")
        assert r["color_scheme"] == "Blues"
        assert "desconocido" in r["reasoning"].lower() or "fallback" in r["reasoning"].lower()

    @pytest.mark.asyncio
    async def test_num_classes_none_uses_default_5(self):
        """num_classes=None no debe crashear — usa default."""
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(
            data_type="numeric_continuous", num_classes=None,  # type: ignore[arg-type]
        )
        assert r["num_classes"] == 5
        assert len(r["palette_hex"]) == 5

    @pytest.mark.asyncio
    async def test_num_classes_string_parsed(self):
        """num_classes='7' (string) parseado a int sin crash."""
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(
            data_type="categorical", num_classes="7",  # type: ignore[arg-type]
        )
        assert r["num_classes"] == 7
        assert len(r["palette_hex"]) == 7

    @pytest.mark.asyncio
    async def test_num_classes_garbage_string_falls_back(self):
        """num_classes='abc' → fallback a default 5 sin crash."""
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(
            data_type="categorical", num_classes="abc",  # type: ignore[arg-type]
        )
        assert r["num_classes"] == 5

    @pytest.mark.asyncio
    async def test_data_type_case_insensitive(self):
        """LLM puede devolver case mixto. Lower-casing aplicado."""
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(
            data_type="NUMERIC_CONTINUOUS", purpose="MAP",
            geometry_type="POINT",
        )
        # Esperamos plasma (point + numeric + map).
        assert r["color_scheme"] == "plasma"
        assert r["is_sequential"] is True

    @pytest.mark.asyncio
    async def test_purpose_case_insensitive(self):
        """purpose case mixto debe normalizar."""
        sym = SymbologyAgent(llm_client=False)
        r = await sym.suggest_palette_for(
            data_type="numeric_continuous", purpose="Chart",
        )
        assert r["color_scheme"] == "Blues"

    @pytest.mark.asyncio
    async def test_boolean_always_returns_2_colors_regardless_of_num_classes(self):
        """Boolean tiene exactamente 2 valores — n_classes input se ignora."""
        sym = SymbologyAgent(llm_client=False)
        for requested in [1, 3, 5, 10]:
            r = await sym.suggest_palette_for(
                data_type="boolean", num_classes=requested,
            )
            assert r["num_classes"] == 2, (
                f"boolean con num_classes={requested} debería devolver 2, "
                f"got {r['num_classes']}"
            )
            assert len(r["palette_hex"]) == 2


class TestInsightsA2APalette:
    """Integración: InsightsAgent consulta paleta via A2A para chart consistency."""

    @pytest.mark.asyncio
    async def test_insights_pulls_palette_via_a2a_into_theme(self):
        """**Caso clave A2A #3**: cuando hay hub, ``result["theme"]`` se
        llena con la paleta del SymbologyAgent y queda etiquetada como
        ``from="symbology_agent"``.
        """
        from geo_copilot.agents.insights_agent.agent import OutputFormat

        sym = SymbologyAgent(llm_client=False)
        hub = AgentHub()
        hub.register("symbology_agent", sym)

        insights = InsightsAgent(llm_client=False, agent_hub=hub)

        # Mock del LLM design para que sea determinístico — devuelve un
        # plan con un histogram (que dispara data_type=numeric_continuous).
        async def fake_design(*args, **kwargs):
            return {
                "map_type": "point_map",
                "map_value_field": None,
                "map_color_field": None,
                "popup_fields": [],
                "charts": [{
                    "chart_type": "histogram",
                    "value_field": "distance_m",
                    "title": "Histograma de distancias",
                }],
                "reasoning": "histograma de distancias",
            }
        insights._llm_design_visualizations = fake_design  # type: ignore[method-assign]

        analysis_result = {
            "geojson": {
                "type": "FeatureCollection",
                "features": [
                    {"type": "Feature",
                     "geometry": {"type": "Point", "coordinates": [-74, 4]},
                     "properties": {"distance_m": float(i)}}
                    for i in range(60)
                ],
            },
            "stats": {"feature_count": 60},
        }

        result = await insights.generate_insights(
            analysis_result=analysis_result,
            analysis_type="proximity",
            output_format=OutputFormat.CHARTS_ONLY,
        )

        # 1. Theme presente con paleta A2A.
        theme = result.get("theme")
        assert theme is not None, "esperaba result['theme'] poblado via A2A"
        assert theme["from"] == "symbology_agent"
        assert theme["data_type"] == "numeric_continuous"
        assert theme["purpose"] == "chart"
        assert theme["color_scheme"] == "Blues"
        assert isinstance(theme["palette_hex"], list)
        assert len(theme["palette_hex"]) > 0
        assert all(c.startswith("#") for c in theme["palette_hex"])

        # 2. El hub registró la llamada cross-agent.
        a2a_calls = [
            c for c in hub.call_log
            if c.target == "symbology_agent"
            and c.method == "suggest_palette_for"
        ]
        assert len(a2a_calls) == 1
        assert a2a_calls[0].success is True

    @pytest.mark.asyncio
    async def test_insights_without_hub_no_theme(self):
        """Contraprueba: sin hub, theme=None y no hay A2A."""
        from geo_copilot.agents.insights_agent.agent import OutputFormat

        insights = InsightsAgent(llm_client=False, agent_hub=None)

        async def fake_design(*args, **kwargs):
            return {
                "map_type": "point_map",
                "map_value_field": None,
                "map_color_field": None,
                "popup_fields": [],
                "charts": [{"chart_type": "bar", "x_key": "x", "y_key": "y",
                            "title": "x"}],
                "reasoning": "",
            }
        insights._llm_design_visualizations = fake_design  # type: ignore[method-assign]

        analysis_result = {
            "geojson": {"type": "FeatureCollection", "features": [
                {"type": "Feature",
                 "geometry": {"type": "Point", "coordinates": [-74, 4]},
                 "properties": {"x": "A", "y": 1}}
            ]},
            "stats": {"feature_count": 1},
        }

        result = await insights.generate_insights(
            analysis_result=analysis_result,
            analysis_type="proximity",
            output_format=OutputFormat.CHARTS_ONLY,
        )

        assert result.get("theme") is None

    @pytest.mark.asyncio
    async def test_insights_data_type_inference_for_bar_chart(self):
        """Bar chart con x_key categórico → A2A invocado con
        data_type=categorical."""
        from geo_copilot.agents.insights_agent.agent import OutputFormat

        sym = SymbologyAgent(llm_client=False)
        hub = AgentHub()
        hub.register("symbology_agent", sym)

        insights = InsightsAgent(llm_client=False, agent_hub=hub)

        async def fake_design(*args, **kwargs):
            return {
                "map_type": "point_map",
                "map_value_field": None,
                "map_color_field": None,
                "popup_fields": [],
                "charts": [{
                    "chart_type": "bar",
                    "x_key": "categoria",
                    "y_key": "conteo",
                    "title": "Distribución",
                    "limit": 6,
                }],
                "reasoning": "",
            }
        insights._llm_design_visualizations = fake_design  # type: ignore[method-assign]

        analysis_result = {
            "geojson": {"type": "FeatureCollection", "features": [
                {"type": "Feature",
                 "geometry": {"type": "Polygon", "coordinates": []},
                 "properties": {"categoria": "A", "conteo": 10}}
            ]},
            "stats": {"feature_count": 1},
        }

        result = await insights.generate_insights(
            analysis_result=analysis_result,
            analysis_type="aggregation",
            output_format=OutputFormat.CHARTS_ONLY,
        )

        theme = result["theme"]
        assert theme["data_type"] == "categorical"
        assert theme["color_scheme"] == "Set2"
        # limit=6 → num_classes=6 — clamped por suggest_palette_for.
        assert theme["num_classes"] == 6
        assert len(theme["palette_hex"]) == 6


# ---------------------------------------------------------------------------
# A2A capability #4: DataAgent.list_available_entities
# ---------------------------------------------------------------------------


class TestListAvailableEntities:
    """Discovery capability — el Router/Planner pide qué hay disponible."""

    @pytest.mark.asyncio
    async def test_returns_all_entities_with_structure(self, data_agent):
        entities = await data_agent.list_available_entities()
        # El fixture tiene 3 entidades: construcciones, lotes, vias.
        assert len(entities) == 3
        names = {e["name"] for e in entities}
        assert names == {"construcciones", "lotes", "vias"}
        # Cada entry tiene la estructura esperada.
        e = next(x for x in entities if x["name"] == "construcciones")
        assert e["table"] == "catastro.construcciones"
        assert e["geometry_type"] == "MULTIPOLYGON"
        assert e["geometry_column"] == "geom"
        assert e["srid"] == 4326
        assert e["field_count"] == 3
        assert "edificio" in e["aliases"]

    @pytest.mark.asyncio
    async def test_only_with_geometry_filter(self, data_agent):
        # En este fixture todas tienen geometry — el filtro no cambia el set.
        entities_all = await data_agent.list_available_entities()
        entities_geo = await data_agent.list_available_entities(only_with_geometry=True)
        assert len(entities_all) == len(entities_geo)

    @pytest.mark.asyncio
    async def test_max_entities_respected(self, data_agent):
        entities = await data_agent.list_available_entities(max_entities=2)
        assert len(entities) == 2

    @pytest.mark.asyncio
    async def test_no_semantic_layer_returns_empty(self):
        agent = DataAgent(semantic_layer=None, llm_client=MagicMock())
        entities = await agent.list_available_entities()
        assert entities == []

    @pytest.mark.asyncio
    async def test_callable_via_hub(self, data_agent):
        """Confirma que es invocable como capacidad A2A vía hub."""
        hub = AgentHub()
        hub.register("data_agent", data_agent)
        ok, result = await hub.call(
            caller="planner_test",
            target="data_agent",
            method="list_available_entities",
        )
        assert ok is True
        assert isinstance(result, list)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_max_entities_zero_returns_empty(self, data_agent):
        """**Bug fix**: max_entities=0 antes devolvía 1 entrada por off-by-check."""
        entities = await data_agent.list_available_entities(max_entities=0)
        assert entities == []

    @pytest.mark.asyncio
    async def test_max_entities_negative_returns_empty(self, data_agent):
        entities = await data_agent.list_available_entities(max_entities=-5)
        assert entities == []

    @pytest.mark.asyncio
    async def test_max_entities_string_coerced(self, data_agent):
        """max_entities='2' (string) parseado a int."""
        entities = await data_agent.list_available_entities(
            max_entities="2",  # type: ignore[arg-type]
        )
        assert len(entities) == 2

    @pytest.mark.asyncio
    async def test_max_entities_garbage_uses_default(self, data_agent):
        """max_entities='abc' → fallback default."""
        entities = await data_agent.list_available_entities(
            max_entities="abc",  # type: ignore[arg-type]
        )
        # Default 50 — el fixture tiene solo 3, devuelve todas.
        assert len(entities) == 3

    @pytest.mark.asyncio
    async def test_uses_public_semantic_api(self, data_agent):
        """list_available_entities NO debe acceder a _entities privado."""
        from unittest.mock import patch
        original = data_agent.semantic_layer.list_entities
        with patch.object(
            data_agent.semantic_layer, "list_entities",
            side_effect=original,
        ) as mock_list:
            await data_agent.list_available_entities()
            assert mock_list.call_count >= 1


# ---------------------------------------------------------------------------
# A2A capability #5: GISAgent.preview_count
# ---------------------------------------------------------------------------


class _MockDBConnection:
    """Simula asyncpg.Connection con un fetchrow controlado."""

    def __init__(self, count_value: int):
        self._count = count_value
        self.executed_queries: list[str] = []

    async def fetchrow(self, sql: str):
        self.executed_queries.append(sql)
        return {"n": self._count}

    async def execute(self, sql: str):
        self.executed_queries.append(sql)

    def transaction(self, readonly: bool = False):
        # Re-uso del mismo objeto para simplicidad — el wrapper no hace nada.
        return _NullContextManager()


class _NullContextManager:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False


class _MockDBPool:
    """Simula asyncpg.Pool retornando una _MockDBConnection."""

    def __init__(self, count_value: int):
        self.connection = _MockDBConnection(count_value)

    def acquire(self):
        return _MockPoolAcquireCtx(self.connection)


class _MockPoolAcquireCtx:
    def __init__(self, conn):
        self.conn = conn
    async def __aenter__(self):
        return self.conn
    async def __aexit__(self, *args):
        return False


class TestPreviewCount:
    """Preflight count via SQL COUNT(*) sin fetchear geometría."""

    @pytest.mark.asyncio
    async def test_preview_count_happy_path(self, semantic_layer):
        pool = _MockDBPool(count_value=487)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        r = await gis.preview_count(entity="construcciones")
        assert r["success"] is True
        assert r["count"] == 487
        # S5: identificadores citados.
        assert r["table"] == '"catastro"."construcciones"'
        assert "SELECT COUNT(*)" in r["executed_sql"]
        assert '"catastro"."construcciones"' in r["executed_sql"]

    @pytest.mark.asyncio
    async def test_preview_count_with_where_clause(self, semantic_layer):
        pool = _MockDBPool(count_value=42)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        r = await gis.preview_count(
            entity="construcciones", where_clause="area_m2 > 500",
        )
        assert r["success"] is True
        assert r["count"] == 42
        assert "WHERE area_m2 > 500" in r["executed_sql"]

    @pytest.mark.asyncio
    async def test_preview_count_rejects_multi_statement(self, semantic_layer):
        """Multi-statement con DROP — SQLValidator detecta el DROP antes
        del ``;``, devuelve error explícito. Antes el check era solo
        contra ``;``; ahora delegamos a SQLValidator que es más estricto."""
        pool = _MockDBPool(count_value=0)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        r = await gis.preview_count(
            entity="construcciones",
            where_clause="1=1; DROP TABLE construcciones",
        )
        assert r["success"] is False
        # El validator rechaza DROP/DELETE/etc — el error contiene la
        # palabra prohibida o "rechazada".
        assert "rechazada" in r["error"].lower() or "drop" in r["error"].lower()
        assert r["count"] is None

    @pytest.mark.asyncio
    async def test_preview_count_unknown_entity(self, semantic_layer):
        pool = _MockDBPool(count_value=0)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        r = await gis.preview_count(entity="xyz_no_existe")
        assert r["success"] is False
        assert "not found" in r["error"]

    @pytest.mark.asyncio
    async def test_preview_count_no_db_pool(self, semantic_layer):
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=None)
        r = await gis.preview_count(entity="construcciones")
        assert r["success"] is False
        assert "no database pool" in r["error"]

    @pytest.mark.asyncio
    async def test_preview_count_via_hub(self, semantic_layer):
        pool = _MockDBPool(count_value=100)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        hub = AgentHub()
        hub.register("gis_agent", gis)
        ok, result = await hub.call(
            caller="symbology_test", target="gis_agent",
            method="preview_count", entity="construcciones",
        )
        assert ok is True
        assert result["count"] == 100

    @pytest.mark.asyncio
    async def test_preview_count_blocks_union_injection(self, semantic_layer):
        """**SECURITY**: UNION en where_clause debe rechazarse vía SQLValidator."""
        pool = _MockDBPool(count_value=0)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        r = await gis.preview_count(
            entity="construcciones",
            where_clause="1=1 UNION ALL SELECT pg_sleep(60)",
        )
        assert r["success"] is False
        assert "SQLValidator" in r["error"] or "rechazada" in r["error"].lower()

    @pytest.mark.asyncio
    async def test_preview_count_blocks_pg_sleep(self, semantic_layer):
        """**SECURITY**: pg_sleep en where_clause se rechaza."""
        pool = _MockDBPool(count_value=0)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        r = await gis.preview_count(
            entity="construcciones",
            where_clause="area_m2 > pg_sleep(10)",
        )
        assert r["success"] is False

    @pytest.mark.asyncio
    async def test_preview_count_blocks_destructive_in_where(self, semantic_layer):
        """**SECURITY**: DELETE/DROP/etc en where_clause se rechazan."""
        pool = _MockDBPool(count_value=0)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        for malicious in [
            "1=1 AND (DELETE FROM lotes)",
            "1=1 OR EXISTS (DROP TABLE x)",
        ]:
            r = await gis.preview_count(
                entity="construcciones", where_clause=malicious,
            )
            assert r["success"] is False, (
                f"se debió rechazar where_clause: {malicious!r}, got {r}"
            )

    @pytest.mark.asyncio
    async def test_preview_count_caps_where_clause_length(self, semantic_layer):
        """where_clause >2000 chars se rechaza por longitud."""
        pool = _MockDBPool(count_value=0)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        # 3000 caracteres de un OR encadenado.
        long_where = " OR ".join(["1=1"] * 800)
        r = await gis.preview_count(
            entity="construcciones", where_clause=long_where,
        )
        assert r["success"] is False
        assert "too long" in r["error"].lower()

    @pytest.mark.asyncio
    async def test_preview_count_empty_entity(self, semantic_layer):
        pool = _MockDBPool(count_value=0)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        r = await gis.preview_count(entity="")
        assert r["success"] is False
        assert "empty" in r["error"].lower() or "not a string" in r["error"].lower()

    @pytest.mark.asyncio
    async def test_preview_count_timeout_garbage_falls_back(self, semantic_layer):
        """timeout_ms='abc' usa default sin crash."""
        pool = _MockDBPool(count_value=42)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        r = await gis.preview_count(
            entity="construcciones",
            timeout_ms="abc",  # type: ignore[arg-type]
        )
        assert r["success"] is True
        assert r["count"] == 42

    @pytest.mark.asyncio
    async def test_preview_count_entity_with_trim(self, semantic_layer):
        """Espacios envolventes en entity se ignoran."""
        pool = _MockDBPool(count_value=5)
        gis = GISAgent(semantic_layer=semantic_layer, llm_client=MagicMock(), db_pool=pool)
        r = await gis.preview_count(entity="  construcciones  ")
        assert r["success"] is True
        assert r["count"] == 5


# ---------------------------------------------------------------------------
# A2A capability #6: PythonAgent.validate_operation
# ---------------------------------------------------------------------------


class TestValidateOperation:
    """Reglas determinísticas: ¿la op espacial es viable?"""

    @pytest.fixture
    def py_agent(self):
        return PythonAgent(llm_client=MagicMock())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("op,geom,fc,params,expected", [
        # buffer: cualquier geometría, distance > 0
        ("buffer", "Point", 10, {"distance": 500}, True),
        ("buffer", "Polygon", 10, {"distance": -10}, False),
        # centroid: cualquier geom — pero warning en Point
        ("centroid", "Polygon", 5, {}, True),
        ("centroid", "Point", 5, {}, True),  # ok pero con warning
        # area: solo polígonos
        ("area", "Polygon", 10, {}, True),
        ("area", "Point", 10, {}, False),
        ("area", "LineString", 10, {}, False),
        # length: lines y polygons
        ("length", "LineString", 10, {}, True),
        ("length", "Polygon", 10, {}, True),
        ("length", "Point", 10, {}, False),
        # intersection requires other_layer_key
        ("intersection", "Polygon", 10, {}, False),
        ("intersection", "Polygon", 10, {"other_layer_key": "lotes"}, True),
        # union: cualquier feature_count >=1 pero warning si =1
        ("union", "Polygon", 1, {}, True),
        ("union", "Polygon", 100, {}, True),
        # distance requires reference
        ("distance", "Point", 10, {}, False),
        ("distance", "Point", 10, {"other_layer_key": "vias"}, True),
        # convex_hull con <3 features tiene warning
        ("convex_hull", "Point", 2, {}, True),
        ("convex_hull", "Point", 50, {}, True),
        # filter es siempre viable
        ("filter", "Point", 1, {}, True),
        # op desconocida: feasible con warning
        ("xyzunknown", "Polygon", 10, {}, True),
        # 0 features: nunca viable
        ("buffer", "Polygon", 0, {"distance": 100}, False),
    ])
    async def test_rules_matrix(self, py_agent, op, geom, fc, params, expected):
        r = await py_agent.validate_operation(
            op_name=op, feature_count=fc, geometry_type=geom, params=params,
        )
        assert r["feasible"] is expected, (
            f"{op} on {geom}/{fc} feat with {params} → expected {expected}, "
            f"got {r}"
        )

    @pytest.mark.asyncio
    async def test_centroid_on_point_emits_warning(self, py_agent):
        r = await py_agent.validate_operation(
            op_name="centroid", feature_count=5, geometry_type="Point",
        )
        assert r["feasible"] is True
        assert r["warning"] is not None
        assert "redundante" in r["warning"].lower()

    @pytest.mark.asyncio
    async def test_area_on_point_suggests_no_alternative(self, py_agent):
        r = await py_agent.validate_operation(
            op_name="area", feature_count=5, geometry_type="Point",
        )
        assert r["feasible"] is False
        assert r["alternative"] is None

    @pytest.mark.asyncio
    async def test_area_on_line_suggests_length_alternative(self, py_agent):
        r = await py_agent.validate_operation(
            op_name="area", feature_count=5, geometry_type="LineString",
        )
        assert r["feasible"] is False
        assert r["alternative"] == "length"

    @pytest.mark.asyncio
    async def test_callable_via_hub(self, py_agent):
        hub = AgentHub()
        hub.register("python_agent", py_agent)
        ok, result = await hub.call(
            caller="planner_test", target="python_agent",
            method="validate_operation", op_name="buffer",
            feature_count=100, geometry_type="Point",
            params={"distance": 500},
        )
        assert ok is True
        assert result["feasible"] is True

    @pytest.mark.asyncio
    async def test_buffer_without_distance_warns(self, py_agent):
        """buffer sin distance: feasible=True con warning explícito."""
        r = await py_agent.validate_operation(
            op_name="buffer", feature_count=10, geometry_type="Point",
            params={},
        )
        assert r["feasible"] is True
        assert r["warning"] is not None
        assert "distance" in r["warning"].lower()

    @pytest.mark.asyncio
    async def test_feature_count_as_string(self, py_agent):
        """feature_count='50' (string) coerciona."""
        r = await py_agent.validate_operation(
            op_name="buffer", feature_count="50",  # type: ignore[arg-type]
            geometry_type="Point", params={"distance": 500},
        )
        assert r["feasible"] is True

    @pytest.mark.asyncio
    async def test_feature_count_garbage_rejected(self, py_agent):
        """feature_count no parseable: rechazo limpio."""
        r = await py_agent.validate_operation(
            op_name="buffer", feature_count="not_int",  # type: ignore[arg-type]
            geometry_type="Point",
        )
        assert r["feasible"] is False
        assert "inválido" in r["reason"].lower()

    @pytest.mark.asyncio
    async def test_op_name_none_falls_back(self, py_agent):
        """op_name=None usa fallback 'desconocida' sin crash."""
        r = await py_agent.validate_operation(
            op_name=None,  # type: ignore[arg-type]
            feature_count=10, geometry_type="Polygon",
        )
        # Cae al fallback: feasible=True con warning de op desconocida.
        assert r["feasible"] is True
        assert "desconocida" in (r["warning"] or "").lower() or "sandbox" in (r["warning"] or "").lower()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("variant", [
        "BUFFER", "Buffer", "  buffer  ", "BuFfEr",
    ])
    async def test_op_name_case_insensitive(self, py_agent, variant):
        """LLM puede devolver case mixto — lower-casing y strip aplicados."""
        r = await py_agent.validate_operation(
            op_name=variant, feature_count=10, geometry_type="Point",
            params={"distance": 100},
        )
        assert r["feasible"] is True
        assert "buffer" in r["reason"].lower() or "válido" in r["reason"].lower()

    @pytest.mark.asyncio
    async def test_length_on_point_rejected(self, py_agent):
        """length en Point NO es viable (no hay longitud meaningful)."""
        r = await py_agent.validate_operation(
            op_name="length", feature_count=10, geometry_type="Point",
        )
        assert r["feasible"] is False


# ---------------------------------------------------------------------------
# A2A capability #7: InsightsAgent.summarize_data_shape
# ---------------------------------------------------------------------------


class TestSummarizeDataShape:
    """Resumen NL determinístico de la forma de los datos."""

    @pytest.mark.asyncio
    async def test_empty_layer(self):
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(feature_count=0)
        assert r["has_data"] is False
        assert "no hay" in r["summary"].lower()

    @pytest.mark.asyncio
    async def test_polygon_layer_with_fields(self):
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(
            feature_count=487,
            geometry_type="Polygon",
            field_names=["id", "uso_suelo", "area_m2", "altura"],
        )
        assert r["has_data"] is True
        assert "487" in r["summary"]
        assert "polígonos" in r["summary"]
        assert "uso_suelo" in r["summary"]
        assert r["feature_count"] == 487
        assert r["field_count"] == 4

    @pytest.mark.asyncio
    async def test_singular_feature(self):
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(
            feature_count=1, geometry_type="Point",
        )
        # Singular: "1 punto" no "1 puntos".
        assert "1 punto" in r["summary"]
        assert "1 puntos" not in r["summary"]

    @pytest.mark.asyncio
    async def test_massive_dataset_formatted_with_separator(self):
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(
            feature_count=2_400_000, geometry_type="MultiPolygon",
        )
        # Formato con coma.
        assert "2,400,000" in r["summary"]
        assert "polígonos" in r["summary"]
        assert "masivos" in r["summary"].lower()

    @pytest.mark.asyncio
    async def test_with_sample_values(self):
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(
            feature_count=100,
            geometry_type="Polygon",
            field_names=["nombre", "categoria"],
            sample_values={"nombre": "Bogotá", "categoria": "Capital"},
        )
        assert "Bogotá" in r["summary"]
        assert "Capital" in r["summary"]

    @pytest.mark.asyncio
    async def test_callable_via_hub(self):
        ins = InsightsAgent(llm_client=False)
        hub = AgentHub()
        hub.register("insights_agent", ins)
        ok, result = await hub.call(
            caller="router_test", target="insights_agent",
            method="summarize_data_shape",
            feature_count=50, geometry_type="Point",
            field_names=["id"],
        )
        assert ok is True
        assert "50 puntos" in result["summary"]

    @pytest.mark.asyncio
    async def test_feature_count_as_string(self):
        """feature_count='100' string se coerciona."""
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(
            feature_count="100",  # type: ignore[arg-type]
            geometry_type="Polygon",
        )
        assert r["has_data"] is True
        assert "100 polígonos" in r["summary"]
        assert r["feature_count"] == 100

    @pytest.mark.asyncio
    async def test_feature_count_garbage_returns_no_data(self):
        """feature_count no-parseable → has_data=False con error."""
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(
            feature_count="not_a_number",  # type: ignore[arg-type]
        )
        assert r["has_data"] is False
        assert "inválido" in r["summary"].lower()

    @pytest.mark.asyncio
    async def test_singular_geometrycollection_does_not_use_naive_strip(self):
        """**Bug fix**: 1 feature de GeometryCollection antes daba
        '1 geometrías mixta' (raro). Ahora usa singular explícito."""
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(
            feature_count=1, geometry_type="GeometryCollection",
        )
        # Singular correcto: "1 geometría" (sin "mixtas" pluralizado mal).
        assert "1 geometría" in r["summary"]
        assert "mixta" not in r["summary"]  # antes producía "geometrías mixta"

    @pytest.mark.asyncio
    async def test_field_names_not_list_ignored(self):
        """Si caller pasa string en lugar de list, se ignora limpiamente."""
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(
            feature_count=10, geometry_type="Point",
            field_names="not_a_list",  # type: ignore[arg-type]
        )
        # field_count debe ser 0 (string ignorado).
        assert r["field_count"] == 0
        # El summary tampoco debe traer characters sueltos del string.
        assert "`n`" not in r["summary"]

    @pytest.mark.asyncio
    async def test_negative_feature_count(self):
        """Negativo se trata como sin datos."""
        ins = InsightsAgent(llm_client=False)
        r = await ins.summarize_data_shape(feature_count=-5)
        assert r["has_data"] is False
