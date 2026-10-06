"""
SymbologyAgent - Agente especializado en simbología cartográfica.

Decisiones SEMÁNTICAS las toma el LLM viendo schema + samples:
- Qué campo usar como etiqueta visible (label_field).
- Qué campo usar para clasificación temática (classification_field).

Decisiones TÉCNICAS (no semánticas) siguen siendo determinísticas:
- Tipo de geometría primaria (MultiPolygon > Polygon, etc. — realidad GeoJSON).
- Análisis estadístico de cada campo (min/max/std, value_counts) — math puro.
- Cálculo de breaks de clasificación (quantiles, unique values) — math.
- Selección de paleta de color por data_type — convención cartográfica.

Antes había `_find_label_field` (keywords hardcoded `["nombre","name","label",...]`)
y `_find_classification_field` (umbrales mágicos `std>0`, `2<=unique<=10`) que
fallaban con schemas no convencionales — eliminados en Sprint D.
"""

import hashlib
from typing import Any

from geo_copilot.agents.base import AgentResponse, BaseAgent
from geo_copilot.agents.symbology_agent.styles import (
    ClassBreak,
    ClassificationMethod,
    ColorScheme,
    DataType,
    FillStyle,
    GeometryType,
    LabelStyle,
    LegendConfig,
    MarkerStyle,
    StrokeStyle,
    SymbologyConfig,
    SymbologyType,
    SymbolType,
    get_color_palette,
)
from geo_copilot.core.error_sanitizer import sanitize_error
from geo_copilot.core.llm_client import LLMClient
from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

# F4: la clasificación, el análisis, el diseño y las paletas viven en sus módulos (mixins).
from geo_copilot.agents.symbology_agent.analisis import AnalisisMixin
from geo_copilot.agents.symbology_agent.clasificacion import ClasificacionMixin
from geo_copilot.agents.symbology_agent.diseno import (  # noqa: F401
    DisenoMixin,
    _hecho_estilo_actual,
)
from geo_copilot.agents.symbology_agent.paletas import PaletasMixin


def _plan_del_diseno(design: dict, preferences: dict) -> tuple[SymbologyType, Any, ClassificationMethod | None,
                                                       ColorScheme, int]:
    """El plan del LLM (tipo, campo, método, paleta, nº de clases) con los overrides del caller."""
    # Resolver el plan del LLM con posibles overrides del caller.
    symb_type_str = design.get("symbology_type", "single_symbol")
    try:
        symbology_type = SymbologyType(symb_type_str)
    except ValueError:
        symbology_type = SymbologyType.SINGLE_SYMBOL

    classification_field = preferences.get(
        "classification_field", design.get("classification_field")
    )
    classification_method_str = design.get("classification_method")
    color_scheme_str = design.get("color_scheme", "Blues")
    num_classes = int(design.get("num_classes", 5) or 5)
    num_classes = max(2, min(num_classes, 12))

    try:
        color_scheme = ColorScheme(color_scheme_str)
    except ValueError:
        color_scheme = ColorScheme.BLUES

    classification_method: ClassificationMethod | None = None
    if classification_method_str:
        try:
            classification_method = ClassificationMethod(classification_method_str)
        except ValueError:
            classification_method = None
    return symbology_type, classification_field, classification_method, color_scheme, num_classes


