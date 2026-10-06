"""El DISEÑO de las visualizaciones por el LLM y la paleta pedida a la simbología (A2A).

Salió de `InsightsAgent` (F4 del plan de calidad: agent.py tenía 1.610 líneas), tal cual.
"""

import json
from typing import TYPE_CHECKING, Any

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.prompts import cargar_prompt

if TYPE_CHECKING:
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.insights_agent.agent")


def _tipo_y_clases(charts: list[dict]) -> tuple[str, int]:
    """El tipo de dato y el número de clases que implican los gráficos diseñados."""
    # Heurística de data_type — solo determinística, sin LLM.
    data_type = "categorical"
    if charts:
        chart_types = {c.get("chart_type") for c in charts}
        if chart_types & {"histogram", "scatter"}:
            data_type = "numeric_continuous"
        elif "line" in chart_types:
            data_type = "temporal"
        elif chart_types & {"bar", "pie", "treemap"}:
            # categorical — la mayoría de los bar/pie son sobre
            # categorías nominales.
            data_type = "categorical"

    # num_classes: el plan del LLM ya define cuántas clases. Si no, 5.
    num_classes = 5
    if charts:
        limits: list[int] = [c["limit"] for c in charts if isinstance(c.get("limit"), int)]
        if limits:
            num_classes = max(2, min(min(limits), 12))
    return data_type, num_classes


def _tipo_de_geometria(geojson_data: Any) -> str | None:
    """La geometría de la capa (para que la simbología prefiera la paleta adecuada)."""
    # Geometría → para que el SymbologyAgent prefiera plasma/viridis correcto.
    geom_type: str | None = None
    if isinstance(geojson_data, dict):
        features = geojson_data.get("features") or []
        if features:
            geom_type = (features[0].get("geometry") or {}).get("type")
    return geom_type


def _defaults_viz() -> dict[str, Any]:
    """Plan conservador (sin diseño del LLM); uno nuevo cada vez."""
    return {
        "map_type": "point_map",
        "map_value_field": None,
        "map_color_field": None,
        "popup_fields": [],
        "charts": [],
        "reasoning": "no LLM available",
    }


def _entradas(geojson_data: Any, analysis_result: dict[str, Any]) -> tuple[list, list, list]:
    """Los features del geojson y los registros `data` (con sus campos) que hay que visualizar."""
    # Resolver geojson y features.
    if isinstance(geojson_data, str):
        try:
            geojson_data = json.loads(geojson_data)
        except json.JSONDecodeError:
            # GeoJSON no parseable: se diseña sólo con los `data` records.
            logger.warning("[InsightsAgent] geojson no es JSON válido; se ignora")
            geojson_data = None
    features = (geojson_data or {}).get("features", []) if isinstance(geojson_data, dict) else []

    # `data` records aparte de geojson (ej. ranking tabular, stats per-group).
    data_records = analysis_result.get("data") or []
    if isinstance(data_records, list) and data_records and isinstance(data_records[0], dict):
        data_fields = sorted(data_records[0].keys())
    else:
        data_records = []
        data_fields = []
    return features, data_records, data_fields


def _mensaje_viz(query: str, analysis_type: str, features: list, data_records: list, data_fields: list,
                 stats: dict[str, Any]) -> tuple[str, list]:
    """Lo que el LLM ve para diseñar (y los campos disponibles para validar su diseño)."""
    # Schema desde features (si hay) o desde data records (fallback).
    if features:
        first_props = features[0].get("properties", {})
        first_geom_type = features[0].get("geometry", {}).get("type", "Point")
    else:
        first_props = data_records[0] if data_records else {}
        first_geom_type = "None"  # sin geometría
    available_fields = sorted(first_props.keys())

    user_msg = (
        f"Query del usuario: {query!r}\n"
        f"Tipo de análisis: {analysis_type}\n"
        f"Geometría primaria: {first_geom_type}\n"
        f"Total features: {len(features)}\n"
        f"Campos disponibles (geojson.properties): {available_fields}\n"
        f"Campos disponibles (data records): {data_fields}\n"
        f"Sample feature: {json.dumps(first_props, ensure_ascii=False)[:300]}\n"
        f"Stats: {json.dumps({k: v for k, v in stats.items() if isinstance(v, (int, float))}, ensure_ascii=False)[:300]}\n\n"
        "Diseña las visualizaciones llamando a `design_visualizations`:"
    )
    return user_msg, available_fields


