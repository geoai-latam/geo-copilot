"""Los MENSAJES del WebSocket: su forma (modelos pydantic), su validación y el error que se
le puede mostrar al cliente (sanitizado).

Salió de `websocket.py` (F4 del plan de calidad: 1.352 líneas), tal cual.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.api.websocket")


# QA-11: ``class Config`` está deprecado en Pydantic v2 y se eliminará en v3.
# Reemplazado por ``model_config = ConfigDict(...)``.
_WS_MSG_CONFIG = ConfigDict(extra="ignore")


def sanitize_error_for_client(error: Exception, include_type: bool = False) -> str:
    """
    Sanitiza errores antes de enviarlos al cliente.

    Evita exponer detalles internos del sistema como:
    - Paths de archivos
    - Nombres de tablas/columnas de DB
    - Stack traces
    - Información de configuración
    """
    safe_messages = {
        "ConnectionError": "Error de conexión con el servidor",
        "TimeoutError": "La operación tardó demasiado tiempo",
        "asyncio.TimeoutError": "La operación tardó demasiado tiempo",
        "ValidationError": "Los datos proporcionados no son válidos",
        "JSONDecodeError": "Error al procesar el mensaje",
        "WebSocketDisconnect": "Conexión cerrada",
        "ValueError": "Valor no válido proporcionado",
        "KeyError": "Datos incompletos en la solicitud",
    }

    error_type = type(error).__name__
    if error_type in safe_messages:
        return safe_messages[error_type]

    # Para errores desconocidos, no exponer detalles
    if include_type:
        return f"Error interno ({error_type})"
    return "Error interno del servidor"


class WSQueryMessage(BaseModel):
    """Modelo de validación para mensajes de query."""
    model_config = _WS_MSG_CONFIG
    type: Literal["query"] = "query"
    data: dict = Field(default_factory=dict)


class WSApprovalMessage(BaseModel):
    """Modelo de validación para mensajes de aprobación."""
    model_config = _WS_MSG_CONFIG
    type: Literal["approval"] = "approval"
    data: dict = Field(default_factory=dict)


class WSCancelMessage(BaseModel):
    """Modelo de validación para mensajes de cancelación."""
    model_config = _WS_MSG_CONFIG
    type: Literal["cancel"] = "cancel"
    data: dict = Field(default_factory=dict)


class WSPingMessage(BaseModel):
    """Modelo de validación para mensajes ping."""
    model_config = _WS_MSG_CONFIG
    type: Literal["ping"] = "ping"
    data: dict = Field(default_factory=dict)


def validate_ws_message(data: dict) -> dict:
    """
    Valida y parsea mensaje WebSocket.

    Args:
        data: Diccionario con el mensaje recibido

    Returns:
        Diccionario validado

    Raises:
        ValueError: Si el tipo de mensaje no es soportado
        ValidationError: Si el mensaje no pasa validación
    """
    message_type = data.get("type", "").lower()

    validators = {
        "query": WSQueryMessage,
        "approval": WSApprovalMessage,
        "cancel": WSCancelMessage,
        "ping": WSPingMessage,
    }

    validator_class = validators.get(message_type)
    if not validator_class:
        raise ValueError(f"Tipo de mensaje no soportado: {message_type}")

    # Validar con Pydantic
    validated = validator_class(**data)
    datos: dict = validated.model_dump()
    return datos


class WSMessageType(str, Enum):
    """Tipos de mensajes WebSocket."""
    # Cliente -> Servidor
    QUERY = "query"
    APPROVAL = "approval"
    CANCEL = "cancel"
    PING = "ping"

    # Servidor -> Cliente
    STATUS = "status"
    PROGRESS = "progress"
    RESULT = "result"
    ERROR = "error"
    APPROVAL_REQUEST = "approval_request"
    PONG = "pong"

    # Autonomía: Progreso de reintentos y planificación
    RETRY_STARTED = "retry_started"          # Iniciando reintento
    RETRY_CORRECTION = "retry_correction"    # Mostrando corrección aplicada
    RETRY_SUCCESS = "retry_success"          # Reintento exitoso
    RETRY_FAILED = "retry_failed"            # Reintento fallido (max alcanzado)
    PLAN_CREATED = "plan_created"            # Plan multi-paso generado (Fase 2)
    STEP_STARTED = "step_started"            # Iniciando paso N del plan
    STEP_COMPLETED = "step_completed"        # Paso N completado
    EXECUTION_CANCELLED = "execution_cancelled"  # Ejecución cancelada con resultados parciales


class WSMessage(BaseModel):
    """Mensaje WebSocket."""
    type: WSMessageType
    data: dict[str, Any]
    timestamp: datetime | None = None

    def __init__(self, **data):
        if "timestamp" not in data or data["timestamp"] is None:
            data["timestamp"] = datetime.now()
        super().__init__(**data)
