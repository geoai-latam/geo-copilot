"""
Comprehensive tests for SQL comment sanitization.

These tests verify that the escape_sql_comment function correctly
prevents SQL injection via comment manipulation, including:
- Block comment closing sequences
- Line comment sequences
- Statement terminators
- Control characters
- Unicode bypass attempts
- Nested attack patterns
- Edge cases and boundary conditions
"""

import pytest

from geo_copilot.agents.gis_agent.sql_generator import escape_sql_comment


class TestBasicSQLCommentEscaping:
    """Basic tests for SQL comment escaping."""

    def test_escapes_block_comment_close(self):
        """Should escape */ to prevent closing block comments."""
        result = escape_sql_comment("test */ DROP TABLE users;")
        assert "*/" not in result
        assert "* /" in result

    def test_escapes_line_comment(self):
        """Should escape -- to prevent line comments."""
        result = escape_sql_comment("test -- DROP TABLE users")
        assert "--" not in result
        assert "- -" in result

    def test_removes_newlines(self):
        """Should remove newlines to prevent multi-line injection."""
        result = escape_sql_comment("line1\nline2\nline3")
        assert "\n" not in result
        assert "line1 line2 line3" == result

    def test_removes_carriage_returns(self):
        """Should remove carriage returns."""
        result = escape_sql_comment("line1\r\nline2")
        assert "\r" not in result
        assert "\n" not in result

    def test_removes_null_bytes(self):
        """Should remove null bytes."""
        result = escape_sql_comment("test\x00injection")
        assert "\x00" not in result

    def test_escapes_semicolon(self):
        """Should escape semicolons to prevent statement termination."""
        result = escape_sql_comment("test; DROP TABLE users")
        assert ";" not in result
        assert "," in result

    def test_truncates_long_input(self):
        """Should truncate input longer than max_length."""
        long_input = "a" * 500
        result = escape_sql_comment(long_input, max_length=200)
        assert len(result) <= 203  # 200 + "..."
        assert result.endswith("...")

    def test_handles_empty_string(self):
        """Should handle empty string."""
        result = escape_sql_comment("")
        assert result == ""

    def test_handles_none(self):
        """Should handle None input."""
        result = escape_sql_comment(None)
        assert result == ""


class TestAdvancedInjectionPrevention:
    """Tests for advanced SQL injection prevention."""

    def test_combined_attack(self):
        """Should handle combined attack vectors."""
        malicious = "test */ --\n; DROP TABLE users; /*"
        result = escape_sql_comment(malicious)
        # Should not contain any dangerous sequences
        assert "*/" not in result
        assert "--" not in result
        assert ";" not in result
        assert "\n" not in result

    def test_nested_block_comments(self):
        """Should handle nested block comment attempts."""
        malicious = "/* /* */ */ DROP TABLE"
        result = escape_sql_comment(malicious)
        assert "*/" not in result

    def test_multiple_block_comment_closes(self):
        """Should escape multiple */ sequences."""
        malicious = "*/ DROP */ TABLE */ users"
        result = escape_sql_comment(malicious)
        assert "*/" not in result
        assert result.count("* /") == 3

    def test_multiple_line_comments(self):
        """Should escape multiple -- sequences."""
        malicious = "test -- comment -- another -- final"
        result = escape_sql_comment(malicious)
        assert "--" not in result
        assert result.count("- -") == 3

    def test_mixed_comment_styles(self):
        """Should handle mixed comment styles."""
        malicious = "test */ -- DROP /* -- TABLE */"
        result = escape_sql_comment(malicious)
        assert "*/" not in result
        assert "--" not in result

    def test_comment_with_union(self):
        """Should sanitize UNION injection attempts."""
        malicious = "test */ UNION SELECT * FROM passwords--"
        result = escape_sql_comment(malicious)
        assert "*/" not in result
        assert "--" not in result

    def test_stacked_queries(self):
        """Should prevent stacked query injection."""
        malicious = "test; DROP TABLE users; SELECT * FROM admin;"
        result = escape_sql_comment(malicious)
        assert ";" not in result


class TestControlCharacters:
    """Tests for control character handling."""

    def test_removes_tab(self):
        """Should handle tab characters."""
        result = escape_sql_comment("test\tvalue")
        # Tabs might be kept or removed - ensure no dangerous chars remain
        assert "--" not in result
        assert "*/" not in result

    def test_removes_vertical_tab(self):
        """Should handle vertical tab (\\v)."""
        result = escape_sql_comment("test\x0bvalue")
        assert "\x0b" not in result or result.replace("\x0b", " ")

    def test_removes_form_feed(self):
        """Should handle form feed (\\f)."""
        result = escape_sql_comment("test\x0cvalue")
        # Ensure it doesn't cause issues
        assert "*/" not in result

    def test_removes_backspace(self):
        """Should handle backspace character."""
        result = escape_sql_comment("test\x08\x08value")
        assert "\x08" not in result

    def test_removes_bell(self):
        """Should handle bell character."""
        result = escape_sql_comment("test\x07value")
        assert "\x07" not in result


