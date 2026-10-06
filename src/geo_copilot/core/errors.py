"""
Errores personalizados para GEO_COPILOT.

Todos los agentes deben usar estas clases para consistencia en:
- Mensajes de error al usuario
- Logging estructurado
- Manejo de reintentos
"""

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Códigos de error estandarizados."""

    # LLM Errors
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_PARSE_ERROR = "LLM_PARSE_ERROR"
    LLM_INVALID_RESPONSE = "LLM_INVALID_RESPONSE"
    LLM_RATE_LIMIT = "LLM_RATE_LIMIT"

    # Validation Errors
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_GEOJSON = "INVALID_GEOJSON"
    INVALID_SQL = "INVALID_SQL"
    INVALID_CODE = "INVALID_CODE"

    # Execution Errors
    SQL_EXECUTION_ERROR = "SQL_EXECUTION_ERROR"
    CODE_EXECUTION_ERROR = "CODE_EXECUTION_ERROR"
    EXTERNAL_API_ERROR = "EXTERNAL_API_ERROR"
    TIMEOUT_ERROR = "TIMEOUT_ERROR"

    # State Errors
    MISSING_DATA = "MISSING_DATA"
    INVALID_STATE = "INVALID_STATE"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"

    # Security Errors
    UNAUTHORIZED = "UNAUTHORIZED"
    RATE_LIMITED = "RATE_LIMITED"
    FORBIDDEN = "FORBIDDEN"


class GeocopilotError(Exception):
    """
    Error base con contexto estructurado.

    Attributes:
        message: Mensaje descriptivo del error
        code: Código de error estandarizado
        context: Contexto adicional para debugging (no expuesto al usuario)
        recoverable: Si el error puede recuperarse con reintento
        suggestion: Sugerencia para el usuario
    """

    def __init__(
        self,
        message: str,
        code: ErrorCode,
        context: dict[str, Any] | None = None,
        recoverable: bool = True,
        suggestion: str | None = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.context = context or {}
        self.recoverable = recoverable
        self.suggestion = suggestion

    def to_dict(self) -> dict[str, Any]:
        """Convertir a diccionario para logging/API."""
        return {
            "error": self.message,
            "code": self.code.value,
            "recoverable": self.recoverable,
            "suggestion": self.suggestion,
        }

    def to_user_message(self) -> str:
        """Mensaje seguro para mostrar al usuario."""
        if self.suggestion:
            return f"{self.message}. {self.suggestion}"
        return self.message

    def __str__(self) -> str:
        return f"[{self.code.value}] {self.message}"


class LLMError(GeocopilotError):
    """Errores relacionados con el LLM."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.LLM_PARSE_ERROR,
        context: dict[str, Any] | None = None,
        recoverable: bool = True,
        suggestion: str | None = None
    ):
        if suggestion is None:
            suggestion = "Intenta reformular tu consulta de manera más simple"
        super().__init__(message, code, context, recoverable, suggestion)


class ValidationError(GeocopilotError):
    """Errores de validación de datos."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.INVALID_INPUT,
        context: dict[str, Any] | None = None,
        recoverable: bool = False,
        suggestion: str | None = None
    ):
        if suggestion is None:
            suggestion = "Verifica los datos proporcionados"
        super().__init__(message, code, context, recoverable, suggestion)


class ExecutionError(GeocopilotError):
    """Errores de ejecución de código/SQL."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.SQL_EXECUTION_ERROR,
        context: dict[str, Any] | None = None,
        recoverable: bool = True,
        suggestion: str | None = None
    ):
        super().__init__(message, code, context, recoverable, suggestion)


class StateError(GeocopilotError):
    """Errores de estado del grafo."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.INVALID_STATE,
        context: dict[str, Any] | None = None,
        recoverable: bool = False,
        suggestion: str | None = None
    ):
        super().__init__(message, code, context, recoverable, suggestion)


class ExternalAPIError(GeocopilotError):
    """Errores de APIs externas."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.EXTERNAL_API_ERROR,
        context: dict[str, Any] | None = None,
        recoverable: bool = True,
        suggestion: str | None = None
    ):
        if suggestion is None:
            suggestion = "Intenta de nuevo o selecciona otro servicio"
        super().__init__(message, code, context, recoverable, suggestion)


class DataFetchError(GeocopilotError):
    """Errores al obtener datos externos (SSRF blocked, download failed, etc.)."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.EXTERNAL_API_ERROR,
        context: dict[str, Any] | None = None,
        recoverable: bool = True,
        suggestion: str | None = None
    ):
        if suggestion is None:
            suggestion = "Verifica la URL o intenta con otra fuente de datos"
        super().__init__(message, code, context, recoverable, suggestion)


class SQLExecutionError(ExecutionError):
    """Errores específicos de ejecución de SQL."""

    def __init__(
        self,
        message: str,
        sql: str | None = None,
        context: dict[str, Any] | None = None,
        recoverable: bool = True,
        suggestion: str | None = None
    ):
        ctx = context or {}
        if sql:
            ctx["sql_preview"] = sql[:200] if len(sql) > 200 else sql
        if suggestion is None:
            suggestion = "El sistema intentará corregir la consulta automáticamente"
        super().__init__(
            message,
            code=ErrorCode.SQL_EXECUTION_ERROR,
            context=ctx,
            recoverable=recoverable,
            suggestion=suggestion
        )


class AuthorizationError(GeocopilotError):
    """Errores de autorización y permisos."""

    def __init__(
        self,
        message: str,
        code: ErrorCode = ErrorCode.UNAUTHORIZED,
        context: dict[str, Any] | None = None,
        recoverable: bool = False,
        suggestion: str | None = None
    ):
        if suggestion is None:
            suggestion = "Verifica tus permisos o contacta al administrador"
        super().__init__(message, code, context, recoverable, suggestion)


class TimeoutError(GeocopilotError):
    """Errores de timeout en operaciones."""

    def __init__(
        self,
        message: str,
        operation: str | None = None,
        context: dict[str, Any] | None = None,
        recoverable: bool = True,
        suggestion: str | None = None
    ):
        ctx = context or {}
        if operation:
            ctx["operation"] = operation
        if suggestion is None:
            suggestion = "La operación tardó demasiado, intenta con una consulta más simple"
        super().__init__(
            message,
            code=ErrorCode.TIMEOUT_ERROR,
            context=ctx,
            recoverable=recoverable,
            suggestion=suggestion
        )


# =============================================================================
# Helpers
# =============================================================================

def create_error_response(error: GeocopilotError) -> dict:
    """Crea una respuesta de error estandarizada para agentes."""
    return {
        "success": False,
        "error": error.to_dict(),
        "message": error.to_user_message(),
    }


def wrap_exception(
    e: Exception,
    default_code: ErrorCode = ErrorCode.INVALID_STATE,
    default_message: str = "Error inesperado"
) -> GeocopilotError:
    """
    Envuelve una excepción estándar en un GeocopilotError.

    Útil para catch-all de excepciones manteniendo contexto.
    """
    if isinstance(e, GeocopilotError):
        return e

    return GeocopilotError(
        message=default_message,
        code=default_code,
        context={"original_error": str(e), "error_type": type(e).__name__},
        recoverable=False
    )