#: la forma del diseño (structured output de `design_visualizations`)
_PARAMETROS_DISENO_VIZ = {
    "type": "object",
    "properties": {
        "map_type": {
            "type": "string",
            "enum": ["point_map", "choropleth", "heatmap", "cluster"],
        },
        "map_value_field": {"type": ["string", "null"]},
        "map_color_field": {"type": ["string", "null"]},
        "popup_fields": {"type": "array", "items": {"type": "string"}},
        "charts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "pie", "line", "histogram", "scatter"],
                    },
                    "x_key": {"type": ["string", "null"]},
                    "y_key": {"type": ["string", "null"]},
                    "value_field": {"type": ["string", "null"]},
                    "title": {"type": "string"},
                    "horizontal": {"type": "boolean"},
                    "sort_by": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                },
                "required": ["chart_type", "title"],
                "additionalProperties": False,
            },
        },
        "reasoning": {"type": "string"},
    },
    "required": ["map_type", "charts", "reasoning"],
    "additionalProperties": False,
}


def _sanear_viz(parsed: dict[str, Any], available_fields: list, data_fields: list) -> None:
    """Anti-alucinación: campos que existen y gráficos con los campos mínimos para su tipo."""
    # Validar campos contra schema (anti-alucinación).
    all_known_fields = set(available_fields) | set(data_fields)
    for key in ("map_value_field", "map_color_field"):
        val = parsed.get(key)
        if val is not None and val not in all_known_fields:
            logger.warning(f"[InsightsAgent] campo inexistente {key}={val!r} — descartado")
            parsed[key] = None
    # Filtrar popup fields existentes.
    parsed["popup_fields"] = [
        f for f in (parsed.get("popup_fields") or []) if f in available_fields
    ][:5]

    # Validar cada chart: x_key, y_key, value_field deben existir.
    valid_charts: list[dict] = []
    for ch in parsed.get("charts") or []:
        if not isinstance(ch, dict):
            continue
        for k in ("x_key", "y_key", "value_field"):
            v = ch.get(k)
            if v is not None and v not in all_known_fields:
                logger.warning(f"[InsightsAgent] chart con campo inexistente {k}={v!r} — descartado")
                ch[k] = None
        # Un chart sirve si tiene los campos mínimos para su tipo.
        ct = ch.get("chart_type")
        if ct in ("bar", "line", "scatter") and (not ch.get("x_key") or not ch.get("y_key")):
            logger.warning(f"[InsightsAgent] {ct} sin x_key/y_key — descartado")
            continue
        if ct == "pie" and (not ch.get("x_key") or not ch.get("y_key")):
            continue
        if ct == "histogram" and not ch.get("value_field"):
            continue
        valid_charts.append(ch)
    parsed["charts"] = valid_charts


def _sin_diseno(defaults: dict[str, Any], features: list, data_records: list, hay_llm: bool) -> dict[str, Any] | None:
    """El plan cuando no se puede diseñar (sin datos o sin LLM), declarado; None si se puede."""
    # Honestidad (LLM-pilar / agentic_no_fallbacks): distinguir "no hay datos"
    # de "no hay LLM". Antes ambos devolvían el mismo point_map plausible,
    # fingiendo un diseño intencional cuando en realidad no se pudo diseñar.
    if not features and not data_records:
        return {**defaults, "reasoning": "sin datos para visualizar"}
    if not hay_llm:
        # Sin LLM NO hay diseño: mostramos la data sin analizar y lo
        # DECLARAMOS (degraded), en vez de inventar un mapa intencional.
        return {
            **defaults,
            "reasoning": "Sin LLM no puedo diseñar la visualización; "
            "se muestra la data sin analizar.",
            "degraded": True,
        }
    return None


