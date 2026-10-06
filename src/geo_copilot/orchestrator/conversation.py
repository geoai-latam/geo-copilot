"""
Gestor de conversación y contexto.

Mantiene el historial de la conversación, el estado actual
del análisis y las preferencias del usuario.
"""

from collections import deque
from datetime import datetime
from typing import TYPE_CHECKING, Any

from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.orchestrator.sessions.base import SessionStore

logger = get_logger(__name__)

# F4: los modelos (rol, mensaje, fuente activa, estado, preferencias) viven en conversacion_modelos;
# se reexportan porque se importan de aquí.
from geo_copilot.orchestrator.conversacion_modelos import (
    AnalysisState,
    DataSource,
    Message,
    MessageRole,
    UserPreferences,
    _entregado,
)


class ConversationContext:
    """
    Contexto completo de una conversación.

    Incluye historial, estado del análisis y preferencias.
    """

    def __init__(
        self,
        session_id: str,
        max_history: int = 50
    ):
        """
        Inicializar contexto de conversación.

        Args:
            session_id: Identificador de la sesión
            max_history: Máximo de mensajes a mantener
        """
        self.session_id = session_id
        self.created_at = datetime.now()
        self.updated_at = datetime.now()

        self._history: deque[Message] = deque(maxlen=max_history)
        self._state = AnalysisState()
        self._preferences = UserPreferences()
        self._variables: dict[str, Any] = {}

    @property
    def history(self) -> list[Message]:
        """Obtener historial de mensajes."""
        return list(self._history)

    @property
    def state(self) -> AnalysisState:
        """Obtener estado del análisis."""
        return self._state

    @property
    def preferences(self) -> UserPreferences:
        """Obtener preferencias del usuario."""
        return self._preferences

    def add_message(
        self,
        role: MessageRole,
        content: str,
        metadata: dict[str, Any] | None = None
    ) -> Message:
        """
        Agregar mensaje al historial.

        Args:
            role: Rol del mensaje
            content: Contenido del mensaje
            metadata: Metadatos adicionales

        Returns:
            Mensaje creado
        """
        message = Message(
            role=role,
            content=content,
            metadata=metadata or {}
        )
        self._history.append(message)
        self.updated_at = datetime.now()

        logger.debug(f"Message added to conversation {self.session_id}: {role.value}")
        return message

    def add_user_message(self, content: str, **metadata) -> Message:
        """Agregar mensaje del usuario."""
        return self.add_message(MessageRole.USER, content, metadata)

    def add_assistant_message(self, content: str, **metadata) -> Message:
        """Agregar mensaje del asistente."""
        return self.add_message(MessageRole.ASSISTANT, content, metadata)

    def add_system_message(self, content: str, **metadata) -> Message:
        """Agregar mensaje del sistema."""
        return self.add_message(MessageRole.SYSTEM, content, metadata)

    def get_last_messages(self, n: int = 5) -> list[Message]:
        """Obtener los últimos n mensajes."""
        return list(self._history)[-n:]

    def get_messages_for_llm(self, max_messages: int = 10) -> list[dict]:
        """
        Obtener mensajes formateados para enviar al LLM.

        Args:
            max_messages: Máximo de mensajes a incluir

        Returns:
            Lista de mensajes en formato LLM
        """
        messages = list(self._history)[-max_messages:]
        return [
            {"role": m.role.value, "content": _entregado(m) + m.content}
            for m in messages
        ]

    def set_variable(self, name: str, value: Any) -> None:
        """Establecer variable de contexto."""
        self._variables[name] = value

    def get_variable(self, name: str, default: Any = None) -> Any:
        """Obtener variable de contexto."""
        return self._variables.get(name, default)

    def update_state(self, **kwargs) -> None:
        """Actualizar estado del análisis."""
        for key, value in kwargs.items():
            if hasattr(self._state, key):
                setattr(self._state, key, value)
        self.updated_at = datetime.now()

    def update_preferences(self, **kwargs) -> None:
        """Actualizar preferencias del usuario."""
        for key, value in kwargs.items():
            if hasattr(self._preferences, key):
                setattr(self._preferences, key, value)

    def set_active_data_source(
        self,
        source: DataSource,
        source_name: str | None = None
    ) -> None:
        """
        Establecer la fuente de datos activa.

        Args:
            source: Tipo de fuente (INTERNAL, EXTERNAL, NONE)
            source_name: Nombre descriptivo de la fuente
        """
        old_source = self._state.active_data_source
        self._state.active_data_source = source
        self._state.active_source_name = source_name
        self.updated_at = datetime.now()

        logger.info(
            f"[Session {self.session_id}] Data source changed: "
            f"{old_source.value} → {source.value} ({source_name or 'N/A'})"
        )

    def get_active_data_source(self) -> tuple[DataSource, str | None]:
        """
        Obtener la fuente de datos activa.

        Returns:
            Tupla (DataSource, nombre_descriptivo)
        """
        return self._state.active_data_source, self._state.active_source_name

    def clear_external_data(self) -> None:
        """
        Limpiar datos externos de la sesión.

        Se usa cuando el usuario cambia a consultar la BD interna.
        """
        # Limpiar variables relacionadas con datos externos
        external_vars = [
            "external_geojson",
            "external_source_name",
            "found_services",
        ]
        for var in external_vars:
            if var in self._variables:
                del self._variables[var]

        logger.debug(f"[Session {self.session_id}] External data cleared")

    def get_active_geojson(self) -> dict[str, Any] | None:
        """
        Obtener el GeoJSON de la fuente de datos ACTIVA declarada.

        R2.6 (agentic, no fallbacks): sin cascada adivinadora — si la fuente
        declarada no tiene datos, se devuelve ``None`` y el caller decide
        honesto (antes "devolvía lo que hubiera", pudiendo servir la capa
        equivocada en silencio).

        Returns:
            GeoJSON de la fuente activa, o None si esa fuente no tiene datos.
        """
        source = self._state.active_data_source

        if source == DataSource.EXTERNAL:
            geojson = self._variables.get("external_geojson")
            if not geojson:
                logger.warning(
                    f"[Session {self.session_id}] Active source is EXTERNAL "
                    "but no external_geojson found — returning None (no fallback)"
                )
            return geojson or None

        if source == DataSource.INTERNAL:
            return self._state.last_geojson or None

        # Sin fuente activa declarada → no hay capa activa. Punto.
        return None

    def get_summary(self) -> dict[str, Any]:
        """
        Obtener resumen del contexto.

        Returns:
            Diccionario con resumen del estado
        """
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "message_count": len(self._history),
            "entities_identified": self._state.entities_identified,
            "analysis_type": self._state.analysis_type,
            "has_results": self._state.last_results is not None,
            "preferences": {
                "language": self._preferences.language,
                "output_format": self._preferences.output_format,
            }
        }

    def clear_history(self) -> None:
        """Limpiar historial de mensajes."""
        self._history.clear()

    def reset(self) -> None:
        """Reiniciar contexto completo."""
        self._history.clear()
        self._state.reset()
        self._variables.clear()

    def to_dict(self) -> dict[str, Any]:
        """Serializar contexto a diccionario."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "history": [m.to_dict() for m in self._history],
            "state": {
                "entities_identified": self._state.entities_identified,
                "current_query": self._state.current_query,
                "last_sql": self._state.last_sql,
                # B5: sin estos campos, una sesión restaurada desde Redis
                # perdía la capa activa (rompía follow-ups multi-worker).
                "last_results": self._state.last_results,
                "last_geojson": self._state.last_geojson,
                "last_dataset_id": self._state.last_dataset_id,
                "analysis_type": self._state.analysis_type,
                "parameters": self._state.parameters,
                "active_data_source": self._state.active_data_source.value,
                "active_source_name": self._state.active_source_name,
            },
            "preferences": {
                "language": self._preferences.language,
                "output_format": self._preferences.output_format,
                "map_style": self._preferences.map_style,
                "narrative_style": self._preferences.narrative_style,
                "max_results": self._preferences.max_results,
                "auto_visualize": self._preferences.auto_visualize,
                "require_approval": self._preferences.require_approval,
            },
            "variables": self._variables,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConversationContext":
        """Reconstruir un ``ConversationContext`` desde el dict de ``to_dict``.

        Fase 6 #11: necesario para deserializar sesiones que viven en
        Redis. Tolerante a payloads parciales (campos ausentes caen al
        default) para sobrevivir a evoluciones de esquema futuras.
        """
        ctx = cls(session_id=data.get("session_id", ""))
        # Timestamps
        created = data.get("created_at")
        updated = data.get("updated_at")
        if created:
            try:
                ctx.created_at = datetime.fromisoformat(created)
            except ValueError:
                pass
        if updated:
            try:
                ctx.updated_at = datetime.fromisoformat(updated)
            except ValueError:
                pass

        # Historial
        for raw in data.get("history", []):
            try:
                role = MessageRole(raw.get("role", "user"))
            except ValueError:
                continue
            timestamp = datetime.now()
            ts_raw = raw.get("timestamp")
            if ts_raw:
                try:
                    timestamp = datetime.fromisoformat(ts_raw)
                except ValueError:
                    pass
            ctx._history.append(
                Message(
                    role=role,
                    content=raw.get("content", ""),
                    timestamp=timestamp,
                    metadata=raw.get("metadata") or {},
                )
            )

        # Estado de análisis
        state = data.get("state") or {}
        ctx._state.entities_identified = state.get("entities_identified", [])
        ctx._state.current_query = state.get("current_query")
        ctx._state.last_sql = state.get("last_sql")
        ctx._state.last_results = state.get("last_results")
        ctx._state.last_geojson = state.get("last_geojson")
        ctx._state.last_dataset_id = state.get("last_dataset_id")
        ctx._state.analysis_type = state.get("analysis_type")
        ctx._state.parameters = state.get("parameters", {})
        try:
            ctx._state.active_data_source = DataSource(
                state.get("active_data_source", DataSource.NONE.value)
            )
        except ValueError:
            ctx._state.active_data_source = DataSource.NONE
        ctx._state.active_source_name = state.get("active_source_name")

        # Preferencias
        prefs = data.get("preferences") or {}
        ctx._preferences.language = prefs.get("language", ctx._preferences.language)
        ctx._preferences.output_format = prefs.get("output_format", ctx._preferences.output_format)
        ctx._preferences.map_style = prefs.get("map_style", ctx._preferences.map_style)
        ctx._preferences.narrative_style = prefs.get(
            "narrative_style", ctx._preferences.narrative_style
        )
        ctx._preferences.max_results = prefs.get("max_results", ctx._preferences.max_results)
        ctx._preferences.auto_visualize = prefs.get(
            "auto_visualize", ctx._preferences.auto_visualize
        )
        ctx._preferences.require_approval = prefs.get(
            "require_approval", ctx._preferences.require_approval
        )

        # Variables sueltas (found_services, external_geojson, ...)
        ctx._variables = data.get("variables") or {}
        return ctx


class ConversationManager:
    """Gestor de múltiples conversaciones.

    Fase 6 #11: el almacenamiento concreto se delega a un
    :class:`SessionStore` (in-memory por defecto, Redis opcional). La
    API pública se mantiene idéntica para que el resto del código no
    tenga que cambiar.
    """

    def __init__(
        self,
        max_sessions: int = 100,
        session_timeout_minutes: int = 60,
        store: "SessionStore | None" = None,
    ):
        from geo_copilot.orchestrator.sessions import InMemorySessionStore

        self.max_sessions = max_sessions
        self.session_timeout_minutes = session_timeout_minutes
        # Si el caller no pasa store, usamos in-memory para preservar
        # el comportamiento histórico.
        self._store: SessionStore = store or InMemorySessionStore(
            session_timeout_minutes=session_timeout_minutes
        )

    @property
    def store(self) -> "SessionStore":
        """Backend de almacenamiento subyacente (útil en tests)."""
        return self._store

    def create_session(self, session_id: str | None = None) -> ConversationContext:
        """Crear una nueva sesión y persistirla."""
        if session_id is None:
            import uuid
            session_id = str(uuid.uuid4())

        # Si el backend acepta limpieza, intentamos liberar espacio
        # cuando nos acercamos al límite.
        if self._store.count() >= self.max_sessions:
            self._store.cleanup_expired()

        context = ConversationContext(session_id=session_id)
        self._store.save(context)
        logger.info(f"Created conversation session: {session_id}")
        return context

    def get_session(self, session_id: str) -> ConversationContext | None:
        """Devolver una sesión existente y viva, o ``None``."""
        return self._store.get(session_id)

    def get_or_create_session(self, session_id: str) -> ConversationContext:
        """Devolver la sesión existente o crear una nueva con ese id."""
        context = self.get_session(session_id)
        if context is None:
            context = self.create_session(session_id)
        return context

    def save_session(self, context: ConversationContext) -> None:
        """Persistir/actualizar una sesión modificada en memoria.

        Necesario para backends donde mutar el ``ConversationContext`` no
        es suficiente (Redis no observa cambios en objetos Python). Para
        el backend in-memory es no-op (la referencia ya está guardada),
        pero llamarlo explícitamente mantiene el contrato.
        """
        self._store.save(context)

    def delete_session(self, session_id: str) -> bool:
        """Eliminar una sesión."""
        return self._store.delete(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        """Listar resúmenes de todas las sesiones activas."""
        return self._store.list_summaries()

    def _cleanup_expired_sessions(self) -> int:
        """Forzar limpieza de expiradas."""
        return self._store.cleanup_expired()

    def get_active_session_count(self) -> int:
        """Número de sesiones activas."""
        return self._store.count()

    def session_exists(self, session_id: str) -> bool:
        """¿Existe la sesión y sigue viva?"""
        return self._store.exists(session_id)
