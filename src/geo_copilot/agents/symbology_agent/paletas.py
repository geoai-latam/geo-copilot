"""PALETAS y nombres: la paleta recomendada por tipo de dato (A2A), el esquema de color, el color
nombrado en la pregunta y el nombre de la capa.

Salió de `SymbologyAgent` (F4 del plan de calidad: agent.py tenía 1.622 líneas), tal cual.
"""

from typing import TYPE_CHECKING, Any

from geo_copilot.agents.symbology_agent.styles import (
    ColorScheme,
    DataType,
    get_color_palette,
)
from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.symbology_agent.agent")


def _num_clases(num_classes: Any) -> int:
    """Cuántas clases, en [1, 12] (5 si no se entiende)."""
    # Defensa de entrada: num_classes puede llegar None, string, float.
    try:
        n_raw = int(num_classes) if num_classes is not None else 5
    except (TypeError, ValueError):
        logger.debug(
            f"[A2A] suggest_palette_for: num_classes={num_classes!r} "
            f"no parseable como int → default 5"
        )
        n_raw = 5
    n = max(1, min(n_raw, 12))
    return n


def _cuantitativo(purp: str, geom: str) -> tuple[ColorScheme, str]:
    """Datos cuantitativos: secuencial (perceptualmente uniforme en el mapa)."""
    if purp == "map":
        # Viridis perceptualmente uniforme + accesible para daltonismo.
        # Para puntos preferimos plasma (más brillante, más legible).
        scheme = (
            ColorScheme.PLASMA
            if "point" in geom
            else ColorScheme.VIRIDIS
        )
        reasoning = (
            f"datos cuantitativos en mapa → paleta secuencial "
            f"perceptualmente uniforme ({scheme.value})."
        )
    elif purp == "chart":
        scheme = ColorScheme.BLUES
        reasoning = (
            "datos cuantitativos en chart → secuencial clásico (Blues)."
        )
    else:  # table_accent u otro
        scheme = ColorScheme.BLUES
        reasoning = f"datos cuantitativos en {purp} → secuencial Blues."
    return scheme, reasoning


def _esquema_para(dt: str, purp: str, geom: str, n: int,
                  data_type: str) -> tuple[ColorScheme, int, bool, bool, bool, str]:
    """La regla cartográfica: el esquema de color por tipo de dato y propósito (y por qué)."""
    scheme: ColorScheme
    is_sequential = False
    is_diverging = False
    is_qualitative = False
    reasoning = ""

    # ---- Datos cuantitativos (numérico continuo/discreto/temporal) ----
    if dt in ("numeric_continuous", "numeric_discrete", "temporal"):
        is_sequential = True
        scheme, reasoning = _cuantitativo(purp, geom)

    # ---- Datos divergentes (+/- alrededor de un punto medio) ----
    elif dt == "diverging":
        is_diverging = True
        scheme = ColorScheme.RDYLGN if purp == "map" else ColorScheme.RDBU
        reasoning = (
            f"datos divergentes (positivo/negativo) → paleta "
            f"divergente ({scheme.value})."
        )

    # ---- Boolean: 2 valores opuestos ----
    elif dt == "boolean":
        is_diverging = True
        scheme = ColorScheme.RDYLGN
        n = 2
        reasoning = "boolean → 2 colores opuestos (rojo/verde)."

    # ---- Datos categóricos (sin orden) ----
    elif dt in ("categorical", "text", "identifier"):
        is_qualitative = True
        # SET2 es el default cartográfico para ≤8 categorías;
        # PAIRED ≤12; arriba de eso saturamos con repeat en get_color_palette.
        if n <= 8:
            scheme = ColorScheme.SET2
            reasoning = (
                f"categórico con {n} clases → SET2 (qualitative, "
                f"buena diferenciación)."
            )
        else:
            scheme = ColorScheme.PAIRED
            reasoning = (
                f"categórico con {n} clases (>8) → PAIRED (más colores)."
            )

    # ---- Fallback: data_type desconocido ----
    else:
        scheme = ColorScheme.BLUES
        reasoning = f"data_type {data_type!r} desconocido → fallback Blues."
    return scheme, n, is_sequential, is_diverging, is_qualitative, reasoning


def _paleta(data_type: str, num_classes: Any, purpose: str, geometry_type: str | None) -> dict[str, Any]:
    """`suggest_palette_for` (ver allí el contrato): reglas cartográficas sobre las paletas predefinidas."""
    n = _num_clases(num_classes)

    dt = (data_type or "").lower().strip()
    purp = (purpose or "map").lower().strip()
    geom = (geometry_type or "").lower()

    scheme, n, is_sequential, is_diverging, is_qualitative, reasoning = _esquema_para(
        dt, purp, geom, n, data_type,
    )

    palette = get_color_palette(scheme, n)

    return {
        "data_type": data_type,
        "purpose": purpose,
        "geometry_type": geometry_type,
        "color_scheme": scheme.value,
        "palette_hex": palette,
        "num_classes": n,
        "is_sequential": is_sequential,
        "is_diverging": is_diverging,
        "is_qualitative": is_qualitative,
        "reasoning": reasoning,
    }


