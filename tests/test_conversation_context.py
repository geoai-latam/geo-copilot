"""
Tests para el contexto de conversación y persistencia de estado.

Estos tests validan:
1. found_services se persiste correctamente entre turnos
2. El contexto de conversación mantiene el estado
3. Las variables de sesión funcionan correctamente
"""

import pytest

from geo_copilot.orchestrator.conversation import (
    AnalysisState,
    ConversationContext,
    ConversationManager,
    MessageRole,
)


class TestConversationContext:
    """Tests para ConversationContext."""

    def test_create_context(self):
        """Debe crear contexto con session_id."""
        ctx = ConversationContext(session_id="test-123")
        assert ctx.session_id == "test-123"
        assert len(ctx.history) == 0

    def test_add_user_message(self):
        """Debe agregar mensajes de usuario."""
        ctx = ConversationContext(session_id="test")
        ctx.add_user_message("busca bomberos")

        assert len(ctx.history) == 1
        assert ctx.history[0].role == MessageRole.USER
        assert ctx.history[0].content == "busca bomberos"

    def test_add_assistant_message(self):
        """Debe agregar mensajes del asistente."""
        ctx = ConversationContext(session_id="test")
        ctx.add_assistant_message("Encontré 4 servicios", sql="SELECT * FROM x")

        assert len(ctx.history) == 1
        assert ctx.history[0].role == MessageRole.ASSISTANT

    def test_set_and_get_variable(self):
        """Debe almacenar y recuperar variables."""
        ctx = ConversationContext(session_id="test")

        services = [
            {"name": "bomberos", "url": "http://example.com/1"},
            {"name": "hospitales", "url": "http://example.com/2"}
        ]
        ctx.set_variable("found_services", services)

        retrieved = ctx.get_variable("found_services")
        assert retrieved == services
        assert len(retrieved) == 2

    def test_get_variable_default(self):
        """Debe retornar default si variable no existe."""
        ctx = ConversationContext(session_id="test")

        result = ctx.get_variable("nonexistent", default=[])
        assert result == []

    def test_found_services_persistence(self):
        """found_services debe persistir entre operaciones."""
        ctx = ConversationContext(session_id="test")

        # Simular búsqueda
        ctx.add_user_message("busca bomberos")
        services = [
            {"name": "Estaciones Bomberos", "url": "http://bomberos.gov.co/arcgis"},
            {"name": "Bomberos Voluntarios", "url": "http://voluntarios.gov.co/arcgis"}
        ]
        ctx.set_variable("found_services", services)
        ctx.add_assistant_message("Encontré 2 servicios de bomberos")

        # Simular selección por número
        ctx.add_user_message("2")
        found = ctx.get_variable("found_services")

        assert found is not None
        assert len(found) == 2
        assert found[1]["name"] == "Bomberos Voluntarios"

    def test_update_state(self):
        """Debe actualizar estado del análisis."""
        ctx = ConversationContext(session_id="test")

        ctx.update_state(last_sql="SELECT * FROM predios")
        assert ctx.state.last_sql == "SELECT * FROM predios"

        ctx.update_state(last_results={"results": [{"id": 1}]})
        assert ctx.state.last_results == {"results": [{"id": 1}]}

    def test_get_messages_for_llm(self):
        """Debe formatear mensajes para el LLM."""
        ctx = ConversationContext(session_id="test")

        ctx.add_user_message("hola")
        ctx.add_assistant_message("hola, ¿en qué puedo ayudarte?")
        ctx.add_user_message("busca parques")

        messages = ctx.get_messages_for_llm(max_messages=10)

        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert messages[2]["role"] == "user"

    def test_context_reset(self):
        """Debe limpiar el contexto correctamente."""
        ctx = ConversationContext(session_id="test")

        ctx.add_user_message("test")
        ctx.set_variable("found_services", [{"test": True}])
        ctx.update_state(last_sql="SELECT 1")

        ctx.reset()

        assert len(ctx.history) == 0
        assert ctx.get_variable("found_services") is None
        assert ctx.state.last_sql is None


