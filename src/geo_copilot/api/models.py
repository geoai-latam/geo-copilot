"""
Modelos Pydantic para la API REST.

Define esquemas de request/response para todos los endpoints.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, Field

from geo_copilot.platform.contracts.map_ops import MapAction, Predicado
from geo_copilot.platform.contracts.respuesta import QueryResponse as ContratoQueryResponse

# =============================================================================
# Enums
# =============================================================================

class QueryStatus(str, Enum):
    """Estados de una consulta."""
    PENDING = "pending"
    PROCESSING = "processing"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class ApprovalAction(str, Enum):
    """Acciones de aprobación HITL."""
    APPROVE = "approve"
    REJECT = "reject"
    MODIFY = "modify"


# =============================================================================
# Map context (Fase A) — lo que el usuario tiene en el mapa/UI, para que el
# agente razone sobre la capa activa, la feature seleccionada y la zona
# visible. Snapshot LIGERO: metadatos + properties de la feature activa;
# las geometrías completas NO viajan aquí (salvo la capa activa, opcional).
# =============================================================================

class SeleccionContext(BaseModel):
    """FH.2: lo seleccionado en una capa. Pocos → ids; muchos → condición (sin geometrías)."""
    ids: list[int] | None = Field(default=None, max_length=5000)
    where: Predicado | None = None
    count: int | None = Field(default=None, ge=0)
    origin: str = Field(default="click", max_length=16)


class MapLayerContext(BaseModel):
    """Resumen de una capa cargada en el mapa (sin geometrías)."""
    id: str
    name: str = ""
    source: str = "unknown"          # 'sql' | 'discovery' | 'external' | ...
    geometry_type: str | None = None
    feature_count: int = 0
    fields: list[str] = Field(default_factory=list)
    visible: bool = True
    is_active: bool = False          # la capa "enfocada" por el usuario
    # Opcional: geojson completo SOLO para la capa activa cuando el usuario
    # quiere operar sobre ella (A4a). Validado/limitado en query.py.
    data: dict[str, Any] | None = None
    # T2.0a: dataset del workspace que respalda la capa. Con él, el cliente no
    # manda `data`: el backend lee la geometría del workspace de la sesión.
    dataset_id: str | None = Field(default=None, max_length=64)
    # S4.4 (F4): la capa POR REFERENCIA, también las que no son vectoriales en
    # memoria (raster, teselas MVT), que antes el agente no veía. Solo hechos:
    # qué es, de dónde salió y dónde está; interpretarlos es del LLM.
    kind: str | None = Field(default=None, max_length=32)       # renderer: raster-xyz, vector-mvt, …
    url: str | None = Field(default=None, max_length=1000)      # plantilla de teselas / servicio
    origin: dict[str, Any] | None = None                        # procedencia: capability + argumentos
    legend: dict[str, Any] | None = None                        # rampa de un raster (campo, min, max)
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)
    # FH.1: lo que el usuario VE de la capa. La lista viene en orden de dibujo
    # (la primera, abajo); aquí va su aspecto: opacidad, estilo y etiquetas.
    opacity: float | None = Field(default=None, ge=0, le=1)
    style: dict[str, Any] | None = None       # resumen: tipo, campo, clases con color
    label_field: str | None = Field(default=None, max_length=128)
    seleccion: SeleccionContext | None = None
    # FH.5: filtro de la capa (AND). La capa filtrada ES ese subconjunto: en el mapa
    # y para las herramientas. `filtro_count`: cuántos quedan (si el cliente lo sabe).
    filtro: list[Predicado] | None = Field(default=None, max_length=10)
    filtro_count: int | None = Field(default=None, ge=0)
    #: FH.10: el instante que retrata (un raster con fecha: la serie temporal).
    fecha: str | None = Field(default=None, max_length=40)


class SelectedFeatureContext(BaseModel):
    """Feature que el usuario tiene seleccionada (click en el mapa)."""
    layer_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)


class PointContext(BaseModel):
    """Punto que el usuario marcó en el mapa (el «aquí» de «¿qué valor tiene aquí?»)."""
    lon: float = Field(ge=-180, le=180)
    lat: float = Field(ge=-90, le=90)


class ViewportContext(BaseModel):
    """Ventana visible del mapa."""
    bbox: list[float] | None = None   # [minx, miny, maxx, maxy] en CRS
    zoom: float | None = None
    crs: str = "EPSG:4326"


class MencionContext(BaseModel):
    """FH.4: una referencia que el usuario ELIGIÓ en el chat con `@` (no texto a interpretar)."""
    tipo: Literal["capa", "seleccion", "campo"]
    #: La capa del mapa (en `seleccion`, la capa donde está lo seleccionado).
    layer_id: str = Field(max_length=128)
    #: Cómo aparece en el mensaje (`@Vías`, `@selección`, `@Lotes.area_m2`).
    texto: str = Field(max_length=160)
    campo: str | None = Field(default=None, max_length=128)


class SeleccionExcluida(BaseModel):
    layer_id: str = Field(max_length=128)
    layer_name: str = Field(max_length=200)
    count: int | None = Field(default=None, ge=0)


class RespuestaMapa(BaseModel):
    """FH.9: el usuario respondió EN el mapa a un `request_input` del agente."""
    modo: Literal["pick_point", "draw_area", "pick_layer", "pick_features"]
    #: Lo que el agente preguntó.
    pedido: str = Field(max_length=300)
    #: La capa elegida / dibujada / donde seleccionó (pick_point: vacío, el punto va en clicked_point).
    layer_id: str | None = Field(default=None, max_length=128)
    #: El usuario canceló en vez de responder.
    cancelado: bool = False


class VistaContext(BaseModel):
    """FH.10: una vista guardada (marcador)."""
    nombre: str = Field(max_length=80)
    bbox: list[float] = Field(min_length=4, max_length=4)


class ComparacionContext(BaseModel):
    """FH.10: la comparación con cortina que el usuario ve ahora."""
    left: str = Field(max_length=128)
    right: str = Field(max_length=128)


class SerieTiempoContext(BaseModel):
    """FH.10: el control de tiempo: las fechas de la serie y la que se muestra."""
    fechas: list[str] = Field(default_factory=list, max_length=60)
    actual: str | None = Field(default=None, max_length=40)


class MapContext(BaseModel):
    """Estado del mapa/UI que el frontend adjunta a la consulta."""
    layers: list[MapLayerContext] = Field(default_factory=list)
    selected_feature: SelectedFeatureContext | None = None
    viewport: ViewportContext | None = None
    clicked_point: PointContext | None = None
    active_visualization: dict[str, Any] | None = None
    basemap: str | None = None
    # FH.1: lo que pasó en el mapa desde el turno anterior (usuario o agente, y si
    # se deshizo). Con él el agente sabe que el usuario deshizo su simbología.
    acciones: list[MapAction] = Field(default_factory=list, max_length=50)
    # FH.4: lo que el usuario mencionó con `@` en ESTE mensaje.
    menciones: list[MencionContext] = Field(default_factory=list, max_length=20)
    #: FH.4: el chip «N seleccionados de X» estaba sobre el cuadro de texto al enviar y el
    #: usuario lo dejó (podía quitarlo): la selección está en el alcance de ESTE mensaje.
    alcance_seleccion: bool = False
    #: FH.4: el usuario QUITÓ ese chip para este mensaje; la UI le mostró entonces
    #: «sin selección: <capa> entera». La selección no viaja; esto dice cuál era.
    seleccion_excluida: SeleccionExcluida | None = None
    #: FH.9: este mensaje es la respuesta del usuario, en el mapa, a lo que el agente pidió.
    respuesta_mapa: RespuestaMapa | None = None
    #: FH.10: vistas guardadas, comparación activa y serie temporal (hechos del mapa).
    vistas: list[VistaContext] = Field(default_factory=list, max_length=20)
    comparacion: ComparacionContext | None = None
    serie_tiempo: SerieTiempoContext | None = None


# =============================================================================
# Request Models
# =============================================================================

class QueryRequest(BaseModel):
    """Request para procesar una consulta."""
    query: str = Field(..., min_length=1, max_length=2000, description="Consulta en lenguaje natural")
    session_id: str | None = Field(None, description="ID de sesión existente")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Parámetros adicionales")
    # Fase A: contexto del mapa (capas, selección, viewport). Opcional —
    # si no se envía, el comportamiento es el de antes (degradación grácil).
    map_context: MapContext | None = Field(
        None, description="Estado del mapa/UI (capas activas, feature seleccionada, viewport)"
    )
    # F2.2: región de sesión (override del default configurado del despliegue).
    # None = región configurada del producto. Una key sin catálogo (p.ej. otro
    # país no soportado) → búsqueda global/neutral, NUNCA anclada a Colombia.
    session_region: str | None = Field(
        None,
        description="Clave de región de sesión (p.ej. 'colombia', 'global'). "
        "None = default configurado; key desconocida = búsqueda global/neutral.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "query": "¿Cuántas escuelas hay dentro de 500 metros de los ríos principales?",
                    "session_id": "abc123",
                    "parameters": {"max_results": 100}
                }
            ]
        }
    }


class ApprovalRequest(BaseModel):
    """Request para aprobar/rechazar SQL o código.

    ``session_id`` ata la aprobación a la sesión que originó la solicitud
    HITL. El endpoint rechaza con 403 si no coincide con la sesión
    guardada en la solicitud. Sin esto, cualquier caller autenticado podía
    aprobar el SQL/código de otro (SEC-3).
    """
    action: ApprovalAction = Field(..., description="Acción a tomar")
    session_id: str = Field(..., description="ID de la sesión que originó la aprobación")
    modified_content: str | None = Field(None, description="Contenido modificado (solo para action=modify)")
    reason: str | None = Field(None, description="Razón del rechazo/modificación")

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"action": "approve", "session_id": "abc-123"},
                {"action": "reject", "session_id": "abc-123", "reason": "Query returns too much data"},
                {"action": "modify", "session_id": "abc-123", "modified_content": "SELECT * FROM table LIMIT 100"}
            ]
        }
    }


class SessionCreateRequest(BaseModel):
    """Request para crear una sesión."""
    session_id: str | None = Field(None, description="ID personalizado (se genera si no se proporciona)")
    preferences: dict[str, Any] = Field(default_factory=dict, description="Preferencias del usuario")


class PreferencesUpdateRequest(BaseModel):
    """Request para actualizar preferencias."""
    language: str | None = Field(None, description="Idioma (es, en)")
    output_format: str | None = Field(None, description="Formato de salida")
    map_style: str | None = Field(None, description="Estilo de mapa")
    narrative_style: str | None = Field(None, description="Estilo narrativo")
    max_results: int | None = Field(None, ge=1, le=10000, description="Máximo de resultados")
    auto_visualize: bool | None = Field(None, description="Generar visualizaciones automáticamente")
    require_approval: bool | None = Field(None, description="Requerir aprobación HITL")


# =============================================================================
# Response Models
# =============================================================================

class QueryResponse(ContratoQueryResponse):
    """Sobre de `/query`: el del contrato (F4). Sin campos propios de la API:
    lo que el frontend lee es exactamente lo que describe el contrato."""

    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalStatusResponse(BaseModel):
    """Response del estado de una aprobación."""
    approval_id: str = Field(..., description="ID de la aprobación")
    query_id: str = Field(..., description="ID de la consulta asociada")
    content_type: str = Field(..., description="Tipo de contenido (sql, python)")
    content: str = Field(..., description="Contenido a aprobar")
    status: str = Field(..., description="Estado actual")
    risk_level: str | None = Field(None, description="Nivel de riesgo detectado")
    warnings: list[str] = Field(default_factory=list, description="Advertencias de seguridad")
    created_at: datetime = Field(default_factory=datetime.now)


class ApprovalResultResponse(BaseModel):
    """Response después de una acción de aprobación."""
    approval_id: str
    action: ApprovalAction
    success: bool
    message: str
    query_result: QueryResponse | None = None


class SessionResponse(BaseModel):
    """Response con información de sesión."""
    session_id: str
    created_at: datetime
    updated_at: datetime
    message_count: int
    entities_identified: list[str]
    analysis_type: str | None
    has_results: bool
    preferences: dict[str, Any]


class SessionListResponse(BaseModel):
    """Response con lista de sesiones."""
    sessions: list[SessionResponse]
    total: int


class HealthResponse(BaseModel):
    """Response de health check."""
    status: str = "healthy"
    version: str
    components: dict[str, str]
    timestamp: datetime = Field(default_factory=datetime.now)


class ErrorResponse(BaseModel):
    """Response de error."""
    error: str
    detail: str | None = None
    code: str | None = None
    timestamp: datetime = Field(default_factory=datetime.now)


class EntitiesResponse(BaseModel):
    """Response con entidades disponibles."""
    entities: list[dict[str, Any]]
    total: int


class WorkflowsResponse(BaseModel):
    """Response con workflows disponibles."""
    workflows: list[dict[str, Any]]
    total: int


class IntentsResponse(BaseModel):
    """Response con intenciones soportadas."""
    intents: list[dict[str, str]]
    total: int
