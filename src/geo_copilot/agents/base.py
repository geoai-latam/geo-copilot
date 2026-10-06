"""
Clase base para todos los agentes del sistema.

Provides:
- Standard response model (AgentResponse)
- Base class with common functionality
- Approval callback support for decoupled HITL
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

# Type alias for approval callbacks
# The callback receives approval data and returns True if approved
ApprovalCallback = Callable[[dict], Awaitable[bool]]


class AgentResponse(BaseModel):
    """Respuesta estándar de un agente."""
    success: bool
    message: str
    data: Any = None
    requires_hitl: bool = False
    hitl_context: dict | None = None


class BaseAgent(ABC):
    """
    Clase base abstracta para todos los agentes del sistema GEO_COPILOT.

    Todos los agentes deben implementar:
    - process(): Procesar una solicitud
    - get_capabilities(): Retornar capacidades del agente

    Supports:
    - Approval callbacks for decoupled HITL integration
    - Tool registration
    """

    def __init__(
        self,
        name: str,
        description: str,
        on_approval_needed: ApprovalCallback | None = None
    ):
        """
        Initialize the agent.

        Args:
            name: Agent name
            description: Agent description
            on_approval_needed: Optional callback for requesting approval
                               If not provided, approval requests auto-approve
        """
        self.name = name
        self.description = description
        self._tools: dict = {}
        self._on_approval_needed = on_approval_needed

    @abstractmethod
    async def process(self, query: str, context: dict | None = None) -> AgentResponse:
        """
        Procesar una solicitud del usuario.

        Args:
            query: Consulta en lenguaje natural
            context: Contexto adicional (datos previos, preferencias, etc.)

        Returns:
            AgentResponse con el resultado del procesamiento
        """
        pass

    @abstractmethod
    def get_capabilities(self) -> dict:
        """
        Retornar las capacidades del agente.

        Returns:
            Diccionario con capacidades categorizadas
        """
        pass

    def register_tool(self, name: str, tool: Callable[..., Any], description: str) -> None:
        """Registrar una herramienta para el agente."""
        self._tools[name] = {
            "function": tool,
            "description": description
        }

    def get_tools(self) -> dict:
        """Obtener todas las herramientas registradas."""
        return self._tools

    async def request_approval(self, approval_data: dict) -> bool:
        """
        Request approval for an action.

        This method provides a decoupled way to request approval without
        directly depending on HITLManager. Agents can use this to request
        approval for SQL execution, code execution, etc.

        Args:
            approval_data: Dictionary with approval details:
                - type: str - Type of approval (e.g., "sql_execution", "code_execution")
                - content: str - The content being approved (SQL, code, etc.)
                - risks: list[str] - Optional list of identified risks
                - session_id: str - Optional session ID for tracking

        Returns:
            True if approved, False if rejected or no callback configured

        Example:
            approved = await self.request_approval({
                "type": "sql_execution",
                "content": sql_query,
                "risks": ["modifies data"],
                "session_id": session_id
            })
            if not approved:
                return self._build_rejection_response()
        """
        if self._on_approval_needed:
            try:
                return await self._on_approval_needed(approval_data)
            except Exception as e:  # captura amplia a propósito: callback externo (HITL/WS) puede lanzar cualquier cosa; falla cerrado: sin aprobación
                logger.error(f"[{self.name}] Approval callback error: {e}", exc_info=True)
                return False

        # No callback configured - auto-approve (useful for testing)
        logger.debug(f"[{self.name}] No approval callback - auto-approving")
        return True

    def set_approval_callback(self, callback: ApprovalCallback | None) -> None:
        """
        Set or update the approval callback.

        Args:
            callback: New approval callback, or None to disable
        """
        self._on_approval_needed = callback

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}')"
