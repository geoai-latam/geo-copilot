"""
Tests para Self-Correction (Fase 1).

Valida:
1. CodeCorrector - corrección de código Python
2. SQLCorrector - corrección de consultas SQL
3. Análisis de errores
4. Integración con LLM
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.agents.gis_agent.sql_corrector import SQLCorrector, correct_sql
from geo_copilot.agents.python_agent.code_corrector import CodeCorrector, correct_code


class TestCodeCorrector:
    """Tests para CodeCorrector."""

    @pytest.fixture
    def mock_llm(self):
        """Mock del cliente LLM."""
        mock = AsyncMock()
        mock.chat = AsyncMock()
        return mock

    @pytest.fixture
    def corrector(self, mock_llm):
        """Crear CodeCorrector con LLM mock."""
        return CodeCorrector(llm_client=mock_llm)

    @pytest.mark.asyncio
    async def test_correct_code_success(self, corrector, mock_llm):
        """Debe corregir código exitosamente."""
        mock_llm.chat.return_value = MagicMock(
            content="""
result = gdf.copy()
result['geometry'] = result.geometry.buffer(500)
"""
        )

        corrected = await corrector.correct_code(
            code="result = gdf.buffer(500)",
            error="AttributeError: 'GeoDataFrame' object has no attribute 'buffer'",
            columns=["id", "name", "geometry"],
            feature_count=10
        )

        assert corrected is not None
        assert "result" in corrected
        assert "buffer" in corrected

    @pytest.mark.asyncio
    async def test_correct_code_no_llm(self):
        """Sin LLM debe retornar None."""
        corrector = CodeCorrector(llm_client=None)
        result = await corrector.correct_code("code", "error")
        assert result is None

    @pytest.mark.asyncio
    async def test_correct_code_empty_input(self, corrector):
        """Input vacío debe retornar None."""
        result = await corrector.correct_code("", "error")
        assert result is None

        result = await corrector.correct_code("code", "")
        assert result is None

    @pytest.mark.asyncio
    async def test_correct_code_no_correction_possible(self, corrector, mock_llm):
        """Cuando LLM no puede corregir, debe retornar None."""
        mock_llm.chat.return_value = MagicMock(content="NO_CORRECTION_POSSIBLE")

        result = await corrector.correct_code("bad code", "complex error")
        assert result is None

    @pytest.mark.asyncio
    async def test_correct_code_cleans_markdown(self, corrector, mock_llm):
        """Debe limpiar marcadores markdown del código."""
        mock_llm.chat.return_value = MagicMock(
            content="```python\nresult = gdf.copy()\n```"
        )

        corrected = await corrector.correct_code("code", "error", [], 0)
        assert "```" not in corrected

    @pytest.mark.asyncio
    async def test_correct_code_validates_result_variable(self, corrector, mock_llm):
        """Código sin 'result' debe ser rechazado."""
        mock_llm.chat.return_value = MagicMock(
            content="output = gdf.copy()"  # No usa 'result'
        )

        corrected = await corrector.correct_code("code", "error", [], 0)
        assert corrected is None

    def test_is_valid_python_valid(self, corrector):
        """Código Python válido debe retornar True."""
        assert corrector._is_valid_python("x = 1") is True
        assert corrector._is_valid_python("def foo():\n    pass") is True

    def test_is_valid_python_invalid(self, corrector):
        """Código Python inválido debe retornar False."""
        assert corrector._is_valid_python("def foo(") is False
        assert corrector._is_valid_python("if x = 1:") is False

    def test_analyze_error_key_error(self, corrector):
        """Debe clasificar KeyError correctamente."""
        analysis = corrector.analyze_error("KeyError: 'column_name'")
        assert analysis["type"] == "key_error"
        assert analysis["correctable"] is True

    def test_analyze_error_buffer_type_error(self, corrector):
        """Debe clasificar error de buffer correctamente."""
        analysis = corrector.analyze_error("TypeError: buffer() requires numeric input")
        assert analysis["type"] == "buffer_type_error"
        assert "UTM" in analysis["suggestion"]

    def test_analyze_error_geometry_error(self, corrector):
        """Debe clasificar error de geometría correctamente."""
        analysis = corrector.analyze_error("TopologyException: invalid geometry")
        assert analysis["type"] == "geometry_error"

    def test_analyze_error_memory_error(self, corrector):
        """Error de memoria no debe ser corregible."""
        analysis = corrector.analyze_error("MemoryError: out of memory")
        assert analysis["type"] == "memory_error"
        assert analysis["correctable"] is False


class TestSQLCorrector:
    """Tests para SQLCorrector."""

    @pytest.fixture
    def mock_llm(self):
        """Mock del cliente LLM."""
        mock = AsyncMock()
        mock.chat = AsyncMock()
        return mock

    @pytest.fixture
    def corrector(self, mock_llm):
        """Crear SQLCorrector con LLM mock."""
        return SQLCorrector(llm_client=mock_llm)

    @pytest.mark.asyncio
    async def test_correct_sql_success(self, corrector, mock_llm):
        """Debe corregir SQL exitosamente."""
        mock_llm.chat.return_value = MagicMock(
            content='SELECT "CODIGO" FROM parcelas LIMIT 1000'
        )

        corrected = await corrector.correct_sql(
            sql="SELECT CODIGO FROM parcelas",
            error='column "codigo" does not exist',
            schema="Table: parcelas\nColumns: CODIGO, NOMBRE",
            query="muestra parcelas"
        )

        assert corrected is not None
        assert "CODIGO" in corrected
        assert "LIMIT" in corrected

    @pytest.mark.asyncio
    async def test_correct_sql_no_llm(self):
        """Sin LLM debe retornar None."""
        corrector = SQLCorrector(llm_client=None)
        result = await corrector.correct_sql("sql", "error")
        assert result is None

    @pytest.mark.asyncio
    async def test_correct_sql_empty_input(self, corrector):
        """Input vacío debe retornar None."""
        result = await corrector.correct_sql("", "error")
        assert result is None

        result = await corrector.correct_sql("sql", "")
        assert result is None

    @pytest.mark.asyncio
    async def test_correct_sql_no_correction_possible(self, corrector, mock_llm):
        """Cuando LLM no puede corregir, debe retornar None."""
        mock_llm.chat.return_value = MagicMock(content="NO_CORRECTION_POSSIBLE")

        result = await corrector.correct_sql("bad sql", "complex error")
        assert result is None

    @pytest.mark.asyncio
    async def test_correct_sql_cleans_markdown(self, corrector, mock_llm):
        """Debe limpiar marcadores markdown del SQL."""
        mock_llm.chat.return_value = MagicMock(
            content="```sql\nSELECT * FROM tabla LIMIT 100\n```"
        )

        corrected = await corrector.correct_sql("sql", "error")
        assert "```" not in corrected

    @pytest.mark.asyncio
    async def test_correct_sql_validates_is_sql(self, corrector, mock_llm):
        """Respuesta sin SELECT/WITH debe ser rechazada."""
        mock_llm.chat.return_value = MagicMock(
            content="This is not SQL at all"
        )

        corrected = await corrector.correct_sql("sql", "error")
        assert corrected is None

    @pytest.mark.asyncio
    async def test_correct_sql_same_as_original(self, corrector, mock_llm):
        """Si corrección es igual al original, debe retornar None."""
        original = "SELECT * FROM tabla"
        mock_llm.chat.return_value = MagicMock(content=original)

        corrected = await corrector.correct_sql(original, "error")
        assert corrected is None

    def test_analyze_error_column_not_found(self, corrector):
        """Debe clasificar error de columna no encontrada."""
        analysis = corrector.analyze_error('column "codigo" does not exist')
        assert analysis["type"] == "column_not_found"
        assert analysis["correctable"] is True

    def test_analyze_error_table_not_found(self, corrector):
        """Debe clasificar error de tabla no encontrada."""
        analysis = corrector.analyze_error('relation "tabla" does not exist')
        assert analysis["type"] == "table_not_found"

    def test_analyze_error_syntax_error(self, corrector):
        """Debe clasificar error de sintaxis."""
        analysis = corrector.analyze_error('syntax error at or near "SELEC"')
        assert analysis["type"] == "syntax_error"

    def test_analyze_error_permission_not_correctable(self, corrector):
        """Error de permisos no debe ser corregible."""
        analysis = corrector.analyze_error('permission denied for table users')
        assert analysis["type"] == "permission_error"
        assert analysis["correctable"] is False

    def test_analyze_error_timeout(self, corrector):
        """Debe clasificar error de timeout."""
        analysis = corrector.analyze_error('canceling statement due to statement timeout')
        assert analysis["type"] == "timeout"
        assert "LIMIT" in analysis["suggestion"]


class TestConvenienceFunctions:
    """Tests para funciones de conveniencia."""

    @pytest.mark.asyncio
    async def test_correct_code_function(self):
        """Función correct_code() debe funcionar."""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="result = gdf.copy()")

        result = await correct_code(
            llm=mock_llm,
            code="bad code",
            error="error msg",
            columns=["col1"],
            feature_count=5
        )
        assert result is not None

    @pytest.mark.asyncio
    async def test_correct_sql_function(self):
        """Función correct_sql() debe funcionar."""
        mock_llm = AsyncMock()
        mock_llm.chat.return_value = MagicMock(content="SELECT * FROM t LIMIT 100")

        result = await correct_sql(
            llm=mock_llm,
            sql="bad sql",
            error="error msg",
            schema="schema info",
            query="user query"
        )
        assert result is not None


class TestCorrectorIntegration:
    """Tests de integración para correctores."""

    @pytest.mark.asyncio
    async def test_code_correction_flow(self):
        """Test del flujo completo de corrección de código."""
        mock_llm = AsyncMock()

        # Simular primera llamada con error
        # Luego corrección exitosa
        mock_llm.chat.return_value = MagicMock(
            content="""
