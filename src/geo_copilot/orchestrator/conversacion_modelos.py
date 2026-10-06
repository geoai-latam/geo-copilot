"""Los MODELOS de la conversación: el rol y el mensaje (con lo que se le entregó al usuario), la
fuente de datos activa, el estado del análisis y las preferencias.

Salió de `conversation.py` (F4 del plan de calidad: conversation.py tenía 560 líneas), tal cual.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

_NOMBRE_ARTEFACTO = {
    "layer": "capa en el mapa", "table": "tabla", "chart": "gráfico", "stats": "estadísticas",
    "report": "informe", "services": "lista de servicios", "map_command": "cambio de estilo en el mapa",
}


class MessageRole(str, Enum):
    """Roles de mensajes en la conversación."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


@dataclass
class Message:
    """Mensaje en la conversación."""
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convertir a diccionario."""
        return {
            "role": self.role.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


def _entregado(m: Message) -> str:
    """Lo que el turno ENTREGÓ de verdad, como hecho junto a lo que el asistente dijo.

    V5 F4: una respuesta afirmó «he generado un gráfico… y un coropleto» sin que
    ninguno llegara; al pedirlo otra vez, el router leyó el historial, lo dio por
    hecho y eligió `follow_up` (solo texto). La narración no es la prueba: los
    artefactos del turno sí. Va DELANTE: quien lee el historial lo recorta (el router,
    a 300 caracteres). Mensajes sin el dato (anteriores) quedan como estaban.
    """
    kinds = m.metadata.get("artefactos") if m.role == MessageRole.ASSISTANT else None
    if kinds is None:
        return ""
    nombres = list(dict.fromkeys(_NOMBRE_ARTEFACTO.get(k, k) for k in kinds))
    txt = f"Entregado al usuario en este turno: {', '.join(nombres) if nombres else 'solo texto'}"
    if m.metadata.get("intent"):
        txt += f" · intención: {m.metadata['intent']}"
    if m.metadata.get("fallidas"):
        txt += f" · FALLARON: {', '.join(m.metadata['fallidas'])}"
    return f"[{txt}] "


class DataSource(str, Enum):
    """Fuente de datos activa en la sesión."""
    INTERNAL = "internal"  # Base de datos PostgreSQL/PostGIS
    EXTERNAL = "external"  # Servicios externos (ArcGIS, Socrata, etc.)
    NONE = "none"          # Sin datos cargados


@dataclass
class AnalysisState:
    """Estado actual del análisis."""
    entities_identified: list[str] = field(default_factory=list)
    current_query: str | None = None
    last_sql: str | None = None
    last_results: dict[str, Any] | None = None
    last_geojson: dict[str, Any] | None = None
    # S2.1/T2.0: la capa del turno anterior POR REFERENCIA (dataset del
    # workspace). Cuando existe, last_geojson queda vacío: la sesión no carga
    # la geometría, se lee del workspace al necesitarla.
    last_dataset_id: str | None = None
    analysis_type: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)
    # Tracking de fuente de datos activa
    active_data_source: DataSource = DataSource.NONE
    active_source_name: str | None = None  # Nombre descriptivo de la fuente

    def reset(self) -> None:
        """Reiniciar estado."""
        self.entities_identified = []
        self.current_query = None
        self.last_sql = None
        self.last_results = None
        self.last_geojson = None
        self.last_dataset_id = None
        self.analysis_type = None
        self.parameters = {}
        self.active_data_source = DataSource.NONE
        self.active_source_name = None


@dataclass
class UserPreferences:
    """Preferencias del usuario."""
    language: str = "es"
    output_format: str = "full_report"
    map_style: str = "CartoDB positron"
    narrative_style: str = "executive"
    max_results: int = 1000
    auto_visualize: bool = True
    require_approval: bool = True
