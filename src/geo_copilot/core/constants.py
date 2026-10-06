"""
Constantes centralizadas para GEO_COPILOT.

Estos valores son defaults que pueden ser sobrescritos por Settings.
Usar estas constantes permite:
- Consistencia en toda la aplicación
- Fácil localización de valores por defecto
- Documentación clara de límites del sistema
"""

from enum import Enum


class DefaultLimits:
    """Límites por defecto del sistema."""
    SQL_RESULT_LIMIT = 1000
    MAX_PLAN_STEPS = 10
    MAX_RETRIES = 3
    CONVERSATION_HISTORY = 50
    MAX_SESSIONS = 100
    MAX_GEOJSON_FEATURES = 10000
    MAX_QUERY_LENGTH = 2000
    MAX_EXTERNAL_FILE_MB = 50


class DefaultTimeouts:
    """Timeouts por defecto en segundos."""
    SANDBOX_EXECUTION = 30
    HITL_APPROVAL = 300
    DATABASE_COMMAND = 30
    WEBSOCKET_RECEIVE = 60
    LLM_REQUEST = 60
    EXTERNAL_API = 30


class DefaultCRS:
    """Sistemas de referencia coordenados."""
    WGS84 = "EPSG:4326"
    WEB_MERCATOR = "EPSG:3857"
    COLOMBIA_UTM_18N = "EPSG:32618"
    COLOMBIA_ORIGIN = "EPSG:3116"


class TruncationLimits:
    """Límites de truncamiento para logs y displays."""
    LOG_MESSAGE = 200
    LOG_QUERY = 100
    LAYER_NAME = 50
    ERROR_MESSAGE = 300
    CONVERSATION_PREVIEW = 500


class SQLTemplateDefaults:
    """Defaults para templates SQL."""
    PROXIMITY_LIMIT = 1000
    AGGREGATION_LIMIT = 1000
    COVERAGE_LIMIT = 1000
    INTERSECTION_LIMIT = 1000
    HOTSPOT_GRID_SIZE = 1000


class GeometryTypes(str, Enum):
    """Tipos de geometría soportados."""
    POINT = "Point"
    LINE = "LineString"
    POLYGON = "Polygon"
    MULTI_POINT = "MultiPoint"
    MULTI_LINE = "MultiLineString"
    MULTI_POLYGON = "MultiPolygon"
    GEOMETRY_COLLECTION = "GeometryCollection"


class MapDefaults:
    """Valores por defecto para el mapa."""
    # Coordenadas de Bogotá
    CENTER_LON = -74.0721
    CENTER_LAT = 4.7110
    ZOOM = 100000
    ANIMATION_DURATION = 1.5


class WebSocketDefaults:
    """Valores por defecto para WebSocket."""
    MAX_RECONNECT_ATTEMPTS = 5
    RECONNECT_DELAY = 1000  # ms
    MAX_RECONNECT_DELAY = 30000  # ms


class UIDefaults:
    """Valores por defecto para UI."""
    MAX_QUERY_HISTORY = 20
    TRUNCATE_LAYER_NAME = 30
    RETRY_HIDE_DELAY_SUCCESS = 3000  # ms
    RETRY_HIDE_DELAY_FAILURE = 5000  # ms
