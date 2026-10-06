"""Los JUICIOS sobre los datos que otros agentes piden por A2A: la forma de los datos, si una
visualización encaja (densidad incluida) y qué visualización inferir para un resultado.

Salió de `InsightsAgent` (F4 del plan de calidad: agent.py tenía 1.610 líneas), tal cual.
"""

from typing import TYPE_CHECKING, Any

from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import parse_json_from_llm

if TYPE_CHECKING:
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.insights_agent.agent")


def _sin_resumen(summary: str, geometry_type: str | None) -> dict[str, Any]:
    """El resumen de una capa sin datos (o con un conteo que no es un entero)."""
    return {
        "summary": summary,
        "feature_count": 0,
        "geometry_type": geometry_type,
        "field_count": 0,
        "has_data": False,
    }


def _campos_y_ejemplos(parts: list[str], field_names: Any, sample_values: dict | None) -> list[str]:
    """Añade al resumen los campos y unos valores de ejemplo; devuelve los campos."""
    # Campos — validar que sea lista (no string), si no, ignorar.
    if isinstance(field_names, list):
        fields = [str(f) for f in field_names]
    else:
        fields = []
    if fields:
        visible_fields = fields[:6]
        field_part = ", ".join(f"`{f}`" for f in visible_fields)
        if len(fields) > 6:
            field_part += f" (+{len(fields) - 6} más)"
        parts.append(f"con {len(fields)} campos: {field_part}")

    # Valores muestra (opcional, enriquece narrativa).
    if sample_values:
        sample_strs: list[str] = []
        for k, v in list(sample_values.items())[:3]:
            v_repr = repr(v)
            if len(v_repr) > 30:
                v_repr = v_repr[:27] + "..."
            sample_strs.append(f"{k}={v_repr}")
        if sample_strs:
            parts.append(f"ejemplos: {'; '.join(sample_strs)}")
    return fields


def _conteo(fc: int, geom_label: str) -> str:
    """El número de elementos, legible."""
    # Texto principal del feature count.
    if fc == 1:
        features_str = f"1 {geom_label}"
    elif fc < 1000:
        features_str = f"{fc} {geom_label}"
    elif fc < 1_000_000:
        features_str = f"{fc:,} {geom_label}"
    else:
        features_str = f"{fc:,} {geom_label} (datos masivos)"
    return features_str


def _ok(reason: str = "") -> dict[str, Any]:
    return {"appropriate": True, "reason": reason or "fit OK", "alternative": None}


def _not_ok(reason: str, alternative: str | None) -> dict[str, Any]:
    return {"appropriate": False, "reason": reason, "alternative": alternative}


def _encaje_por_campos(viz_type_norm: str, geometry_type: str | None, numeric_field_count: int,
                       categorical_field_count: int) -> dict[str, Any]:
    """Reglas por geometría y tipos de campo (choropleth, graduados, valores únicos…)."""
    geom = (geometry_type or "").lower()
    is_point = "point" in geom
    is_polygon = "polygon" in geom

    # ===== choropleth =====
    if viz_type_norm == "choropleth":
        if not is_polygon:
            return _not_ok(
                f"choropleth requiere polígonos, no {geometry_type!r}",
                alternative="graduated_symbols" if is_point else "single_symbol",
            )
        if numeric_field_count == 0:
            return _not_ok(
                "choropleth requiere un campo numérico para clasificar",
                alternative="unique_values" if categorical_field_count else "single_symbol",
            )
        return _ok("polígonos + campo numérico — choropleth apropiado")

    # ===== graduated_colors =====
    if viz_type_norm == "graduated_colors":
        if numeric_field_count == 0:
            return _not_ok(
                "graduated_colors requiere un campo numérico",
                alternative="unique_values" if categorical_field_count else "single_symbol",
            )
        return _ok("campo numérico disponible")

    # ===== graduated_symbols =====
    if viz_type_norm == "graduated_symbols":
        if not is_point:
            return _not_ok(
                f"graduated_symbols requiere puntos, no {geometry_type!r}",
                alternative="graduated_colors" if is_polygon else "single_symbol",
            )
        if numeric_field_count == 0:
            return _not_ok(
                "graduated_symbols requiere un campo numérico para el tamaño",
                alternative="point_map",
            )
        return _ok("puntos + campo numérico apropiado")

    # ===== unique_values =====
    if viz_type_norm == "unique_values":
        if categorical_field_count == 0:
            return _not_ok(
                "unique_values requiere un campo categórico",
                alternative="single_symbol",
            )
        return _ok("campo categórico disponible")

    # single_symbol y point_map son los únicos restantes del set
    # conocido — siempre aplican una vez pasamos las guardas iniciales.
    return _ok(f"{viz_type_norm} sin restricciones de datos")


