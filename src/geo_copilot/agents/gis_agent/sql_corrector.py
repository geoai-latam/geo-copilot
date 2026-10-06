"""
SQL Corrector - Auto-corrección de consultas SQL usando LLM.

Analiza errores de SQL y genera consultas corregidas automáticamente.
"""

from geo_copilot.core.config import get_settings
from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import clean_code_from_markdown
from geo_copilot.prompts import cargar_prompt

logger = get_logger(__name__)

# =============================================================================
# Prompt para corrección de SQL
# =============================================================================

SQL_CORRECTION_PROMPT = cargar_prompt("corrector_sql")


# =============================================================================
# SQLCorrector class
# =============================================================================

class SQLCorrector:
    """
    Corrector de SQL usando LLM.

    Analiza errores de ejecución SQL y genera correcciones automáticas.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        """
        Inicializar corrector.

        Args:
            llm_client: Cliente LLM para generación de correcciones
        """
        self.llm_client = llm_client
        self.settings = get_settings()

    async def correct_sql(
        self,
        sql: str,
        error: str,
        schema: str = "",
        query: str = "",
    ) -> str | None:
        """
        Corregir SQL que generó un error.

        Args:
            sql: SQL original que falló
            error: Mensaje de error
            schema: Schema de la BD (opcional pero recomendado)
            query: Consulta original del usuario (para contexto)

        Returns:
            SQL corregido o None si no se pudo corregir
        """
        if not self.llm_client:
            logger.warning("[SQLCorrector] No LLM client available for correction")
            return None

        if not sql or not error:
            return None

        # F3.3: clasificar el error ANTES de gastar una llamada al LLM. Si el
        # análisis dice que NO es corregible reescribiendo SQL (p. ej. permisos),
        # no reintentamos a ciegas — devolvemos None y el bucle de retry para.
        analysis = self.analyze_error(error)
        if not analysis.get("correctable", True):
            logger.info(
                f"[SQLCorrector] error no corregible por reescritura "
                f"({analysis.get('type')}): {error[:80]} — sin reintento"
            )
            return None

        classification = f"Tipo: {analysis.get('type', 'unknown')}."
        if analysis.get("suggestion"):
            classification += f" {analysis['suggestion']}."

        # Construir sección de schema
        schema_section = ""
        if schema:
            schema_section = f"""SCHEMA DISPONIBLE:
{schema}
"""

        # Construir prompt
        prompt = SQL_CORRECTION_PROMPT.format(
            sql=sql,
            error=error,
            classification=classification,  # F3.3: orientación dirigida
            schema_section=schema_section,
            query=query or "No especificada",
        )

        try:
            response = await self.llm_client.chat([
                LLMMessage(role="user", content=prompt)
            ])

            corrected = response.content.strip()

            # Verificar si no pudo corregir
            if "NO_CORRECTION_POSSIBLE" in corrected:
                logger.info("[SQLCorrector] LLM could not correct the SQL")
                return None

            # Limpiar markdown si viene
            corrected = clean_code_from_markdown(corrected, "sql")

            # Validar que sea diferente al original
            if corrected.lower() == sql.lower():
                logger.info("[SQLCorrector] Corrected SQL is same as original")
                return None

            # Validación básica de que es SQL
            if not any(kw in corrected.upper() for kw in ["SELECT", "WITH"]):
                logger.warning("[SQLCorrector] Corrected content doesn't look like SQL")
                return None

            logger.info(f"[SQLCorrector] Generated correction:\n{corrected[:200]}...")
            return corrected

        except Exception as e:  # captura amplia a propósito: llamada al LLM (red/proveedor/timeout); sin corrección el SQL original sigue su curso
            logger.error(f"[SQLCorrector] Error during correction: {e}", exc_info=True)
            return None

    def analyze_error(self, error: str) -> dict:  # noqa: C901
        """
        Analizar un error SQL para clasificarlo.

        Args:
            error: Mensaje de error

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
        if "no disponible en el catálogo" in error_lower:
            # S0.2: el validador rechazó una tabla fuera del semantic layer.
            analysis["type"] = "table_not_found"
            analysis["suggestion"] = (
                "La tabla no está en el catálogo: corrige solo si otra tabla del "
                "schema representa la MISMA entidad"
            )
        elif "does not exist" in error_lower:
            if "column" in error_lower:
                analysis["type"] = "column_not_found"
                analysis["suggestion"] = "Verificar nombre de columna en el schema"
            elif "relation" in error_lower or "table" in error_lower:
                analysis["type"] = "table_not_found"
                analysis["suggestion"] = "Verificar nombre de tabla y schema"
            elif "function" in error_lower:
                analysis["type"] = "function_not_found"
                analysis["suggestion"] = "Verificar que la función PostGIS exista"

        elif "syntax error" in error_lower:
            analysis["type"] = "syntax_error"
            analysis["suggestion"] = "Corregir sintaxis SQL"

        elif "permission denied" in error_lower:
            analysis["type"] = "permission_error"
            analysis["correctable"] = False
            analysis["suggestion"] = "Error de permisos - requiere intervención manual"

        elif any(s in error_lower for s in (
            "could not connect", "server closed", "connection refused",
            "connection reset", "terminating connection", "out of memory",
        )):
            # F3.3: errores de infra/recursos — reescribir el SQL no los arregla,
            # así que no tiene sentido reintentar la corrección.
            analysis["type"] = "infrastructure_error"
            analysis["correctable"] = False
            analysis["suggestion"] = "Error de conexión/recursos - no corregible por reescritura"

        elif "timeout" in error_lower or "canceling" in error_lower:
            analysis["type"] = "timeout"
            analysis["suggestion"] = "Agregar LIMIT o simplificar consulta"

        elif "geography" in error_lower or "geometry" in error_lower:
            analysis["type"] = "geometry_error"
            analysis["suggestion"] = "Verificar transformación de coordenadas y tipos"

        elif "aggregate" in error_lower or "group by" in error_lower:
            analysis["type"] = "aggregation_error"
            analysis["suggestion"] = "Verificar GROUP BY y agregaciones"

        elif "null" in error_lower:
            analysis["type"] = "null_error"
            analysis["suggestion"] = "Agregar manejo de NULLs (COALESCE, NULLS LAST)"

        return analysis


# =============================================================================
# Convenience function for direct use
# =============================================================================

async def correct_sql(
    llm: LLMClient,
    sql: str,
    error: str,
    schema: str = "",
    query: str = "",
) -> str | None:
    """
    Función de conveniencia para corregir SQL.

    Args:
        llm: Cliente LLM
        sql: SQL original
        error: Error producido
        schema: Schema de BD
        query: Consulta del usuario

    Returns:
        SQL corregido o None
    """
    corrector = SQLCorrector(llm)
    return await corrector.correct_sql(sql, error, schema, query)