class TestConversationManager:
    """Tests para ConversationManager."""

    def test_create_session(self):
        """Debe crear sesiones nuevas."""
        manager = ConversationManager()
        ctx = manager.create_session("session-1")

        assert ctx.session_id == "session-1"

    def test_get_or_create_session(self):
        """Debe obtener sesión existente o crear nueva."""
        manager = ConversationManager()

        ctx1 = manager.get_or_create_session("session-1")
        ctx1.set_variable("test", "value")

        ctx2 = manager.get_or_create_session("session-1")

        assert ctx2.get_variable("test") == "value"

    def test_session_isolation(self):
        """Las sesiones deben estar aisladas."""
        manager = ConversationManager()

        ctx1 = manager.get_or_create_session("session-1")
        ctx2 = manager.get_or_create_session("session-2")

        ctx1.set_variable("found_services", [{"name": "service1"}])

        assert ctx1.get_variable("found_services") is not None
        assert ctx2.get_variable("found_services") is None

    def test_found_services_across_turns(self):
        """found_services debe persistir a través de múltiples turnos en la misma sesión."""
        manager = ConversationManager()

        # Turno 1: Búsqueda
        session_id = "user-session-123"
        ctx = manager.get_or_create_session(session_id)
        ctx.add_user_message("busca bomberos")

        # Simular respuesta del agente
        services = [
            {"id": 1, "name": "Bomberos Bogotá", "url": "http://catastrobogota.gov.co/bomberos"},
            {"id": 2, "name": "Bomberos Medellín", "url": "http://medellin.gov.co/bomberos"},
        ]
        ctx.set_variable("found_services", services)
        ctx.add_assistant_message("Encontré 2 servicios")

        # Turno 2: Selección - obtener contexto de nuevo (como lo haría query.py)
        ctx2 = manager.get_or_create_session(session_id)
        found = ctx2.get_variable("found_services")

        assert found is not None, "found_services should persist between turns"
        assert len(found) == 2
        assert found[0]["name"] == "Bomberos Bogotá"

        # Seleccionar por número
        selected_idx = 1  # Usuario escribió "2"
        selected_service = found[selected_idx]
        assert selected_service["name"] == "Bomberos Medellín"


class TestAnalysisState:
    """Tests para AnalysisState."""

    def test_default_state(self):
        """Estado inicial debe estar vacío."""
        state = AnalysisState()

        assert state.last_sql is None
        assert state.last_results is None
        assert state.entities_identified == []

    def test_state_reset(self):
        """Reset debe limpiar todos los campos."""
        state = AnalysisState()
        state.last_sql = "SELECT * FROM test"
        state.entities_identified = ["parcela"]

        state.reset()

        assert state.last_sql is None
        assert state.entities_identified == []