class PaletasMixin:
    """Paletas, esquema de color, color nombrado en la pregunta y nombre de la capa."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient | None

    async def suggest_palette_for(
        self,
        *,
        data_type: str,
        num_classes: int = 5,
        purpose: str = "map",
        geometry_type: str | None = None,
    ) -> dict[str, Any]:
        """Recomienda un esquema de color apropiado para los datos.

        Llamada típicamente por ``InsightsAgent`` antes de generar charts
        — así la paleta del chart casa con la del mapa, en vez de que el
        chart use Plotly por default y el mapa otra cosa (problema típico
        de inconsistencia visual cross-componente).

        El método NO usa LLM. Aplica reglas cartográficas determinísticas
        sobre la tabla de paletas predefinida (``COLOR_PALETTES`` en
        ``styles.py``). Rápido (<1ms) y reproducible.

        Args:
            data_type: "numeric_continuous" | "numeric_discrete" |
                "categorical" | "boolean" | "diverging" | "text" |
                "temporal" | "identifier". Case-insensitive.
            num_classes: Cantidad de colores a devolver (clamped a [1, 12]).
                None / no-parseable se trata como 5 (default).
                **Nota:** para ``data_type=boolean`` la respuesta SIEMPRE
                tiene num_classes=2 independiente del input — un booleano
                tiene exactamente 2 valores.
            purpose: "map" | "chart" | "table_accent" — afecta la elección
                entre paletas equivalentes (viridis en mapas vs Blues en
                charts, p.ej.). Case-insensitive. Purpose desconocido cae
                al default por data_type (no rechaza).
            geometry_type: "Point" | "Polygon" | etc. — para puntos
                preferimos paletas más brillantes/saturadas.

        Returns:
            ``{
                "data_type": str,
                "purpose": str,
                "color_scheme": str,    # nombre del esquema, ej "viridis"
                "palette_hex": list[str],  # hex codes listos para usar
                "is_sequential": bool,
                "is_diverging": bool,
                "is_qualitative": bool,
                "reasoning": str,
            }``
        """
        return _paleta(data_type, num_classes, purpose, geometry_type)

    async def suggest_layer_name(self, query: str, analysis: dict) -> str:
        """
        Sugerir nombre descriptivo para la capa usando LLM.

        Args:
            query: Consulta original
            analysis: Análisis de datos

        Returns:
            Nombre descriptivo
        """
        if not self.llm_client:
            # Sin LLM no inventamos un nombre con keywords hardcodeados
            # ("buffer", "bomberos", "hospital"...) — esa lista sesga
            # nombres a un dominio y miente para otros casos. Devolvemos
            # algo neutro y honesto basado solo en metadatos verificables.
            geom_type = analysis.get("geometry_type", "datos")
            count = analysis.get("feature_count", 0)
            return f"{geom_type} ({count} elementos)"

        try:
            prompt = f"""Genera un nombre corto y descriptivo (máximo 5 palabras) para una capa de mapa.

Consulta del usuario: "{query}"
Tipo de geometría: {analysis.get("geometry_type")}
Número de elementos: {analysis.get("feature_count")}
Campos disponibles: {list(analysis.get("fields", {}).keys())[:5]}

Responde SOLO con el nombre, sin explicación. Ejemplo: "Predios urbanos zona norte"
"""
            messages = [LLMMessage(role="user", content=prompt)]
            response = await self.llm_client.chat(messages)
            name = response.content.strip().strip('"').strip("'")
            return name[:50]  # Limitar longitud

        except Exception as e:  # captura amplia a propósito: llamada al LLM; nombre neutro basado en metadatos
            logger.error(f"Error suggesting layer name: {e}", exc_info=True)
            return f"Capa de {analysis.get('geometry_type', 'datos')}"

    async def _extract_color_from_query(self, query: str) -> str | None:
        """
        Extraer color especificado por el usuario de la consulta usando LLM.

        Args:
            query: Consulta del usuario

        Returns:
            Color hex si se encuentra, None en caso contrario
        """
        if not query or not self.llm_client:
            return None

        try:
            prompt = f"""Analiza la siguiente consulta del usuario y determina si menciona un color específico para la visualización.

Consulta: "{query}"

Si el usuario menciona un color (en español o inglés), responde SOLO con el código hexadecimal del color (ej: #FF0000).
Si no menciona ningún color, responde exactamente: NONE

Ejemplos:
- "Muestra los predios de color amarillo" -> #FFD700
- "Ver parcelas en rojo" -> #FF0000
- "Mostrar el buffer verde" -> #00FF00
- "Muestra las casas" -> NONE
- "Buffer de 200 metros" -> NONE

Tu respuesta (solo el código hex o NONE):"""

            messages = [LLMMessage(role="user", content=prompt)]
            response = await self.llm_client.chat(messages)
            result = response.content.strip()

            if result.upper() == "NONE" or not result.startswith("#"):
                return None

            # Validar que es un color hex válido
            if len(result) == 7 and result[0] == "#":
                logger.info(f"LLM extracted color from query: {result}")
                return result.upper()

            return None

        except Exception as e:  # captura amplia a propósito: llamada al LLM; sin color explícito se usa el esquema por defecto
            logger.error(f"Error extracting color with LLM: {e}", exc_info=True)
            return None

    async def select_color_scheme(
        self,
        data_type: str | None,
        geometry_type: str
    ) -> ColorScheme:
        """
        Seleccionar esquema de color apropiado.

        Args:
            data_type: Tipo de datos del campo de clasificación
            geometry_type: Tipo de geometría

        Returns:
            ColorScheme apropiado
        """
        # Reglas cartográficas básicas
        if data_type == DataType.NUMERIC_CONTINUOUS.value:
            # Datos numéricos: secuencial
            return ColorScheme.VIRIDIS

        elif data_type == DataType.CATEGORICAL.value:
            # Datos categóricos: cualitativo
            return ColorScheme.SET2

        elif data_type == DataType.BOOLEAN.value:
            # Booleano: divergente
            return ColorScheme.RDYLGN

        else:
            # Default según geometría
            if geometry_type in ["Polygon", "MultiPolygon"]:
                return ColorScheme.BLUES
            elif geometry_type in ["LineString", "MultiLineString"]:
                return ColorScheme.ORANGES
            else:
                return ColorScheme.SET1