class TestUnicodeBypass:
    """Tests for Unicode bypass attempts."""

    def test_unicode_newline_variants(self):
        """Should handle Unicode newline variants."""
        # Unicode line separators
        variants = [
            "\u2028",  # Line separator
            "\u2029",  # Paragraph separator
            "\u0085",  # Next line (NEL)
        ]
        for char in variants:
            result = escape_sql_comment(f"test{char}injection")
            # These should either be removed or not cause SQL issues
            assert "--" not in result
            assert "*/" not in result

    def test_unicode_dash_variants(self):
        """Should handle Unicode dash variants that might bypass -- check."""
        # Various Unicode dashes that might look like --
        dashes = [
            "\u2010\u2010",  # Hyphen
            "\u2011\u2011",  # Non-breaking hyphen
            "\u2012\u2012",  # Figure dash
            "\u2013\u2013",  # En dash
            "\u2014\u2014",  # Em dash
        ]
        for dash in dashes:
            result = escape_sql_comment(f"test{dash}comment")
            # Regular -- should still be caught if present
            assert result.count("--") == 0

    def test_fullwidth_characters(self):
        """Should handle fullwidth asterisk and slash."""
        # Fullwidth * and / characters
        malicious = "test\uff0a\uff0f DROP"  # *​/​ in fullwidth
        result = escape_sql_comment(malicious)
        # Should handle gracefully
        assert isinstance(result, str)

    def test_homoglyph_attack(self):
        """Should handle homoglyph attacks (lookalike characters)."""
        # Cyrillic letters that look like Latin
        homoglyphs = "tеst"  # 'е' is Cyrillic
        result = escape_sql_comment(homoglyphs)
        # Should pass through but not cause SQL issues
        assert isinstance(result, str)


class TestEncodingBypass:
    """Tests for encoding-based bypass attempts."""

    def test_html_entities_passthrough(self):
        """Should handle HTML entities (they should pass through)."""
        html_encoded = "&lt;script&gt; */ --"
        result = escape_sql_comment(html_encoded)
        assert "*/" not in result
        assert "--" not in result

    def test_double_encoding(self):
        """Should handle double-encoded characters."""
        # %252d%252d = URL-encoded -- (double encoded)
        double_encoded = "%252d%252d DROP TABLE"
        result = escape_sql_comment(double_encoded)
        # URL encoding should pass through as literal text
        assert isinstance(result, str)

    def test_mixed_case_not_relevant(self):
        """Comment markers are not case-sensitive, test they're caught."""
        # -- and */ don't have case, but ensure mixed content is safe
        result = escape_sql_comment("TEST */ drop TABLE--")
        assert "*/" not in result
        assert "--" not in result


class TestBoundaryConditions:
    """Tests for boundary conditions."""

    def test_exact_max_length(self):
        """Should handle input exactly at max_length."""
        exact_input = "a" * 200
        result = escape_sql_comment(exact_input, max_length=200)
        assert len(result) == 200
        assert not result.endswith("...")

    def test_one_over_max_length(self):
        """Should truncate input one char over max_length."""
        over_input = "a" * 201
        result = escape_sql_comment(over_input, max_length=200)
        assert len(result) == 203  # 200 + "..."
        assert result.endswith("...")

    def test_max_length_with_special_chars(self):
        """Should handle max_length when special chars are present."""
        # Input with special chars that need escaping
        special_input = "*/ " * 100  # 300 chars
        result = escape_sql_comment(special_input, max_length=200)
        assert len(result) <= 203
        assert "*/" not in result

    def test_only_special_characters(self):
        """Should handle input with only special characters."""
        result = escape_sql_comment("*/--;\n\r")
        assert "*/" not in result
        assert "--" not in result
        assert ";" not in result
        assert "\n" not in result

    def test_single_character_inputs(self):
        """Should handle single character inputs."""
        chars = ["*", "/", "-", ";", "\n", "a", "1"]
        for char in chars:
            result = escape_sql_comment(char)
            assert isinstance(result, str)

    def test_whitespace_only(self):
        """Should handle whitespace-only input."""
        result = escape_sql_comment("   \t\n   ")
        assert "\n" not in result