class DisenoVizMixin:
    """El DISEÑO de las visualizaciones por el LLM y la paleta pedida a la simbología (A2A)."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient | None
        agent_hub: Any

    _DESIGN_VIZ_SYSTEM = cargar_prompt("insights_diseno_viz")

    async def _a2a_select_palette(
        self,
        *,
        design: dict[str, Any],
        geojson_data: dict | str | None,
        analysis_result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """A2A: consultar al SymbologyAgent qué paleta usar para los charts.

        Inferimos un ``data_type`` razonable mirando los charts diseñados:
        - histogram + scatter + numeric x/y → ``numeric_continuous``.
        - bar/pie con categorical x_key → ``categorical``.
        - line con eje X temporal → ``temporal``.
        - sin charts → ``categorical`` (default safe para tablas).

        Si no hay hub o la llamada falla, devuelve ``None`` y los
        consumidores caen al default (Plotly por defecto en el frontend).
        """
        if self.agent_hub is None:
            return None

        data_type, num_classes = _tipo_y_clases(design.get("charts") or [])
        geom_type = _tipo_de_geometria(geojson_data)

        ok, payload = await self.agent_hub.call(
            caller="insights_agent",
            target="symbology_agent",
            method="suggest_palette_for",
            data_type=data_type,
            num_classes=num_classes,
            purpose="chart",  # InsightsAgent pinta charts, no mapas
            geometry_type=geom_type,
        )
        if not ok:
            logger.debug(f"[A2A] insights→symbology palette skip: {payload}")
            return None

        # ``payload`` es un dict ya — suggest_palette_for retorna dict directo.
        result = dict(payload)
        result["from"] = "symbology_agent"
        logger.info(
            f"[A2A] insights_agent → symbology_agent: palette "
            f"{result.get('color_scheme')} ({len(result.get('palette_hex') or [])} colors) "
            f"for data_type={data_type}, purpose=chart"
        )
        return result

    async def _llm_design_visualizations(
        self,
        query: str,
        analysis_type: str,
        geojson_data: dict | str | None,
        analysis_result: dict[str, Any],
        stats: dict[str, Any],
    ) -> dict[str, Any]:
        """LLM diseña qué mapa + qué charts generar.

        Antes había if/elif por analysis_type fijo que ignoraba la query y
        elegía campos por keyword (`_find_numeric_field`, etc.). Ahora el
        LLM ve el contexto completo y produce el plan visual.

        Sin LLM, devuelve un plan conservador (point_map + bar chart).
        """
        defaults = _defaults_viz()

        features, data_records, data_fields = _entradas(geojson_data, analysis_result)

        sin_diseno = _sin_diseno(defaults, features, data_records, bool(self.llm_client))
        if sin_diseno is not None:
            return sin_diseno

        user_msg, available_fields = _mensaje_viz(query, analysis_type, features, data_records, data_fields,
                                                  stats)

        try:
            # A3 (structured outputs): el diseño llega como llamada a función
            # con schema — sin brace-slicing. Un fallo de forma cae al except
            # de abajo (defaults declarados), igual que antes.
            from geo_copilot.core.structured_output import structured_call
            parsed = await structured_call(
                self.llm_client,
                [
                    LLMMessage(role="system", content=self._DESIGN_VIZ_SYSTEM),
                    LLMMessage(role="user", content=user_msg),
                ],
                name="design_visualizations",
                description="Registra el diseño de mapa + gráficos para el análisis.",
                parameters=_PARAMETROS_DISENO_VIZ,
            )

            _sanear_viz(parsed, available_fields, data_fields)

            # Defaults para campos faltantes.
            for k, v in defaults.items():
                parsed.setdefault(k, v)

            logger.info(
                f"[InsightsAgent] LLM diseñó map={parsed.get('map_type')}, "
                f"{len(parsed['charts'])} charts — {parsed.get('reasoning', '')[:80]}"
            )
            return parsed
        except Exception as exc:  # structured_call al LLM puede fallar de cualquier forma; caemos a defaults declarados
            logger.warning(
                f"[InsightsAgent] _llm_design_visualizations error: {exc}", exc_info=True
            )
            return _defaults_viz()
