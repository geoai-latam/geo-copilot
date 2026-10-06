"""
Generador de narrativas automáticas.

Crea resúmenes textuales a partir de análisis de datos
espaciales usando LLM o plantillas.
"""

from typing import Any, cast

from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)

# F4: la narrativa por plantillas vive en su módulo (mixin).
from geo_copilot.agents.insights_agent.narrativa_plantillas import (
    NarrativaPlantillasMixin,
    NarrativeStyle,
)


class NarrativeGenerator(NarrativaPlantillasMixin):
    """
    Generador de narrativas automáticas.

    Puede generar narrativas usando:
    - Plantillas predefinidas (sin LLM)
    - LLM para narrativas más naturales
    """


    # Prompts para LLM
    LLM_PROMPTS = {
        "analyze_spatial": """
Analiza los siguientes resultados de un análisis espacial y genera una narrativa clara y concisa en español.

Tipo de análisis: {analysis_type}
Contexto: {context}

Datos estadísticos:
{statistics}

Genera una narrativa que:
1. Resuma los hallazgos principales
2. Destaque patrones o anomalías
3. Proporcione contexto relevante
4. Sugiera posibles acciones o investigaciones adicionales

La narrativa debe ser profesional pero accesible, usando datos concretos.
""",
        "generate_insights": """
Basándote en los siguientes datos de análisis geoespacial, genera insights accionables en español.

Datos:
{data_summary}

Genera 3-5 insights que:
1. Sean específicos y basados en los datos
2. Tengan relevancia práctica
3. Sugieran acciones concretas
4. Identifiquen oportunidades o riesgos
""",
        "compare_datasets": """
Compara los siguientes conjuntos de datos geoespaciales y genera un análisis comparativo en español.

Dataset 1: {dataset1_name}
{dataset1_stats}

Dataset 2: {dataset2_name}
{dataset2_stats}

Genera una comparación que:
1. Identifique diferencias clave
2. Explique posibles causas
3. Sugiera implicaciones
"""
    }

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        default_style: NarrativeStyle = NarrativeStyle.EXECUTIVE
    ):
        """
        Inicializar generador de narrativas.

        Args:
            llm_client: Cliente LLM para generación avanzada
            default_style: Estilo de narrativa por defecto
        """
        self.llm_client = llm_client
        self.default_style = default_style

    async def generate_narrative(
        self,
        analysis_type: str,
        data: dict[str, Any],
        context: str | None = None,
        style: NarrativeStyle | None = None,
        use_llm: bool = False
    ) -> dict[str, Any]:
        """
        Generar narrativa a partir de resultados de análisis.

        Args:
            analysis_type: Tipo de análisis (proximity, aggregation, etc.)
            data: Datos del análisis
            context: Contexto adicional
            style: Estilo de narrativa
            use_llm: Usar LLM para generación

        Returns:
            Narrativa generada con metadatos
        """
        style = style or self.default_style

        # ``generated_with`` debe reflejar lo que REALMENTE corrió (B2):
        # antes la ruta LLM fallaba (método inexistente), caía a plantilla
        # en silencio y aun así se reportaba "llm".
        generated_with = "template"
        narrative: str | None = None

        llm_requested_but_failed = False
        if use_llm and self.llm_client:
            try:
                narrative = await self._generate_with_llm(analysis_type, data, context, style)
                generated_with = "llm"
            except Exception as e:  # captura amplia a propósito: llamada al LLM; la degradación a plantilla se DECLARA (R2.4)
                logger.error(
                    f"LLM narrative failed, falling back to template: {e}", exc_info=True
                )
                llm_requested_but_failed = True

        if narrative is None:
            narrative = self._generate_from_template(analysis_type, data, context, style)
            # R2.4: si se pidió narrativa LLM y falló, la degradación al
            # template se DECLARA — el usuario recibe hechos, no "análisis",
            # y lo sabe.
            if llm_requested_but_failed:
                narrative = (
                    "(Resumen de datos — la narrativa analítica no está "
                    "disponible en este momento.)\n\n" + narrative
                )

        return {
            "narrative": narrative,
            "analysis_type": analysis_type,
            "style": style.value,
            "generated_with": generated_with,
            "word_count": len(narrative.split()),
        }


    async def _generate_with_llm(
        self,
        analysis_type: str,
        data: dict[str, Any],
        context: str | None,
        style: NarrativeStyle
    ) -> str:
        """Generar narrativa usando LLM."""
        prompt = self.LLM_PROMPTS.get("analyze_spatial", "")

        # Preparar estadísticas para el prompt
        statistics = self._format_statistics(data)

        formatted_prompt = prompt.format(
            analysis_type=analysis_type,
            # F2.2: default NEUTRAL — sin nombre de país. Antes fijaba "de
            # Colombia", lo que sesgaba la narrativa ("tendencias en el país" =
            # Colombia) para datos de cualquier otra región.
            context=context or "Análisis geoespacial de los datos",
            statistics=statistics
        )

        # Agregar instrucciones de estilo
        style_instructions = self._get_style_instructions(style)
        formatted_prompt += f"\n\nEstilo de redacción: {style_instructions}"

        # LLMClient expone ``chat()`` (no ``generate()``). Los errores
        # propagan: el fallback y su contabilidad viven en
        # ``generate_narrative`` (B2).
        response = await cast("LLMClient", self.llm_client).chat([
            LLMMessage(role="user", content=formatted_prompt)
        ])
        return response.content


    def _apply_style(self, narrative: str, style: NarrativeStyle) -> str:
        """Aplicar estilo a la narrativa."""
        if style == NarrativeStyle.BULLET_POINTS:
            # Convertir párrafos a bullets
            lines = narrative.split("\n")
            styled_lines = []
            for line in lines:
                if line.strip() and not line.startswith("#") and not line.startswith("-"):
                    styled_lines.append(f"- {line.strip()}")
                else:
                    styled_lines.append(line)
            return "\n".join(styled_lines)

        # R2.4: el estilo EXECUTIVE ya no fabrica un "Resumen Ejecutivo" con
        # regex de negritas (pseudo-síntesis mecánica que suplantaba el juicio
        # de resumen). En modo template la narrativa va tal cual; un resumen
        # ejecutivo de verdad lo redacta el LLM cuando corre la ruta LLM.

        return narrative

    def _get_style_instructions(self, style: NarrativeStyle) -> str:
        """Obtener instrucciones de estilo para LLM."""
        instructions = {
            NarrativeStyle.TECHNICAL: "Usa terminología técnica precisa, incluye todos los detalles estadísticos relevantes.",
            NarrativeStyle.EXECUTIVE: "Sé conciso, enfócate en conclusiones y recomendaciones accionables, usa bullets.",
            NarrativeStyle.CASUAL: "Usa un tono conversacional, explica conceptos técnicos de forma simple.",
            NarrativeStyle.BULLET_POINTS: "Presenta la información en puntos cortos y concisos, sin párrafos largos.",
        }
        return instructions.get(style, "")


    def generate_quick_summary(
        self,
        data: dict[str, Any],
        max_sentences: int = 3
    ) -> str:
        """
        Generar resumen rápido sin LLM.

        Args:
            data: Datos del análisis
            max_sentences: Máximo de oraciones

        Returns:
            Resumen conciso
        """
        stats = data.get("stats", {})
        sentences = []

        # Primera oración: conteo principal
        count = stats.get("feature_count", stats.get("record_count"))
        if count:
            sentences.append(f"Se encontraron {count:,} elementos en el análisis.")

        # Segunda oración: métrica principal
        for key in ["coverage_percent", "avg_distance", "total_area"]:
            if key in stats:
                value = stats[key]
                label = key.replace("_", " ")
                if isinstance(value, float):
                    sentences.append(f"El {label} es de {value:.2f}.")
                break

        # Tercera oración: observación
        if stats.get("max_value") and stats.get("max_name"):
            sentences.append(
                f"El valor máximo ({stats['max_value']:,}) se encuentra en {stats['max_name']}."
            )

        return " ".join(sentences[:max_sentences])
