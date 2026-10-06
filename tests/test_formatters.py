"""
Tests para funciones de formateo en geo_copilot.core.formatters.

Valida:
1. format_found_services - formateo de lista de servicios
2. format_external_data_context - contexto de datos externos
3. format_plan_steps - pasos de plan multi-step
4. format_error_for_user - errores amigables
"""

import pytest

from geo_copilot.core.formatters import (
    format_error_for_user,
    format_external_data_context,
    format_found_services,
    format_plan_steps,
)


class TestFormatFoundServices:
    """Tests para format_found_services."""

    def test_format_single_service(self):
        """Debe formatear un solo servicio correctamente."""
        services = [{"name": "bomberos"}]
        result = format_found_services(services)
        assert "1. bomberos" in result
        assert "SERVICIOS ENCONTRADOS" in result

    def test_format_multiple_services(self):
        """Debe formatear múltiples servicios con números."""
        services = [
            {"name": "bomberos"},
            {"name": "hospitales"},
            {"name": "colegios"},
        ]
        result = format_found_services(services)
        assert "1. bomberos" in result
        assert "2. hospitales" in result
        assert "3. colegios" in result

    def test_empty_list_returns_empty(self):
        """Lista vacía debe retornar string vacío."""
        result = format_found_services([])
        assert result == ""

    def test_none_returns_empty(self):
        """None debe retornar string vacío."""
        result = format_found_services(None)
        assert result == ""

    def test_uses_title_if_no_name(self):
        """Debe usar 'title' si no hay 'name'."""
        services = [{"title": "Estaciones de Bomberos"}]
        result = format_found_services(services)
        assert "Estaciones de Bomberos" in result

    def test_max_services_limit(self):
        """Debe respetar el límite máximo de servicios."""
        services = [{"name": f"service_{i}"} for i in range(20)]
        result = format_found_services(services, max_services=5)
        assert "1. service_0" in result
        assert "5. service_4" in result
        assert "6. service_5" not in result

    def test_missing_name_and_title(self):
        """Servicio sin name ni title debe mostrar 'Sin nombre'."""
        services = [{"id": 123}]
        result = format_found_services(services)
        assert "Sin nombre" in result


class TestFormatExternalDataContext:
    """Tests para format_external_data_context."""

    def test_with_external_data(self):
        """Debe formatear contexto cuando hay datos externos."""
        result = format_external_data_context(
            has_external_data=True,
            source_name="bomberos",
            feature_count=50
        )
        assert "bomberos" in result
        assert "50" in result
        assert "DATOS EXTERNOS CARGADOS" in result

    def test_no_external_data(self):
        """Sin datos externos debe retornar vacío."""
        result = format_external_data_context(has_external_data=False)
        assert result == ""

    def test_default_values(self):
        """Debe usar valores por defecto correctamente."""
        result = format_external_data_context(has_external_data=True)
        assert "servicio externo" in result
        assert "0" in result  # feature_count default

    def test_includes_operations_list(self):
        """Debe incluir lista de operaciones disponibles."""
        result = format_external_data_context(has_external_data=True)
        assert "buffer" in result
        assert "centroid" in result
        assert "union" in result


class TestFormatPlanSteps:
    """Tests para format_plan_steps."""

    def test_format_simple_steps(self):
        """Debe formatear pasos simples correctamente."""
        steps = [
            {"description": "Buscar bomberos"},
            {"description": "Aplicar buffer"},
        ]
        result = format_plan_steps(steps)
        assert "Paso 1: Buscar bomberos" in result
        assert "Paso 2: Aplicar buffer" in result

    def test_empty_steps(self):
        """Lista vacía debe retornar mensaje apropiado."""
        result = format_plan_steps([])
        assert "Sin pasos" in result

    def test_with_status_icons(self):
        """Debe mostrar iconos de estado cuando se solicita."""
        steps = [
            {"description": "Buscar", "status": "completed"},
            {"description": "Procesar", "status": "in_progress"},
            {"description": "Finalizar", "status": "pending"},
        ]
        result = format_plan_steps(steps, include_status=True)
        assert "✓" in result  # completed
        assert "►" in result  # in_progress
        assert "○" in result  # pending

    def test_uses_query_fragment_if_no_description(self):
        """Debe usar query_fragment si no hay description."""
        steps = [{"query_fragment": "busca parques"}]
        result = format_plan_steps(steps)
        assert "busca parques" in result

    def test_failed_status_icon(self):
        """Estado failed debe mostrar ✗."""
        steps = [{"description": "Falló", "status": "failed"}]
        result = format_plan_steps(steps, include_status=True)
        assert "✗" in result


class TestFormatErrorForUser:
    """Tests para format_error_for_user."""

    def test_timeout_error(self):
        """Error de timeout debe dar mensaje amigable."""
        result = format_error_for_user("Connection timeout after 30s")
        assert "tardó demasiado" in result.lower()

    def test_connection_error(self):
        """Error de conexión debe dar mensaje amigable."""
        result = format_error_for_user("Connection error: server unavailable")
        assert "conectar" in result.lower()

    def test_permission_error(self):
        """Error de permisos debe dar mensaje amigable."""
        result = format_error_for_user("Permission denied for resource")
        assert "permisos" in result.lower()

    def test_not_found_error(self):
        """Error not found debe dar mensaje amigable."""
        result = format_error_for_user("Resource not found")
        assert "encontró" in result.lower()

    def test_generic_error(self):
        """Error genérico debe incluir el mensaje original."""
        result = format_error_for_user("Some weird error XYZ")
        assert "error" in result.lower()

    def test_long_error_truncated(self):
        """Errores muy largos deben truncarse."""
        long_error = "A" * 200
        result = format_error_for_user(long_error)
        assert len(result) < 200

    def test_without_suggestion(self):
        """Sin sugerencia solo muestra error."""
        result = format_error_for_user("Error 123", include_suggestion=False)
        assert "intenta" not in result.lower()
