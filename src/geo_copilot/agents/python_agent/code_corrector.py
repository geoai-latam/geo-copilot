"""
Code Corrector - Auto-corrección de código Python usando LLM.

Analiza errores de ejecución de código Python y genera correcciones automáticas.
"""

from geo_copilot.core.config import get_settings
from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import clean_code_from_markdown
from geo_copilot.prompts import cargar_prompt

logger = get_logger(__name__)

# =============================================================================
# Prompt para corrección de código Python
# =============================================================================

CODE_CORRECTION_PROMPT = cargar_prompt("corrector_python")


# =============================================================================
# CodeCorrector class
# =============================================================================

class CodeCorrector:
    """
    Corrector de código Python usando LLM.

    Analiza errores de ejecución y genera correcciones automáticas
    para operaciones espaciales con GeoPandas.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        """
        Inicializar corrector.

        Args:
            llm_client: Cliente LLM para generación de correcciones
        """
        self.llm_client = llm_client
        self.settings = get_settings()

    async def correct_code(
        self,
        code: str,
        error: str,
        columns: list[str] | None = None,
        feature_count: int = 0,
        query: str | None = None,
    ) -> str | None:
        """
        Corregir código Python que generó un error.

        Args:
            code: Código original que falló
            error: Mensaje de error o traceback
            columns: Lista de columnas disponibles en el GeoDataFrame
            feature_count: Número de features en el GeoDataFrame
            query: Solicitud original del usuario (R1.3 — el corrector debe
                conocer la intención para no corregir hacia otra cosa)

        Returns:
            Código corregido o None si no se pudo corregir
        """
        if not self.llm_client:
            logger.warning("[CodeCorrector] No LLM client available for correction")
            return None

        if not code or not error:
            return None

        # F3.3: clasificar el error antes de gastar una llamada al LLM. Si no es
        # corregible reescribiendo el código (p. ej. MemoryError: datos muy
        # grandes), no reintentamos a ciegas — el bucle de retry para.
        analysis = self.analyze_error(error)
        if not analysis.get("correctable", True):
            logger.info(
                f"[CodeCorrector] error no corregible por reescritura "
                f"({analysis.get('type')}): {error[:80]} — sin reintento"
            )
            return None

        classification = f"Tipo: {analysis.get('type', 'unknown')}."
        if analysis.get("suggestion"):
            classification += f" {analysis['suggestion']}."

        # Formatear columnas
        columns_str = ", ".join(columns) if columns else "No disponibles"

        # Construir prompt
        prompt = CODE_CORRECTION_PROMPT.format(
            query=query or "(no disponible — infiere la intención del código)",
            code=code,
            error=error,
            classification=classification,  # F3.3: orientación dirigida
            feature_count=feature_count,
            columns=columns_str,
        )

        try:
            response = await self.llm_client.chat([
                LLMMessage(role="user", content=prompt)
            ])

            corrected = response.content.strip()

            # Verificar si no pudo corregir
            if "NO_CORRECTION_POSSIBLE" in corrected:
                logger.info("[CodeCorrector] LLM could not correct the code")
                return None

            # Limpiar markdown si viene
            corrected = clean_code_from_markdown(corrected, "python")

            # Validar que sea diferente al original
            if corrected == code:
                logger.info("[CodeCorrector] Corrected code is same as original")
                return None

            # Validación básica de que es código Python válido
            if not self._is_valid_python(corrected):
                logger.warning("[CodeCorrector] Corrected content is not valid Python")
                return None

            # R1.3: verificar que el código ASIGNE al menos una salida del
            # contrato (result | table | stats | chart | summary), por AST —
            # el gate viejo por substring ("result" in corrected) rechazaba
            # correcciones analíticas válidas y se satisfacía con un comentario.
            if not self._assigns_output(corrected):
                logger.warning(
                    "[CodeCorrector] Corrected code doesn't assign any output "
                    "(result/table/stats/chart/summary)"
                )
                return None

            logger.info(f"[CodeCorrector] Generated correction:\n{corrected[:200]}...")
            return corrected

        except Exception as e:  # captura amplia a propósito: llamada al LLM (red/proveedor/timeout); None = sin corrección
            logger.error(f"[CodeCorrector] Error during correction: {e}", exc_info=True)
            return None

    # Salidas válidas del contrato del sandbox (mismo vocabulario que el
    # scaffolding de agent._execute_in_sandbox y prompts.SYSTEM_PROMPT).
    _OUTPUT_NAMES = frozenset({"result", "table", "stats", "chart", "summary"})

    @classmethod
    def _assigns_output(cls, code: str) -> bool:
        """¿El código asigna alguna variable de salida del contrato? (AST real,
        no substring — R1.3)."""
        import ast
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            targets: list = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            for t in targets:
                # Acepta `result = ...` y también `table, stats = ...`.
                names = [t] if not isinstance(t, (ast.Tuple, ast.List)) else list(t.elts)
                for n in names:
                    if isinstance(n, ast.Name) and n.id in cls._OUTPUT_NAMES:
                        return True
        return False

    def _is_valid_python(self, code: str) -> bool:
        """
        Verificar si el código es Python sintácticamente válido.

        Args:
            code: Código a verificar

        Returns:
            True si es válido
        """
        try:
            compile(code, "<string>", "exec")
            return True
        except SyntaxError:
            return False

    def analyze_error(self, error: str) -> dict:
        """
        Analizar un error de Python para clasificarlo.

        Args:
            error: Mensaje de error o traceback

        Returns:
            Diccionario con análisis del error
        """
        error_lower = error.lower()

        analysis = {
            "type": "unknown",
            "correctable": True,
            "suggestion": None,
        }

        # Clasificar tipo de error
        if "keyerror" in error_lower:
            analysis["type"] = "key_error"
            analysis["suggestion"] = "Columna no existe - usar columna disponible"

        elif "attributeerror" in error_lower:
            analysis["type"] = "attribute_error"
            analysis["suggestion"] = "Método o atributo no existe en el objeto"

        elif "typeerror" in error_lower:
            if "buffer" in error_lower:
                analysis["type"] = "buffer_type_error"
                analysis["suggestion"] = "Reproyectar a UTM para buffer en metros"
            else:
                analysis["type"] = "type_error"
                analysis["suggestion"] = "Error de tipos - verificar conversiones"

        elif "valueerror" in error_lower:
            if "crs" in error_lower:
                analysis["type"] = "crs_error"
                analysis["suggestion"] = "Error de CRS - verificar proyecciones"
            else:
                analysis["type"] = "value_error"
                analysis["suggestion"] = "Valor inválido para la operación"

        elif "geometryerror" in error_lower or "topologyexception" in error_lower:
            analysis["type"] = "geometry_error"
            analysis["suggestion"] = "Geometría inválida - usar make_valid()"

        elif "empty" in error_lower and "geometry" in error_lower:
            analysis["type"] = "empty_geometry"
            analysis["suggestion"] = "Filtrar geometrías vacías antes de operar"

        elif "memoryerror" in error_lower:
            analysis["type"] = "memory_error"
            analysis["correctable"] = False
            analysis["suggestion"] = "Datos muy grandes - reducir features"

        elif "indexerror" in error_lower:
            analysis["type"] = "index_error"
            analysis["suggestion"] = "Índice fuera de rango - verificar tamaño"

        return analysis


# =============================================================================
# Convenience function for direct use
# =============================================================================

async def correct_code(
    llm: LLMClient,
    code: str,
    error: str,
    columns: list[str] | None = None,
    feature_count: int = 0,
) -> str | None:
    """
    Función de conveniencia para corregir código Python.

    Args:
        llm: Cliente LLM
        code: Código original
        error: Error producido
        columns: Columnas disponibles
        feature_count: Número de features

    Returns:
        Código corregido o None
    """
    corrector = CodeCorrector(llm)
    return await corrector.correct_code(code, error, columns, feature_count)
