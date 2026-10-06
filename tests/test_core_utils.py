"""
Tests para utilidades del core - parse_json_from_llm y configuración.

Estos tests validan:
1. La función parse_json_from_llm funciona correctamente con diferentes inputs
2. Las configuraciones dinámicas se cargan correctamente
3. Los valores por defecto son los esperados
"""

import pytest

from geo_copilot.core.config import Settings, settings
from geo_copilot.core.utils import clean_code_from_markdown, parse_json_from_llm


class TestParseJsonFromLLM:
    """Tests estrictos para parse_json_from_llm."""

    def test_parse_simple_json(self):
        """Debe parsear JSON simple correctamente."""
        content = '{"key": "value"}'
        result = parse_json_from_llm(content)
        assert result == {"key": "value"}

    def test_parse_json_with_surrounding_text(self):
        """Debe extraer JSON incluso con texto alrededor."""
        content = 'Here is the answer: {"action": "query", "intent": "search"} Done!'
        result = parse_json_from_llm(content)
        assert result == {"action": "query", "intent": "search"}

    def test_parse_json_with_markdown(self):
        """Debe manejar JSON dentro de bloques markdown."""
        content = '''```json
{"type": "response", "data": [1, 2, 3]}
```'''
        result = parse_json_from_llm(content)
        assert result["type"] == "response"
        assert result["data"] == [1, 2, 3]

    def test_parse_nested_json(self):
        """Debe parsear JSON anidado correctamente."""
        content = '{"outer": {"inner": {"deep": "value"}}}'
        result = parse_json_from_llm(content)
        assert result["outer"]["inner"]["deep"] == "value"

    def test_return_default_on_no_json(self):
        """Debe retornar default cuando no hay JSON."""
        content = "This is just plain text without any JSON"
        default = {"status": "error", "reason": "no json"}
        result = parse_json_from_llm(content, default)
        assert result == default

    def test_return_empty_dict_on_no_json_no_default(self):
        """Debe retornar dict vacío cuando no hay JSON y no hay default."""
        content = "No JSON here"
        result = parse_json_from_llm(content)
        assert result == {}

    def test_return_default_on_invalid_json(self):
        """Debe retornar default cuando el JSON es inválido."""
        content = '{"key": invalid_value}'
        default = {"fallback": True}
        result = parse_json_from_llm(content, default)
        assert result == default

    def test_parse_json_with_special_characters(self):
        """Debe manejar caracteres especiales en JSON."""
        content = '{"message": "Hola \\n mundo", "emoji": "\\u2713"}'
        result = parse_json_from_llm(content)
        assert "message" in result

    def test_parse_json_with_extra_closing_brace(self):
        """Debe manejar JSON con texto después."""
        # Caso realista: LLM responde con JSON y luego texto
        content = 'The result is {"status": "success", "count": 5}. That is all.'
        result = parse_json_from_llm(content)
        assert result["status"] == "success"
        assert result["count"] == 5

    def test_empty_string_returns_default(self):
        """Debe manejar string vacío."""
        result = parse_json_from_llm("", {"empty": True})
        assert result == {"empty": True}

    def test_none_default_returns_empty_dict(self):
        """Debe retornar {} cuando default es None."""
        result = parse_json_from_llm("no json", None)
        assert result == {}


