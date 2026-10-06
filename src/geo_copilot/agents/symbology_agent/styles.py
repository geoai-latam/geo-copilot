"""
Definiciones de estilos y configuraciones de simbología.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ColorScheme(str, Enum):
    """Esquemas de color disponibles."""
    # Secuenciales (datos ordenados de bajo a alto)
    BLUES = "Blues"
    GREENS = "Greens"
    REDS = "Reds"
    ORANGES = "Oranges"
    PURPLES = "Purples"
    GREYS = "Greys"

    # Divergentes (valores que divergen de un punto medio)
    RDYLGN = "RdYlGn"  # Rojo-Amarillo-Verde
    RDBU = "RdBu"      # Rojo-Azul
    PRGN = "PRGn"      # Púrpura-Verde
    BRBG = "BrBG"      # Marrón-Verde azulado

    # Categóricos (datos sin orden)
    SET1 = "Set1"
    SET2 = "Set2"
    SET3 = "Set3"
    PASTEL1 = "Pastel1"
    PAIRED = "Paired"
    DARK2 = "Dark2"

    # Especiales para mapas
    TERRAIN = "terrain"
    VIRIDIS = "viridis"
    PLASMA = "plasma"
    INFERNO = "inferno"


class SymbolType(str, Enum):
    """Tipos de símbolos para geometrías."""
    # Puntos
    CIRCLE = "circle"
    SQUARE = "square"
    TRIANGLE = "triangle"
    DIAMOND = "diamond"
    STAR = "star"
    MARKER = "marker"
    CLUSTER = "cluster"

    # Líneas
    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"
    ARROW = "arrow"

    # Polígonos
    FILL = "fill"
    HATCH = "hatch"
    PATTERN = "pattern"
    OUTLINE = "outline"


class ClassificationMethod(str, Enum):
    """Métodos de clasificación para datos numéricos."""
    EQUAL_INTERVAL = "equal_interval"      # Intervalos iguales (uniform width)
    QUANTILE = "quantile"                  # Quantiles (misma cantidad por clase)
    NATURAL_BREAKS = "natural_breaks"      # Jenks natural breaks (minimiza varianza intra-clase)
    STANDARD_DEVIATION = "std_deviation"   # Desviación estándar (mean ± n·σ)
    MANUAL = "manual"                      # Breaks definidos manualmente
    UNIQUE_VALUES = "unique_values"        # Valores únicos (categórico)


class SymbologyType(str, Enum):
    """Tipos de simbología cartográfica.

    Determina CÓMO se representan visualmente los features. La elección
    depende del tipo de dato del campo y de la intención del usuario.
    """
    SINGLE_SYMBOL = "single_symbol"            # Color/símbolo único para todos
    UNIQUE_VALUES = "unique_values"            # Color por valor categórico distinto
    GRADUATED_COLORS = "graduated_colors"      # Coropleto: color cambia con valor numérico (clases)
    GRADUATED_SYMBOLS = "graduated_symbols"    # Tamaño del marcador cambia con valor numérico (solo puntos)
    HEATMAP = "heatmap"                        # Densidad de puntos (kernel density)
    CLUSTER = "cluster"                        # Agrupar puntos cercanos (solo puntos)


class DataType(str, Enum):
    """Tipos de datos detectados."""
    NUMERIC_CONTINUOUS = "numeric_continuous"
    NUMERIC_DISCRETE = "numeric_discrete"
    CATEGORICAL = "categorical"
    BOOLEAN = "boolean"
    TEMPORAL = "temporal"
    IDENTIFIER = "identifier"
    TEXT = "text"


class GeometryType(str, Enum):
    """Tipos de geometría."""
    POINT = "Point"
    MULTIPOINT = "MultiPoint"
    LINESTRING = "LineString"
    MULTILINESTRING = "MultiLineString"
    POLYGON = "Polygon"
    MULTIPOLYGON = "MultiPolygon"
    GEOMETRY_COLLECTION = "GeometryCollection"


class FillStyle(BaseModel):
    """Estilo de relleno para polígonos."""
    color: str = "#3388ff"
    opacity: float = Field(default=0.6, ge=0, le=1)
    pattern: str | None = None


class StrokeStyle(BaseModel):
    """Estilo de borde/línea."""
    color: str = "#3388ff"
    width: float = Field(default=2, ge=0)
    opacity: float = Field(default=1.0, ge=0, le=1)
    dash_array: list[int] | None = None
    line_cap: str = "round"
    line_join: str = "round"


class MarkerStyle(BaseModel):
    """Estilo de marcador para puntos."""
    type: SymbolType = SymbolType.CIRCLE
    color: str = "#3388ff"
    size: float = Field(default=8, ge=1)
    opacity: float = Field(default=1.0, ge=0, le=1)
    stroke_color: str = "#ffffff"
    stroke_width: float = Field(default=1, ge=0)


class LabelStyle(BaseModel):
    """Estilo de etiquetas."""
    field: str | None = None
    font_size: int = Field(default=12, ge=6, le=36)
    font_color: str = "#333333"
    font_weight: str = "normal"
    halo_color: str = "#ffffff"
    halo_width: float = Field(default=1, ge=0)
    anchor: str = "center"
    offset: tuple[int, int] = (0, 0)


class ClassBreak(BaseModel):
    """Definición de un break de clasificación."""
    min_value: float | None = None
    max_value: float | None = None
    label: str
    color: str
    count: int = 0  # Cantidad de features en esta clase


class LegendConfig(BaseModel):
    """Configuración de la leyenda."""
    title: str
    position: str = "bottomright"  # topright, topleft, bottomright, bottomleft
    collapsed: bool = False
    classes: list[ClassBreak] = Field(default_factory=list)


class SymbologyConfig(BaseModel):
    """Configuración completa de simbología para una capa."""
    layer_name: str
    layer_title: str  # Título descriptivo para leyenda
    geometry_type: GeometryType

    # Tipo de simbología elegido (LLM lo decide viendo datos + query)
    symbology_type: SymbologyType = SymbologyType.SINGLE_SYMBOL

    # Estilos base (siempre presentes — el frontend usa estos cuando
    # symbology_type=SINGLE_SYMBOL o como fallback cosmético).
    fill: FillStyle | None = None
    stroke: StrokeStyle | None = None
    marker: MarkerStyle | None = None
    label: LabelStyle | None = None

    # Clasificación temática (usado por GRADUATED_COLORS, UNIQUE_VALUES,
    # GRADUATED_SYMBOLS — todos requieren un campo y breaks).
    classification_field: str | None = None
    classification_method: ClassificationMethod | None = None
    color_scheme: ColorScheme = ColorScheme.BLUES
    num_classes: int = Field(default=5, ge=2, le=12)
    class_breaks: list[ClassBreak] = Field(default_factory=list)

    # Para GRADUATED_SYMBOLS (puntos con tamaño proporcional al valor).
    # Rango 8-24: 8px es el mínimo legible con outline visible; 24px evita
    # que los símbolos dominen la vista en zoom out.
    symbol_size_min: float = Field(default=8.0, ge=1.0)
    symbol_size_max: float = Field(default=24.0, ge=2.0)

    # Para HEATMAP (densidad).
    heatmap_radius: int = Field(default=25, ge=5, le=100)
    heatmap_intensity_field: str | None = None  # opcional: campo numérico que pondera

    # Leyenda
    legend: LegendConfig | None = None

    # Metadatos
    data_type: DataType | None = None
    statistics: dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""  # Por qué el LLM eligió esta simbología

    # Opciones adicionales (CLUSTER se controla con symbology_type ahora,
    # estos campos se mantienen para tunear el radio).
    cluster_radius: int = 50
    min_zoom: int = 0
    max_zoom: int = 20

    def to_map_style(self) -> dict:
        """Convertir a un dict de estilo para el mapa del frontend."""
        style: dict[str, Any] = {
            "layerName": self.layer_name,
            "layerTitle": self.layer_title,
        }

        if self.fill:
            style["fill"] = {
                "color": self.fill.color,
                "opacity": self.fill.opacity,
            }

        if self.stroke:
            style["stroke"] = {
                "color": self.stroke.color,
                "width": self.stroke.width,
                "opacity": self.stroke.opacity,
            }

        if self.marker:
            style["marker"] = {
                "type": self.marker.type.value,
                "color": self.marker.color,
                "size": self.marker.size,
                "stroke_color": self.marker.stroke_color,
                "stroke_width": self.marker.stroke_width,
                "opacity": self.marker.opacity,
            }

        if self.class_breaks:
            style["classification"] = {
                "field": self.classification_field,
                "method": self.classification_method.value if self.classification_method else None,
                "breaks": [
                    {
                        "min": b.min_value,
                        "max": b.max_value,
                        "label": b.label,
                        "color": b.color,
                    }
                    for b in self.class_breaks
                ],
            }

        if self.legend:
            style["legend"] = {
                "title": self.legend.title,
                "position": self.legend.position,
                "classes": [
                    {"label": c.label, "color": c.color}
                    for c in self.legend.classes
                ],
            }

        return style


# Paletas de colores predefinidas. Cualquier ``ColorScheme`` que NO esté
# aquí cae al fallback ``BLUES`` en ``get_color_palette``. Añadir entradas
# nuevas cuando el SymbologyAgent o la capacidad A2A
# ``suggest_palette_for`` empiece a usarlas — sino, terminamos con
# inconsistencias silenciosas (PLASMA solicitado pero servido como BLUES).
COLOR_PALETTES = {
    # Secuenciales mono-hue (ColorBrewer).
    ColorScheme.BLUES: ["#f7fbff", "#deebf7", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#084594"],
    ColorScheme.GREENS: ["#f7fcf5", "#e5f5e0", "#c7e9c0", "#a1d99b", "#74c476", "#41ab5d", "#238b45", "#005a32"],
    ColorScheme.REDS: ["#fff5f0", "#fee0d2", "#fcbba1", "#fc9272", "#fb6a4a", "#ef3b2c", "#cb181d", "#99000d"],
    ColorScheme.ORANGES: ["#fff5eb", "#fee6ce", "#fdd0a2", "#fdae6b", "#fd8d3c", "#f16913", "#d94801", "#8c2d04"],
    ColorScheme.PURPLES: ["#fcfbfd", "#efedf5", "#dadaeb", "#bcbddc", "#9e9ac8", "#807dba", "#6a51a3", "#4a1486"],
    ColorScheme.GREYS: ["#ffffff", "#f0f0f0", "#d9d9d9", "#bdbdbd", "#969696", "#737373", "#525252", "#252525"],
    # Secuenciales perceptualmente uniformes (matplotlib).
    ColorScheme.VIRIDIS: ["#440154", "#482878", "#3e4989", "#31688e", "#26838f", "#1f9e89", "#6cce5a", "#b5de2c"],
    ColorScheme.PLASMA: ["#0d0887", "#46039f", "#7201a8", "#9c179e", "#bd3786", "#d8576b", "#ed7953", "#fb9f3a"],
    ColorScheme.INFERNO: ["#000004", "#1b0c41", "#4a0c6b", "#781c6d", "#a52c60", "#cf4446", "#ed6925", "#fb9b06"],
    # Divergentes (ColorBrewer).
    ColorScheme.RDYLGN: ["#d73027", "#f46d43", "#fdae61", "#fee08b", "#d9ef8b", "#a6d96a", "#66bd63", "#1a9850"],
    ColorScheme.RDBU: ["#b2182b", "#d6604d", "#f4a582", "#fddbc7", "#d1e5f0", "#92c5de", "#4393c3", "#2166ac"],
    ColorScheme.PRGN: ["#762a83", "#9970ab", "#c2a5cf", "#e7d4e8", "#d9f0d3", "#a6dba0", "#5aae61", "#1b7837"],
    ColorScheme.BRBG: ["#8c510a", "#bf812d", "#dfc27d", "#f6e8c3", "#c7eae5", "#80cdc1", "#35978f", "#01665e"],
    # Cualitativas (ColorBrewer).
    ColorScheme.SET1: ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00", "#ffff33", "#a65628", "#f781bf"],
    ColorScheme.SET2: ["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3"],
    ColorScheme.SET3: ["#8dd3c7", "#ffffb3", "#bebada", "#fb8072", "#80b1d3", "#fdb462", "#b3de69", "#fccde5"],
    ColorScheme.PASTEL1: ["#fbb4ae", "#b3cde3", "#ccebc5", "#decbe4", "#fed9a6", "#ffffcc", "#e5d8bd", "#fddaec"],
    ColorScheme.PAIRED: ["#a6cee3", "#1f78b4", "#b2df8a", "#33a02c", "#fb9a99", "#e31a1c", "#fdbf6f", "#ff7f00"],
    ColorScheme.DARK2: ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666"],
    # Especiales para mapas (gradient natural).
    ColorScheme.TERRAIN: ["#333399", "#0099ff", "#00ff66", "#ccff00", "#ffcc00", "#cc6600", "#996600", "#996633"],
}


def get_color_palette(scheme: ColorScheme, num_colors: int) -> list[str]:
    """Obtener una paleta de colores del esquema especificado."""
    palette = COLOR_PALETTES.get(scheme, COLOR_PALETTES[ColorScheme.BLUES])

    # R4.4: para UN solo color, el submuestreo devolvía SIEMPRE el índice 0 —
    # en esquemas secuenciales (Blues, etc.) ese es el tono más claro
    # (#f7fbff, casi blanco): single_symbol quedaba invisible sobre basemap
    # claro. Un color representativo es el MEDIO de la rampa.
    if num_colors == 1:
        return [palette[len(palette) // 2]]

    if num_colors <= len(palette):
        # Submuestrear
        step = len(palette) / num_colors
        return [palette[int(i * step)] for i in range(num_colors)]
    else:
        # Repetir si es necesario
        return (palette * ((num_colors // len(palette)) + 1))[:num_colors]