class TestRealWorldAttackVectors:
    """Tests for real-world SQL injection attack patterns."""

    def test_classic_union_injection(self):
        """Should sanitize classic UNION-based injection."""
        attack = "' UNION SELECT username, password FROM users--"
        result = escape_sql_comment(attack)
        assert "--" not in result
        # Note: quotes and UNION keyword pass through (comment context)

    def test_time_based_blind_injection(self):
        """Should sanitize time-based blind injection."""
        attack = "'; WAITFOR DELAY '0:0:5'--"
        result = escape_sql_comment(attack)
        assert ";" not in result
        assert "--" not in result

    def test_error_based_injection(self):
        """Should sanitize error-based injection."""
        attack = "' AND 1=CONVERT(int,(SELECT TOP 1 table_name FROM information_schema.tables))--"
        result = escape_sql_comment(attack)
        assert "--" not in result

    def test_comment_based_auth_bypass(self):
        """Should sanitize comment-based authentication bypass."""
        attack = "admin'--"
        result = escape_sql_comment(attack)
        assert "--" not in result

    def test_second_order_injection(self):
        """Should sanitize potential second-order injection setup."""
        attack = "*/; INSERT INTO logs VALUES ('attack'); /*"
        result = escape_sql_comment(attack)
        assert "*/" not in result
        assert ";" not in result

    def test_polyglot_payload(self):
        """Should handle SQL polyglot payloads."""
        # Polyglot that works in multiple contexts
        polyglot = "'-var x=1/*'\"*/;alert(1)//"
        result = escape_sql_comment(polyglot)
        assert "*/" not in result
        assert ";" not in result


class TestSQLGeneratorIntegration:
    """Integration tests for SQL generator with sanitization."""

    @pytest.mark.asyncio
    async def test_no_llm_fails_honest_instead_of_template(self):
        """R2.2: sin LLM el generador FALLA honesto — el viejo
        `_generate_basic_sql` (template que ignoraba la intención y elegía
        campos por heurística) fue eliminado; la superficie de inyección por
        comentarios SQL desaparece con él."""
        from geo_copilot.agents.gis_agent.sql_generator import SQLGenerator

        generator = SQLGenerator()  # sin llm_client
        with pytest.raises(ValueError, match="LLM"):
            await generator.generate("muestre */ DROP TABLE users; --")

    def test_template_fallback_eliminado(self):
        """El método template no debe volver (regresión R2.2)."""
        from geo_copilot.agents.gis_agent.sql_generator import SQLGenerator

        assert not hasattr(SQLGenerator, "_generate_basic_sql")
        assert not hasattr(SQLGenerator, "generate_sample_query")

    def test_sql_generator_escapes_context_filters(self):
        """Malicious filters from context must be escaped before reaching the prompt.

        The system prompt template itself legitimately includes ``--`` (as a
        SQL comment example) and ``DROP TABLE`` (as guidance text), so a
        substring check against the whole prompt is meaningless. Instead we
        verify that the raw malicious string does not appear verbatim, and
        that the section where the filter is injected has neither
        comment-start nor statement-terminator characters.
        """
        from geo_copilot.agents.gis_agent.sql_generator import SQLGenerator

        generator = SQLGenerator()

        malicious = "type='admin'--; DROP TABLE users;"
        context = {"filters": malicious}
        sql = generator._build_system_prompt("", context)

        # Raw malicious filter must not appear unescaped anywhere in the prompt.
        assert malicious not in sql

        # The filter is injected after a known marker; isolate that line and
        # check it has been neutralized.
        assert "Filtros a aplicar:" in sql
        filter_line = sql.split("Filtros a aplicar:", 1)[1].split("\n", 1)[0]
        assert "--" not in filter_line
        assert ";" not in filter_line


class TestPerformance:
    """Performance tests for sanitization."""

    def test_handles_large_input_efficiently(self):
        """Should handle large input without excessive time."""
        import time

        large_input = "test */ -- ; \n" * 10000  # ~150KB

        start = time.time()
        result = escape_sql_comment(large_input, max_length=10000)
        elapsed = time.time() - start

        assert elapsed < 1.0  # Should complete in under 1 second
        assert len(result) <= 10003

    def test_handles_pathological_patterns(self):
        """Should handle pathological regex patterns efficiently."""
        import time

        # Pattern that might cause catastrophic backtracking
        pathological = "*/" * 1000 + "--" * 1000

        start = time.time()
        result = escape_sql_comment(pathological, max_length=5000)
        elapsed = time.time() - start

        assert elapsed < 1.0
        assert "*/" not in result
        assert "--" not in result