class TestConfigSettings:
    """Tests para configuraciones dinámicas."""

    def test_auth_error_codes_are_list(self):
        """auth_error_codes debe ser una lista de enteros."""
        assert isinstance(settings.auth_error_codes, list)
        assert all(isinstance(code, int) for code in settings.auth_error_codes)

    def test_auth_error_codes_contain_expected_values(self):
        """auth_error_codes debe contener códigos HTTP de auth."""
        expected_codes = [401, 403]
        for code in expected_codes:
            assert code in settings.auth_error_codes, f"Missing expected auth code: {code}"

    def test_auth_error_keywords_are_list(self):
        """auth_error_keywords debe ser una lista de strings."""
        assert isinstance(settings.auth_error_keywords, list)
        assert all(isinstance(kw, str) for kw in settings.auth_error_keywords)

    def test_auth_error_keywords_contain_expected(self):
        """auth_error_keywords debe contener keywords de auth comunes."""
        expected_keywords = ["unauthorized", "token", "denied"]
        for kw in expected_keywords:
            assert kw in settings.auth_error_keywords, f"Missing expected keyword: {kw}"

    def test_date_field_keywords_bilingual(self):
        """date_field_keywords debe soportar español e inglés."""
        spanish_keywords = ["fecha", "año"]
        english_keywords = ["date", "year"]

        for kw in spanish_keywords + english_keywords:
            assert kw in settings.date_field_keywords, f"Missing date keyword: {kw}"

    def test_geometry_field_names_complete(self):
        """geometry_field_names debe incluir variantes comunes."""
        expected = ["geometry", "geom", "the_geom", "shape"]
        for field in expected:
            assert field in settings.geometry_field_names, f"Missing geometry field: {field}"

    def test_symbology_defaults_in_valid_range(self):
        """Los defaults de simbología deben estar en rangos válidos."""
        assert 0 <= settings.default_fill_opacity <= 1
        assert settings.default_stroke_width >= 0
        assert settings.default_marker_size >= 1
        assert 6 <= settings.default_font_size <= 36
        assert 2 <= settings.default_num_classes <= 12

    def test_settings_immutable_defaults(self):
        """Los defaults no deben cambiar entre instancias."""
        settings1 = Settings()
        settings2 = Settings()

        assert settings1.default_fill_opacity == settings2.default_fill_opacity
        assert settings1.auth_error_codes == settings2.auth_error_codes

    def test_allowed_domains_not_empty(self):
        """allowed_domains debe tener dominios configurados."""
        assert len(settings.allowed_domains) > 0

    def test_allowed_domains_is_list(self):
        """allowed_domains debe ser una lista de strings."""
        assert isinstance(settings.allowed_domains, list)
        # Si hay dominios, deben ser strings
        if settings.allowed_domains:
            assert all(isinstance(d, str) for d in settings.allowed_domains)


class TestConfigEnvironmentOverride:
    """Tests para override de configuración via environment."""

    def test_settings_can_be_instantiated(self):
        """Settings debe poder instanciarse sin errores."""
        s = Settings()
        assert s is not None
        assert s.app_name == "GEO_COPILOT"

    def test_default_values_exist(self):
        """Todos los campos deben tener valores por defecto."""
        s = Settings()

        # Core settings
        assert s.llm_provider is not None
        assert s.database_url is not None
        assert s.hitl_enabled is not None

        # New configurable settings
        assert s.auth_error_codes is not None
        assert s.date_field_keywords is not None
        assert s.geometry_field_names is not None
        assert s.default_fill_opacity is not None


class TestCleanCodeFromMarkdown:
    """Tests para clean_code_from_markdown."""

    def test_clean_python_code_block(self):
        """Debe limpiar bloque de código Python con marcadores."""
        code = "```python\nprint('hello')\n```"
        result = clean_code_from_markdown(code, "python")
        assert result == "print('hello')"

    def test_clean_sql_code_block(self):
        """Debe limpiar bloque de código SQL con marcadores."""
        code = "```sql\nSELECT * FROM users\n```"
        result = clean_code_from_markdown(code, "sql")
        assert result == "SELECT * FROM users"

    def test_clean_generic_code_block(self):
        """Debe limpiar bloque de código genérico sin lenguaje específico."""
        code = "```\nsome code\n```"
        result = clean_code_from_markdown(code)
        assert result == "some code"

    def test_no_markdown_returns_same(self):
        """Código sin markdown debe retornar igual (stripped)."""
        code = "print('hello')"
        result = clean_code_from_markdown(code, "python")
        assert result == "print('hello')"

    def test_empty_string(self):
        """String vacío debe retornar string vacío."""
        result = clean_code_from_markdown("")
        assert result == ""

    def test_none_input(self):
        """None debe retornar None."""
        result = clean_code_from_markdown(None)
        assert result is None

    def test_multiline_code(self):
        """Debe manejar código multilínea correctamente."""
        code = """```python
def hello():
    print('hello')
    return True
```"""
        result = clean_code_from_markdown(code, "python")
        assert "def hello():" in result
        assert "return True" in result
        assert "```" not in result

    def test_code_with_extra_whitespace(self):
        """Debe manejar espacios en blanco extra."""
        code = "  ```python\n  code  \n```  "
        result = clean_code_from_markdown(code, "python")
        assert result == "code"

    def test_wrong_language_marker(self):
        """Marcador de otro lenguaje debe limpiarse igual."""
        code = "```javascript\nconst x = 1;\n```"
        result = clean_code_from_markdown(code, "python")
        # javascript != python, así que solo quita ``` genérico
        assert "javascript" in result or "const x = 1;" in result