class SymbologyAgent(ClasificacionMixin, AnalisisMixin, DisenoMixin, PaletasMixin, BaseAgent):
    """
    Agente especializado en análisis de datos y generación de simbología.

    Capacidades:
    - Análisis de datos para determinar tipo y distribución
    - Selección automática de esquema de color
    - Generación de clasificaciones temáticas
    - Creación de configuraciones de leyenda
    - Sugerencia de nombres descriptivos para capas
    """

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        *,
        agent_hub: Any = None,
    ):
        super().__init__(
            name="SymbologyAgent",
            description="Especialista en simbología cartográfica y visualización de datos"
        )
        # Auto-init si no llega — consistente con DataAgent/RouterAgent/PythonAgent.
        # Para forzar modo offline (sin LLM, devuelve None en field choice),
        # pasar `llm_client=False` explícitamente.
        if llm_client is False:
            self.llm_client = None
        elif llm_client is None:
            from geo_copilot.core.config import settings
            self.llm_client = LLMClient.from_settings(settings)
        else:
            self.llm_client = llm_client
        # A2A (2026-05-31): hub para consultar al InsightsAgent si una
        # visualización elegida (heatmap/cluster/choropleth) es razonable
        # para los datos antes de finalizarla.
        self.agent_hub = agent_hub
        self._register_tools()

    def _register_tools(self) -> None:
        """Registrar herramientas del agente."""
        self.register_tool(
            "analyze_data",
            self.analyze_data,
            "Analizar datos para determinar tipo y estadísticas"
        )
        self.register_tool(
            "generate_symbology",
            self.generate_symbology,
            "Generar configuración de simbología basada en datos"
        )
        self.register_tool(
            "suggest_layer_name",
            self.suggest_layer_name,
            "Sugerir nombre descriptivo para una capa"
        )
        self.register_tool(
            "select_color_scheme",
            self.select_color_scheme,
            "Seleccionar esquema de color apropiado"
        )

    def get_capabilities(self) -> dict:
        """Retornar capacidades del agente."""
        return {
            "analysis_types": [
                "data_profiling",
                "statistical_analysis",
                "classification",
            ],
            "symbology_types": [
                "simple",
                "categorized",
                "graduated",
                "heatmap",
            ],
            "color_schemes": [e.value for e in ColorScheme],
            "classification_methods": [e.value for e in ClassificationMethod],
        }

    # ==========================================================================
    # A2A — capacidades públicas para que otros agentes consulten al SymbologyAgent.
    # Se invocan via ``hub.call(target="symbology_agent", method="...")``.
    # ==========================================================================

    async def process(
        self,
        query: str,
        context: dict | None = None
    ) -> AgentResponse:
        """
        Procesar solicitud de simbología.

        Args:
            query: Descripción de lo que se quiere visualizar
            context: Debe incluir 'geojson' con los datos y opcionalmente
                    'geometry_type', 'field_to_symbolize', etc.

        Returns:
            AgentResponse con SymbologyConfig
        """
        context = context or {}
        geojson = context.get("geojson")

        if not geojson:
            return AgentResponse(
                success=False,
                message="Se requiere GeoJSON para generar simbología",
                data=None
            )

        try:
            # Analizar datos + diseñar simbología via LLM (decide tipo,
            # método de clasificación, paleta, num clases, campos).
            analysis = await self.analyze_data(geojson, query=query, estilo_actual=context.get("estilo_actual"))

            # Construir SymbologyConfig a partir del diseño del LLM.
            symbology = await self.generate_symbology(
                geojson=geojson,
                analysis=analysis,
                query=query,
                preferences=context.get("preferences", {})
            )

            msg = f"Simbología generada: {symbology.layer_title}"
            # #7 (audit 2026-06-13): si un juez posterior (InsightsAgent) degradó
            # el tipo elegido (p. ej. heatmap → point_map por baja densidad),
            # DECLARARLO al usuario en vez de aplicar la alternativa en silencio
            # fingiendo que el tipo pedido encajó.
            reasoning = getattr(symbology, "reasoning", "") or ""
            if "[A2A] InsightsAgent corrigió" in reasoning:
                nota = reasoning.split("LLM original")[0]
                nota = nota.replace("[A2A] InsightsAgent corrigió:", "").strip().rstrip(".")
                msg = f"Nota: ajusté la visualización — {nota}. {msg}"
            # R2.3: si el diseño degradó (fallo del LLM → estilo neutro), se
            # DECLARA al usuario con el porqué y el siguiente paso — nunca
            # "Simbología generada" a secas fingiendo que salió lo pedido.
            degraded = "[DEGRADED]" in reasoning
            if degraded:
                nota = reasoning.replace("[DEGRADED]", "").strip()
                msg = (
                    f"Aviso: {nota}. Pídemela de nuevo o dime el campo a "
                    f"clasificar. {msg}"
                )
            data = symbology.model_dump()
            data["degraded"] = degraded
            return AgentResponse(
                success=True,
                message=msg,
                data=data,
            )

        except Exception as e:  # noqa: BLE001 — frontera del agente; sanitize_error registra con traceback
            # No filtrar internals (traceback/rutas/módulos) a la UI: el detalle
            # completo va al log; al usuario un mensaje honesto sanitizado.
            return AgentResponse(
                success=False,
                message=sanitize_error(
                    e,
                    context="symbology_agent.process",
                    user_message="No pude generar la simbología para estos datos.",
                ),
                data=None
            )

    async def generate_symbology(
        self,
        geojson: dict,
        analysis: dict,
        query: str = "",
        preferences: dict | None = None,
    ) -> SymbologyConfig:
        """Construir SymbologyConfig usando el `design` del LLM en `analysis`.

        El LLM decidió tipo, método, paleta, num clases, campos. Aquí
        calculamos los breaks numéricos/categóricos según ese plan y
        ensamblamos el config. Si el usuario pasó preferences (color
        explícito, override de campo), tienen prioridad sobre el LLM.
        """
        preferences = preferences or {}
        geom_type = analysis.get("geometry_type", "Point")
        design = analysis.get("design", {}) or {}

        layer_title = await self.suggest_layer_name(query, analysis)

        (symbology_type, classification_field, classification_method, color_scheme,
         num_classes) = _plan_del_diseno(design, preferences)

        config = SymbologyConfig(
            # C3d-2: hash determinista. ``hash(str)`` está randomizado por
            # proceso (PYTHONHASHSEED) → layer_name no reproducible entre
            # runs/workers y con colisiones (% 10000).
            layer_name=f"layer_{hashlib.md5(query.encode('utf-8')).hexdigest()[:8]}",
            layer_title=layer_title,
            geometry_type=GeometryType(geom_type),
            symbology_type=symbology_type,
            color_scheme=color_scheme,
            num_classes=num_classes,
            reasoning=design.get("reasoning", ""),
        )

        # Manual breaks: el usuario dio umbrales+colores EXPLÍCITOS
        # ("rojo >1000, azul <200"). Solo aplica a graduated_colors con campo.
        manual_spec = (
            design.get("manual_class_breaks")
            if symbology_type == SymbologyType.GRADUATED_COLORS
            else None
        )
        use_manual = bool(manual_spec) and bool(classification_field)

        base_color = await self._color_base(use_manual, preferences, query, color_scheme)
        self._estilo_base(config, geom_type, base_color)

        # Aplicar la simbología según el tipo elegido por el LLM.
        fields_info = analysis.get("fields", {}) or {}
        field_info = fields_info.get(classification_field, {}) if classification_field else {}
        data_type = field_info.get("data_type")
        self._clasificar(config, geojson, design, symbology_type, classification_field, field_info,
                         classification_method, color_scheme, num_classes, geom_type,
                         manual_spec if use_manual else None)
        return self._completar(config, symbology_type, preferences, design, layer_title, base_color,
                               analysis, geom_type, classification_field, data_type)

    async def _color_base(self, use_manual: bool, preferences: dict, query: str,
                          color_scheme: ColorScheme) -> str:
        """El color de partida de la capa."""
        # Color base. En modo manual los features fuera de TODA clase quedan en
        # gris neutro (no pintamos todo del primer color del usuario).
        if use_manual:
            base_color = "#9e9e9e"
        else:
            # Color del usuario en la query (ej. "muéstrame en rojo") override.
            # FH.6: el color que el usuario eligió a mano en el editor manda sobre la query.
            user_color = preferences.get("base_color") or await self._extract_color_from_query(query)
            base_color = user_color if user_color else get_color_palette(color_scheme, 1)[0]
        return base_color

    def _estilo_base(self, config: SymbologyConfig, geom_type: str, base_color: str) -> None:
        """El estilo base por geometría (lo que no cae en ninguna clase)."""
        # Estilo base por geometría — siempre presente como fallback visual
        # incluso cuando hay clasificación (el frontend usa fill.color base
        # para features que no caen en ningún break).
        if geom_type in ["Polygon", "MultiPolygon"]:
            config.fill = FillStyle(color=base_color, opacity=0.6)
            config.stroke = StrokeStyle(color=self._darken_color(base_color), width=2)
        elif geom_type in ["LineString", "MultiLineString"]:
            config.stroke = StrokeStyle(color=base_color, width=3, opacity=0.9)
        else:
            # Marker base con outline visible (frontend lo respeta vía
            # applyPointEntityStyle). 10px + stroke 1.5 es el sweet spot
            # legibilidad/no-saturación para puntos genéricos.
            config.marker = MarkerStyle(
                type=SymbolType.CIRCLE,
                color=base_color,
                size=10,
                stroke_color="#ffffff",
                stroke_width=1.5,
                opacity=0.95,
            )

    def _clasificar(self, config: SymbologyConfig, geojson: dict, design: dict,
                    symbology_type: SymbologyType, classification_field: Any, field_info: dict,
                    classification_method: ClassificationMethod | None, color_scheme: ColorScheme,
                    num_classes: int, geom_type: str, manual_spec: Any) -> None:
        """Las clases según el tipo que eligió el LLM (manuales si el usuario dio umbrales)."""
        if manual_spec:
            # Umbrales+colores explícitos del usuario → breaks MANUALES.
            # symbology_type queda graduated_colors (lo exige el render del
            # frontend para colorear por rango).
            config.classification_field = classification_field
            config.classification_method = ClassificationMethod.MANUAL
            config.class_breaks = self._build_manual_breaks(
                manual_spec, self._numeric_field_values(geojson, classification_field)
            )

        elif symbology_type == SymbologyType.UNIQUE_VALUES and classification_field:
            self._valores_unicos(config, classification_field, field_info, color_scheme, design)

        elif symbology_type == SymbologyType.GRADUATED_COLORS and classification_field:
            config.classification_field = classification_field
            method = classification_method or ClassificationMethod.NATURAL_BREAKS
            config.classification_method = method
            config.class_breaks = self._calculate_numeric_breaks(
                geojson, classification_field, method, color_scheme, num_classes
            )

        elif symbology_type == SymbologyType.GRADUATED_SYMBOLS and classification_field:
            # Solo aplica a puntos; si no, degradamos a graduated_colors.
            if geom_type not in ["Point", "MultiPoint"]:
                logger.info(
                    f"[SymbologyAgent] graduated_symbols requiere puntos pero geom={geom_type}; "
                    "degradando a graduated_colors."
                )
                config.symbology_type = SymbologyType.GRADUATED_COLORS
            config.classification_field = classification_field
            method = classification_method or ClassificationMethod.QUANTILE
            config.classification_method = method
            config.class_breaks = self._calculate_numeric_breaks(
                geojson, classification_field, method, color_scheme, num_classes
            )

        elif symbology_type == SymbologyType.HEATMAP:
            # Heatmap no usa breaks; el frontend renderiza densidad con
            # kernel. El field opcional pondera la intensidad.
            config.heatmap_intensity_field = classification_field

        elif symbology_type == SymbologyType.CLUSTER:
            # Cluster — el frontend agrupa puntos cercanos visualmente.
            pass  # No breaks; usa el marker base.

        # else: SINGLE_SYMBOL — solo usa el estilo base.

    def _valores_unicos(self, config: SymbologyConfig, classification_field: Any, field_info: dict,
                        color_scheme: ColorScheme, design: dict) -> None:
        """Una clase por categoría (las de fuera del top, como «Otros»), con los colores del LLM."""
        config.classification_field = classification_field
        config.classification_method = ClassificationMethod.UNIQUE_VALUES
        value_counts = field_info.get("value_counts", {}) or {}
        # R4.5: las categorías fuera del top perfilado se declaran como
        # clase "Otros" (con su conteo) en vez de perderse sin leyenda.
        _total = field_info.get("count", 0) or 0
        _others = max(0, _total - sum(value_counts.values()))
        config.class_breaks = self._calculate_categorical_breaks(
            value_counts, color_scheme, others_count=_others
        )
        # T3.0: colores por categoría que el LLM DECIDIÓ (pedido del usuario
        # o convención del dominio). El código solo los aplica.
        colores = self._sanitize_category_colors(design.get("category_colors")) or {}
        for b in config.class_breaks or []:
            if b.label in colores:
                b.color = colores[b.label]

    def _completar(self, config: SymbologyConfig, symbology_type: SymbologyType, preferences: dict,
                   design: dict, layer_title: str, base_color: str, analysis: dict, geom_type: str,
                   classification_field: Any, data_type: Any) -> SymbologyConfig:
        """Degradar si no hubo clases, y etiquetas, leyenda y estadísticas."""
        # Sanity: si el LLM eligió graduated/unique pero no produjeron breaks
        # (ej. campo vacío), degradar a single_symbol antes que mostrar mapa
        # roto al usuario.
        if (
            symbology_type in (SymbologyType.UNIQUE_VALUES, SymbologyType.GRADUATED_COLORS, SymbologyType.GRADUATED_SYMBOLS)
            and not config.class_breaks
        ):
            logger.warning(
                f"[SymbologyAgent] {symbology_type.value} sin breaks calculables — "
                f"degradando a single_symbol."
            )
            config.symbology_type = SymbologyType.SINGLE_SYMBOL
            config.classification_field = None
            config.classification_method = None

        # Etiquetas — usa el label_field del LLM si existe.
        label_field = preferences.get("label_field") or design.get("label_field")
        if label_field:
            config.label = LabelStyle(field=label_field)

        # Leyenda.
        config.legend = LegendConfig(
            title=layer_title,
            classes=config.class_breaks or [
                ClassBreak(label=layer_title, color=base_color)
            ],
        )

        # Estadísticas para que el frontend pueda mostrar info.
        config.statistics = {
            "feature_count": analysis.get("feature_count", 0),
            "geometry_type": geom_type,
            "classification_field": classification_field,
            "field_data_type": data_type,
        }
        config.data_type = DataType(data_type) if data_type else None

        return config