class TestFoundServicesFlow:
    """Tests de integración para el flujo completo de found_services."""

    def test_search_select_flow(self):
        """Test del flujo completo: buscar -> mostrar -> seleccionar."""
        manager = ConversationManager()
        session_id = "integration-test"

        # === TURNO 1: Usuario busca ===
        ctx = manager.get_or_create_session(session_id)
        user_query = "busca información de parques nacionales"
        ctx.add_user_message(user_query)

        # Simular respuesta del DataAgent (búsqueda exitosa)
        found_services = [
            {
                "name": "Limites_oficiales_Linea",
                "description": "Límites de Parques Nacionales",
                "url": "http://mapas.parquesnacionales.gov.co/arcgis/rest/services/pnn/Limites_oficiales_Linea/FeatureServer",
                "type": "ArcGIS"
            },
            {
                "name": "Limites_oficiales_Poligono",
                "description": "Polígonos de Parques Nacionales",
                "url": "http://mapas.parquesnacionales.gov.co/arcgis/rest/services/pnn/Limites_oficiales_Poligono/FeatureServer",
                "type": "ArcGIS"
            },
            {
                "name": "Zonificacion",
                "description": "Zonificación de áreas protegidas",
                "url": "http://mapas.parquesnacionales.gov.co/arcgis/rest/services/pnn/zonificacion/FeatureServer",
                "type": "ArcGIS"
            }
        ]

        # Guardar servicios (como lo hace query.py)
        ctx.set_variable("found_services", found_services)
        ctx.add_assistant_message(f"Encontré {len(found_services)} datasets")

        # === TURNO 2: Usuario selecciona por número ===
        ctx2 = manager.get_or_create_session(session_id)
        ctx2.add_user_message("2")

        # Obtener servicios del turno anterior
        services = ctx2.get_variable("found_services")

        # VALIDACIÓN CRÍTICA: Los servicios deben existir
        assert services is not None, "found_services DEBE persistir entre turnos"
        assert len(services) == 3, "Deben estar todos los servicios"

        # Seleccionar servicio por número
        selected_number = 2
        selected_idx = selected_number - 1  # 0-indexed
        selected = services[selected_idx]

        assert selected["name"] == "Limites_oficiales_Poligono"
        assert "parquesnacionales" in selected["url"]

    def test_new_search_replaces_services(self):
        """Una nueva búsqueda debe reemplazar los servicios anteriores."""
        manager = ConversationManager()
        session_id = "replace-test"
        ctx = manager.get_or_create_session(session_id)

        # Primera búsqueda
        ctx.set_variable("found_services", [{"name": "old_service"}])

        # Nueva búsqueda
        new_services = [{"name": "new_service_1"}, {"name": "new_service_2"}]
        ctx.set_variable("found_services", new_services)

        # Verificar que se reemplazaron
        current = ctx.get_variable("found_services")
        assert len(current) == 2
        assert current[0]["name"] == "new_service_1"


class TestSerializationRoundTrip:
    """Regresión B5: ``to_dict``/``from_dict`` perdían la capa activa.

    El round-trip omitía ``last_results``, ``last_geojson``,
    ``active_data_source`` y ``active_source_name`` — una sesión
    restaurada desde Redis perdía la capa activa que el smart-router
    necesita para follow-ups (symbology/spatial sobre "la capa cargada").
    """

    def _build_context(self):
        from geo_copilot.orchestrator.conversation import (
            ConversationContext,
            DataSource,
        )
        ctx = ConversationContext(session_id="rt-1")
        ctx.add_user_message("muestra las manzanas de Suba")
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [-74.1, 4.7]},
                    "properties": {"nombre": "Suba"},
                }
            ],
        }
        ctx.update_state(
            last_results={"rows": [{"nombre": "Suba", "area": 12.5}]},
            last_geojson=geojson,
            active_data_source=DataSource.EXTERNAL,
            active_source_name="Manzanas IGAC",
        )
        return ctx, geojson

    def test_round_trip_preserves_active_layer(self):
        from geo_copilot.orchestrator.conversation import (
            ConversationContext,
            DataSource,
        )
        ctx, geojson = self._build_context()

        restored = ConversationContext.from_dict(ctx.to_dict())

        assert restored.state.last_geojson == geojson
        assert restored.state.last_results == {
            "rows": [{"nombre": "Suba", "area": 12.5}]
        }
        assert restored.state.active_data_source is DataSource.EXTERNAL
        assert restored.state.active_source_name == "Manzanas IGAC"

    def test_round_trip_is_json_safe(self):
        """El dict debe sobrevivir json.dumps/loads (camino Redis real)."""
        import json

        from geo_copilot.orchestrator.conversation import (
            ConversationContext,
            DataSource,
        )
        ctx, geojson = self._build_context()

        payload = json.loads(json.dumps(ctx.to_dict(), default=str))
        restored = ConversationContext.from_dict(payload)

        assert restored.state.last_geojson == geojson
        assert restored.state.active_data_source is DataSource.EXTERNAL

    def test_round_trip_preserves_preferences(self):
        from geo_copilot.orchestrator.conversation import ConversationContext

        ctx = ConversationContext(session_id="rt-2")
        ctx.update_preferences(auto_visualize=False, require_approval=False)

        restored = ConversationContext.from_dict(ctx.to_dict())

        assert restored.preferences.auto_visualize is False
        assert restored.preferences.require_approval is False

    def test_from_dict_tolerates_unknown_data_source(self):
        from geo_copilot.orchestrator.conversation import (
            ConversationContext,
            DataSource,
        )
        ctx, _ = self._build_context()
        payload = ctx.to_dict()
        payload["state"]["active_data_source"] = "marciano"

        restored = ConversationContext.from_dict(payload)

        assert restored.state.active_data_source is DataSource.NONE