# Código corregido
gdf_utm = gdf.copy().to_crs('EPSG:32618')
gdf_utm['geometry'] = gdf_utm.geometry.buffer(500)
result = gdf_utm.to_crs('EPSG:4326')
"""
        )

        corrector = CodeCorrector(llm_client=mock_llm)
        original_code = "result = gdf.buffer(500)"
        error = "CRS mismatch: cannot buffer in degrees"

        corrected = await corrector.correct_code(
            code=original_code,
            error=error,
            columns=["id", "geometry"],
            feature_count=10
        )

        assert corrected is not None
        assert "to_crs" in corrected
        assert "result" in corrected
        assert corrected != original_code

    @pytest.mark.asyncio
    async def test_sql_correction_flow(self):
        """Test del flujo completo de corrección de SQL."""
        mock_llm = AsyncMock()

        mock_llm.chat.return_value = MagicMock(
            content='SELECT "CODIGO", "NOMBRE" FROM public.parcelas WHERE "CODIGO" IS NOT NULL LIMIT 1000'
        )

        corrector = SQLCorrector(llm_client=mock_llm)
        original_sql = "SELECT codigo, nombre FROM parcelas"
        error = 'column "codigo" does not exist'

        corrected = await corrector.correct_sql(
            sql=original_sql,
            error=error,
            schema='Table: public.parcelas\nColumns: "CODIGO" (varchar), "NOMBRE" (varchar)',
            query="muestra código y nombre de parcelas"
        )

        assert corrected is not None
        assert '"CODIGO"' in corrected
        assert "LIMIT" in corrected
        assert corrected.upper() != original_sql.upper()
