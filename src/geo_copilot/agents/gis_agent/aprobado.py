"""Lo ya APROBADO por el usuario (HITL): ejecutar el SQL o el código que aprobó.

Salió de `GISAgent` (F4 del plan de calidad: agent.py tenía 1.101 líneas), tal cual.
"""

from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.gis_agent.agent")


class AprobadoMixin:
    """Ejecutar lo que el usuario ya aprobó: SQL (validado de nuevo) o código (en el sandbox)."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        db_connection: Any
        sql_validator: Any

        async def _execute_sql(self, sql: str, params: dict | None = None) -> tuple[list[dict], dict | None]: ...

    async def execute_approved_sql(self, sql: str) -> dict[str, Any]:
        """
        Ejecutar SQL que ya fue aprobado por HITL.

        Args:
            sql: Consulta SQL pre-aprobada

        Returns:
            Resultados de la ejecución
        """
        # Validación básica (ya fue aprobado, solo verificar sintaxis)
        validation = self.sql_validator.validate(sql)
        if not validation["is_valid"]:
            return {
                "success": False,
                "message": "SQL validation failed",
                "errors": validation["errors"]
            }

        if not self.db_connection:
            # Sin conexión a DB, simular resultado
            return {
                "success": True,
                "message": "Query validated (no DB connection)",
                "sql": sql,
                "row_count": 0,
                "results": [],
                "stats": {"feature_count": 0},
                "geojson": {"type": "FeatureCollection", "features": []},
            }

        try:
            # C3b-1: desempaquetar la tupla (results, geojson). Antes se
            # hacía len()/_results_to_geojson sobre la tupla → row_count
            # siempre 2 y geojson construido desde [list, dict] (basura).
            results, geojson = await self._execute_sql(sql)
            return {
                "success": True,
                "message": "Query executed successfully",
                "sql": sql,
                "row_count": len(results),
                "results": results,
                "stats": {"feature_count": len(results)},
                "geojson": geojson,
            }
        except Exception as e:
            logger.error(f"Approved SQL execution failed: {e}", exc_info=True)
            return {
                "success": False,
                "message": f"Execution failed: {str(e)}",
                "sql": sql
            }

    async def execute_approved_code(self, code: str) -> dict[str, Any]:
        """
        Ejecutar código Python ya aprobado por HITL.

        Reescrito en Fase 1: la versión anterior llamaba a ``validate_code``
        (método inexistente — el real es ``analyze_security``) y a
        ``execute`` sin ``await``. El método fallaba en cada invocación.
        """
        from geo_copilot.agents.gis_agent.sandbox import PythonSandbox

        sandbox = PythonSandbox()
        validation = sandbox.analyze_security(code)
        if not validation["is_safe"]:
            return {
                "success": False,
                "message": "Code validation failed",
                "errors": validation["violations"],
            }

        try:
            # Approval ya ocurrió aguas arriba; el sandbox no debe pedirla otra vez.
            result = await sandbox.execute(code, require_approval=False)
            return {
                "success": result.get("success", False),
                "message": (
                    "Code executed successfully"
                    if result.get("success")
                    else result.get("error", "Execution failed")
                ),
                "output": result.get("output"),
                "results": result.get("results"),
            }
        except Exception as e:
            logger.error(f"Approved code execution failed: {e}", exc_info=True)
            return {"success": False, "message": f"Execution failed: {e}"}