class TestHistorialConLoEntregado:
    """V5 F4: el LLM ve lo que cada turno ENTREGÓ, no solo lo que la respuesta afirma."""

    def test_la_respuesta_lleva_los_artefactos_entregados(self):
        ctx = ConversationContext(session_id="t")
        ctx.add_user_message("hazme un gráfico")
        ctx.add_assistant_message("He generado un gráfico y un coropleto.", artefactos=["table"])
        msgs = ctx.get_messages_for_llm()
        assert msgs[0]["content"] == "hazme un gráfico"
        assert msgs[1]["content"] == "[Entregado al usuario en este turno: tabla] He generado un gráfico y un coropleto."

    def test_un_turno_sin_artefactos_dice_solo_texto(self):
        ctx = ConversationContext(session_id="t")
        ctx.add_assistant_message("Para hacer un gráfico usa matplotlib…", artefactos=[])
        assert ctx.get_messages_for_llm()[0]["content"].startswith("[Entregado al usuario en este turno: solo texto] ")

    def test_nombres_legibles_y_sin_repetir(self):
        ctx = ConversationContext(session_id="t")
        ctx.add_assistant_message("Listo.", artefactos=["layer", "chart", "table", "table"])
        assert ctx.get_messages_for_llm()[0]["content"].startswith("[Entregado al usuario en este turno: capa en el mapa, gráfico, tabla] ")

    def test_mensajes_sin_el_dato_quedan_como_estaban(self):
        ctx = ConversationContext(session_id="t")
        ctx.add_assistant_message("Hola.")
        assert ctx.get_messages_for_llm()[0]["content"] == "Hola."

    def test_el_hecho_sobrevive_al_recorte_del_router(self):
        ctx = ConversationContext(session_id="t")
        ctx.add_assistant_message("x" * 1000, artefactos=["table"])
        assert "[Entregado al usuario en este turno: tabla]" in ctx.get_messages_for_llm()[0]["content"][:300]

    def test_sobrevive_a_serializar_la_sesion(self):
        ctx = ConversationContext(session_id="t")
        ctx.add_assistant_message("Listo.", artefactos=["chart"])
        otro = ConversationContext.from_dict(ctx.to_dict())
        assert otro.get_messages_for_llm()[0]["content"].startswith("[Entregado al usuario en este turno: gráfico] ")


class TestHistorialConLoIntentado:
    """FH.1 (V5): «prueba de nuevo» necesita saber qué se intentó y qué falló."""

    def test_la_respuesta_lleva_la_intencion_y_lo_que_fallo(self):
        ctx = ConversationContext(session_id="t")
        ctx.add_assistant_message("El servicio de imágenes tuvo un fallo interno.", artefactos=[],
                                  intent="connected_service", fallidas=["imagery__imagery_ndvi"])
        assert ctx.get_messages_for_llm()[0]["content"].startswith(
            "[Entregado al usuario en este turno: solo texto · intención: connected_service · "
            "FALLARON: imagery__imagery_ndvi] ")

    def test_sin_fallos_no_se_menciona(self):
        ctx = ConversationContext(session_id="t")
        ctx.add_assistant_message("Listo.", artefactos=["layer"], intent="query_data", fallidas=[])
        assert "FALLARON" not in ctx.get_messages_for_llm()[0]["content"]