class EncajeMixin:
    """Juicios A2A sobre los datos: su forma, si una visualización encaja y cuál inferir."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient | None
        _GEOM_LABELS: dict[str, tuple[str, str]]
        _KNOWN_VIZ_TYPES: frozenset[str]

    async def summarize_data_shape(
        self,
        *,
        feature_count: int,
        geometry_type: str | None = None,
        field_names: list[str] | None = None,
        sample_values: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resumen textual NL del shape de una capa cargada.

        Útil para:
        - Router/responder respondiendo follow-ups ("¿qué hay cargado?")
          sin volver al LLM costoso.
        - AgentsPipe footer mostrando contexto de la sesión.
        - Logs estructurados para debugging.

        Plantilla determinística — sin LLM. Salida en español.

        Defensa de entrada:
        - ``feature_count`` se coerciona a int (no-int → has_data=False).
        - ``field_names`` no-lista (string, None, etc.) → ignorado.
        - Singular/plural usa tabla explícita ``_GEOM_LABELS`` en vez de
          heurística ``[:-1]`` (que rompía con "geometrías mixtas").

        ``sample_values`` (opcional, ``{campo: valor}``) enriquece el resumen. Devuelve
        ``{summary, feature_count, geometry_type, field_count, has_data}``.
        """
        # Coerción de feature_count.
        try:
            fc = int(feature_count)
        except (TypeError, ValueError):
            return _sin_resumen(
                f"feature_count inválido: {feature_count!r} no es un entero — sin resumen.", geometry_type,
            )

        if fc <= 0:
            return _sin_resumen("No hay datos cargados en la capa activa.", geometry_type)

        # Singular/plural desde tabla explícita.
        singular, plural = self._GEOM_LABELS.get(
            (geometry_type or "").lower(),
            ("geometría", "geometrías"),
        )
        geom_label = singular if fc == 1 else plural

        features_str = _conteo(fc, geom_label)

        parts = [f"La capa activa tiene **{features_str}**"]

        fields = _campos_y_ejemplos(parts, field_names, sample_values)

        summary = ". ".join(parts) + "."

        return {
            "summary": summary,
            "feature_count": fc,
            "geometry_type": geometry_type,
            "field_count": len(fields),
            "has_data": True,
        }

    async def evaluate_visualization_fit(
        self,
        *,
        feature_count: int,
        geometry_type: str | None,
        viz_type: str,
        numeric_field_count: int = 0,
        categorical_field_count: int = 0,
        extent_km2: float | None = None,
    ) -> dict[str, Any]:
        """¿La visualización propuesta es razonable para estos datos?

        Llamada típicamente por ``SymbologyAgent`` antes de finalizar un
        ``heatmap`` o ``cluster``. La función NO usa LLM — son reglas
        determinísticas sobre forma + cantidad de datos. Eso la hace
        rápida (<1ms) y reproducible.

        Defensa de entrada:
        - ``feature_count <= 0`` → ``appropriate=False`` (no hay datos).
        - ``feature_count`` no-int (ej. ``"50"``) se coerciona via int(),
          retorna False si no se puede.
        - ``viz_type`` se lower-casea (el LLM puede devolver "HEATMAP").
        - ``viz_type`` fuera del set conocido → ``appropriate=False`` con
          razón explícita (antes caía al "ok sin restricciones").

        ``viz_type`` es uno de ``_KNOWN_VIZ_TYPES``. Devuelve ``{appropriate, reason, alternative}``;
        si no encaja, ``alternative`` sugiere qué usar (o ``None`` si no hay una razonable).
        """
        geom = (geometry_type or "").lower()
        is_point = "point" in geom

        # Coerción / validación de entradas.
        try:
            fc = int(feature_count)
        except (TypeError, ValueError):
            return _not_ok(
                f"feature_count inválido: {feature_count!r} no es entero",
                alternative=None,
            )
        if fc <= 0:
            return _not_ok(
                "no hay features de entrada — ningún viz aplica",
                alternative=None,
            )

        # Lower-casear viz_type para tolerar variantes del LLM.
        viz_type_norm = (viz_type or "").lower().strip()
        if viz_type_norm not in self._KNOWN_VIZ_TYPES:
            return _not_ok(
                f"viz_type {viz_type!r} no reconocido. Válidos: "
                f"{sorted(self._KNOWN_VIZ_TYPES)}",
                alternative="single_symbol",
            )

        if viz_type_norm in ("heatmap", "cluster"):
            return await self._encaje_por_densidad(viz_type_norm, fc, geometry_type, is_point,
                                                   numeric_field_count, extent_km2)
        return _encaje_por_campos(viz_type_norm, geometry_type, numeric_field_count,
                                  categorical_field_count)

    async def _encaje_por_densidad(self, viz_type_norm: str, fc: int, geometry_type: str | None, is_point: bool,
                                   numeric_field_count: int, extent_km2: float | None) -> dict[str, Any]:
        """heatmap y cluster: puntos, y densidad juzgada por el LLM (o el umbral honesto)."""
        is_polygon = "polygon" in (geometry_type or "").lower()
        # ===== heatmap =====
        if viz_type_norm == "heatmap":
            if not is_point:  # type-check determinista (correcto como código)
                return _not_ok(
                    f"heatmap requiere puntos, no {geometry_type!r}",
                    alternative="graduated_colors" if is_polygon else "single_symbol",
                )
            # LLM-pilar (Fix #2): la DENSIDAD es interpretación, no un umbral fijo.
            # El código ya calculó el extent; el LLM juzga si encaja. Sin extent/
            # LLM → fallback honesto al umbral.
            verdict = await self._judge_density_fit(
                "heatmap", fc, extent_km2,
                alt="graduated_symbols" if numeric_field_count else "point_map")
            if verdict is not None:
                return verdict
            if fc < 50:
                return _not_ok(
                    f"heatmap necesita densidad — {fc} puntos es "
                    f"muy poco; visualmente equivale a puntos sueltos",
                    alternative=(
                        "graduated_symbols" if numeric_field_count else "point_map"
                    ),
                )
            return _ok(f"{fc} puntos, densidad razonable")

        # ===== cluster =====
        if viz_type_norm == "cluster":
            if not is_point:
                return _not_ok(
                    f"cluster requiere puntos, no {geometry_type!r}",
                    alternative="single_symbol",
                )
            verdict = await self._judge_density_fit(
                "cluster", fc, extent_km2, alt="point_map")
            if verdict is not None:
                return verdict
            if fc < 30:
                return _not_ok(
                    f"cluster solo tiene sentido con muchos puntos densos; "
                    f"{fc} se ven mejor individuales",
                    alternative="point_map",
                )
            return _ok(f"{fc} puntos, cluster apropiado")
        raise ValueError(f"_encaje_por_densidad solo juzga heatmap y cluster, no {viz_type_norm!r}")

    async def _judge_density_fit(
        self, viz_type: str, fc: int, extent_km2: float | None, *, alt: str,
    ) -> dict[str, Any] | None:
        """LLM-pilar (Fix #2): juzga si ``heatmap``/``cluster`` encaja según la
        DENSIDAD REAL (puntos/km²), no un umbral fijo que ignora el extent.

        El CÓDIGO calcula la densidad (extent ya viene dado); el LLM INTERPRETA
        si "es denso". Devuelve el mismo dict que ``evaluate_visualization_fit``,
        o ``None`` si no se puede juzgar (sin extent o sin LLM) → el caller cae
        al umbral honesto. Ante fallo del LLM también devuelve ``None`` (fallback
        honesto, no inventa).
        """
        if not self.llm_client or not extent_km2 or extent_km2 <= 0:
            return None
        density = round(fc / extent_km2, 2)  # puntos por km²
        prompt = (
            f"Eres un cartógrafo. Decide si la visualización '{viz_type}' es "
            f"apropiada para estos PUNTOS, juzgando la DENSIDAD (no solo el conteo):\n"
            f"- nº de puntos: {fc}\n- extensión (bbox): {extent_km2} km²\n"
            f"- densidad: {density} puntos/km²\n\n"
            f"Un heatmap/cluster necesita densidad VISUAL: muchos puntos juntos. "
            f"{fc} puntos en 0.1 km² son densos; los mismos en 5000 km² están "
            f"dispersos y se ven como puntos sueltos. Respeta la intención si el "
            f"usuario pidió '{viz_type}' explícitamente, pero sé honesto.\n"
            f"Responde SOLO JSON: {{\"appropriate\": true|false, \"reason\": "
            f"\"breve\", \"alternative\": \"{alt}\" o null}}"
        )
        try:
            response = await self.llm_client.chat([LLMMessage(role="user", content=prompt)])
        except Exception as exc:  # noqa: BLE001 — fallback honesto al umbral
            logger.debug(f"[viz-fit] LLM density judge falló: {exc}")
            return None
        data = parse_json_from_llm(getattr(response, "content", "") or "", default={})
        if "appropriate" not in data:
            return None
        appropriate = bool(data.get("appropriate"))
        reason = str(data.get("reason", "")).strip() or f"densidad {density} pts/km²"
        return {
            "appropriate": appropriate,
            "reason": reason,
            "alternative": None if appropriate else (data.get("alternative") or alt),
        }
