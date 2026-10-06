"""El DISEÑO de la simbología por el LLM (structured output), su coherencia con los datos y la
consulta A2A al InsightsAgent sobre si la visualización encaja.

Salió de `SymbologyAgent` (F4 del plan de calidad: agent.py tenía 1.622 líneas), tal cual.
"""

import json
from functools import partial
from typing import TYPE_CHECKING, Any

from geo_copilot.agents.symbology_agent.styles import (
    ClassificationMethod,
    ColorScheme,
    DataType,
    SymbologyType,
)
from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.prompts import cargar_prompt

if TYPE_CHECKING:
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.symbology_agent.agent")


def _hecho_estilo_actual(estilo: dict | None) -> str:
    """FH.6: el estilo que la capa tiene AHORA y lo que el usuario fijó a mano (un hecho;
    qué conservar lo decide el diseño)."""
    if not isinstance(estilo, dict) or not estilo.get("symbology_type"):
        return ""
    partes = [f"tipo {estilo.get('symbology_type')}"]
    for clave, nombre in (("classification_field", "campo"), ("classification_method", "método"),
                          ("num_classes", "clases"), ("color_scheme", "rampa (color_scheme)")):
        if estilo.get(clave) not in (None, ""):
            partes.append(f"{nombre} {estilo[clave]}")
    texto = "ESTILO QUE LA CAPA TIENE AHORA EN EL MAPA: " + ", ".join(partes) + "." + chr(10)
    fijados = [c for c in (estilo.get("pinned") or []) if isinstance(c, str)]
    if fijados:
        valores = ", ".join(f"{c}={estilo.get(c)!r}" for c in fijados)
        texto += f"EL USUARIO FIJÓ A MANO en el editor de estilo: {valores}." + chr(10)
    return texto + chr(10)


def _contar_tipos(field_analysis: dict[str, dict]) -> tuple[int, int]:
    """Cuántos campos numéricos y cuántos categóricos tiene la capa (para el juez A2A)."""
    numeric_types = {
        DataType.NUMERIC_CONTINUOUS.value,
        DataType.NUMERIC_DISCRETE.value,
    }
    categorical_types = {DataType.CATEGORICAL.value, DataType.BOOLEAN.value}
    numeric_count = sum(
        1 for info in field_analysis.values()
        if info.get("data_type") in numeric_types
    )
    categorical_count = sum(
        1 for info in field_analysis.values()
        if info.get("data_type") in categorical_types
    )
    return numeric_count, categorical_count


def _con_alternativa(design: dict[str, Any], viz_type: str, alternative: str, reason: str) -> dict[str, Any]:
    """El diseño con la visualización que propuso el InsightsAgent (y el porqué en el razonamiento)."""
    new_design = dict(design)
    new_design["symbology_type"] = alternative

    # Anotar el override en el reasoning para que el responder lo cuente.
    original_reasoning = design.get("reasoning") or ""
    new_design["reasoning"] = (
        f"[A2A] InsightsAgent corrigió: {viz_type} → {alternative} "
        f"({reason}). LLM original: {original_reasoning[:120]}"
    )
    # Si la alternativa es single_symbol o point_map, limpiar campos
    # de clasificación para que la simbología no quede inconsistente.
    if alternative in ("single_symbol", "point_map", "heatmap", "cluster"):
        new_design["classification_field"] = None
        new_design["classification_method"] = None
    return new_design


def _esquema_compacto(field_analysis: dict) -> dict:
    """Schema compacto para el LLM: nombre + data_type + stats relevantes."""
    schema_summary = {}
    for fname, info in field_analysis.items():
        entry: dict[str, Any] = {"data_type": info.get("data_type")}
        if "statistics" in info:
            stats = info["statistics"]
            entry["stats"] = {
                "min": stats.get("min"),
                "max": stats.get("max"),
                "std": round(stats.get("std", 0), 3),
            }
        if "unique_values" in info:
            entry["unique"] = info["unique_values"]
        if "value_counts" in info:
            entry["top_values"] = list(info["value_counts"].keys())[:5]
        schema_summary[fname] = entry
    return schema_summary


#: la forma del diseño que el LLM registra (structured output de `design_symbology`)
_PARAMETROS_DISENO = {
    "type": "object",
    "properties": {
        "symbology_type": {
            "type": "string",
            "enum": sorted(e.value for e in SymbologyType),
        },
        "classification_field": {"type": ["string", "null"]},
        "classification_method": {
            "type": ["string", "null"],
            "description": "Uno de: "
            + ", ".join(sorted(e.value for e in ClassificationMethod)),
        },
        "color_scheme": {
            "type": "string",
            "enum": sorted(e.value for e in ColorScheme),
        },
        "num_classes": {"type": "integer", "minimum": 1, "maximum": 12},
        "label_field": {"type": ["string", "null"]},
        "manual_class_breaks": {
            "type": ["array", "null"],
            "items": {"type": "object"},
            "description": "Umbrales+colores explícitos del usuario, si los dio.",
        },
        "category_colors": {
            "type": ["object", "null"],
            "additionalProperties": {"type": "string"},
            "description": "Solo unique_values: {valor: '#hex'} cuando el "
            "usuario nombró colores por categoría o hay una convención de dominio.",
        },
        "explicit_user_request": {
            "type": "boolean",
            "description": "true SOLO si el usuario NOMBRÓ "
            "textualmente este tipo de visualización.",
        },
        "reasoning": {"type": "string"},
    },
    "required": ["symbology_type", "reasoning"],
    "additionalProperties": False,
}


def _veredicto(design: dict[str, Any], viz_type: str, payload: dict) -> dict[str, Any]:
    """Lo que dijo el InsightsAgent: el diseño tal cual o con la alternativa que propuso."""
    if payload.get("appropriate"):
        logger.debug(
            f"[A2A] InsightsAgent OK: {viz_type} fit "
            f"({payload.get('reason')})"
        )
        return design

    alternative = payload.get("alternative")
    reason = payload.get("reason") or "no encaja"
    logger.info(
        f"[A2A] InsightsAgent overrides {viz_type} → {alternative}: {reason}"
    )

    if not alternative:
        # InsightsAgent dijo "no encaja" pero no propuso alternativa.
        # Mantenemos lo que el LLM eligió — preferimos un mapa subóptimo
        # a uno roto.
        return design

    return _con_alternativa(design, viz_type, alternative, reason)


#: R2.3: sin LLM, un estilo neutro con la degradación MARCADA (process() la declara)
_SIN_LLM = {
    "symbology_type": "single_symbol",
    "classification_field": None,
    "classification_method": None,
    "color_scheme": "Blues",
    "num_classes": 5,
    "label_field": None,
    "reasoning": (
        "[DEGRADED] no hay LLM disponible para diseñar la "
        "simbología — apliqué un estilo neutro"
    ),
}


def _mensaje_diseno(query: str, primary_geom: str, feature_count: int | None, field_analysis: dict,
                    sample_props: list[dict], estilo_actual: dict | None) -> str:
    """Lo que el LLM ve para diseñar: la pregunta, la geometría, el schema con stats y muestras."""
    schema_summary = _esquema_compacto(field_analysis)

    return (
        f"Query del usuario: {query!r}\n"
        f"Geometría primaria: {primary_geom}\n"
        # R4.9: len(features) REAL (antes: promedio de counts no-nulos por
        # campo — con nulls subestimaba y sesgaba la guía heatmap/cluster).
        f"Total features: {feature_count if feature_count is not None else max((info.get('count', 0) for info in field_analysis.values()), default=0)}\n"
        f"Schema con stats:\n{json.dumps(schema_summary, ensure_ascii=False, indent=2, default=str)}\n\n"
        f"Samples (primeros features):\n{json.dumps(sample_props, ensure_ascii=False, indent=2, default=str)}\n\n"
        + _hecho_estilo_actual(estilo_actual)
        + "Diseña la simbología llamando a la función `design_symbology`:"
    )


# Defaults seguros (single_symbol neutro) — usados si el LLM falla
# o devuelve un valor inválido.
_DEFAULTS = {
    "symbology_type": "single_symbol",
    "classification_field": None,
    "classification_method": None,
    "color_scheme": "Blues",
    "num_classes": 5,
    "label_field": None,
    "manual_class_breaks": None,
    "explicit_user_request": False,
    "reasoning": "",
}


def _completo(parsed: dict[str, Any]) -> dict[str, Any]:
    """El diseño con los defaults en lo que falte (y registrado)."""
    # Merge defaults para campos faltantes.
    for k, v in _DEFAULTS.items():
        parsed.setdefault(k, v)

    logger.info(
        f"[SymbologyAgent] LLM diseñó {parsed.get('symbology_type')} "
        f"con field={parsed.get('classification_field')}, "
        f"method={parsed.get('classification_method')}, "
        f"scheme={parsed.get('color_scheme')} — "
        f"{parsed.get('reasoning', '')[:100]}"
    )
    return parsed


class DisenoMixin:
    """Diseño de la simbología por el LLM, su coherencia con los datos y el juez A2A."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient | None
        agent_hub: Any

        def _sanitize_manual_breaks(self, raw: Any) -> list[dict[str, Any]] | None: ...

        def _sanitize_category_colors(self, raw: Any) -> dict[str, str] | None: ...

    _DESIGN_SYMBOLOGY_SYSTEM = cargar_prompt("simbologia_diseno")

    async def _validate_design_via_a2a(
        self,
        design: dict[str, Any],
        *,
        feature_count: int,
        geometry_type: str,
        field_analysis: dict[str, dict],
        extent_km2: float | None = None,
    ) -> dict[str, Any]:
        """A2A: consultar InsightsAgent si la viz elegida encaja con los datos.

        Si ``agent_hub`` no está disponible, devuelve el design sin tocar
        (mismo comportamiento que pre-A2A). Si InsightsAgent indica que
        no encaja, ajustamos ``symbology_type`` al ``alternative`` sugerido
        y agregamos una nota a ``reasoning`` para que el responder pueda
        decirle al usuario qué pasó.
        """
        if self.agent_hub is None:
            return design

        viz_type = design.get("symbology_type")
        if not viz_type:
            return design

        numeric_count, categorical_count = _contar_tipos(field_analysis)

        ok, payload = await self.agent_hub.call(
            caller="symbology_agent",
            target="insights_agent",
            method="evaluate_visualization_fit",
            feature_count=feature_count,
            geometry_type=geometry_type,
            viz_type=viz_type,
            numeric_field_count=numeric_count,
            categorical_field_count=categorical_count,
            extent_km2=extent_km2,  # Fix #2: el insights juzga densidad con el LLM
        )
        if not ok:
            logger.debug(f"[A2A] symbology→insights skip: {payload}")
            return design

        return _veredicto(design, viz_type, payload)

    async def _llm_design_symbology(
        self,
        query: str,
        field_analysis: dict,
        primary_geom: str,
        sample_props: list[dict],
        feature_count: int | None = None,
        estilo_actual: dict | None = None,
    ) -> dict[str, Any]:
        """LLM diseña la simbología completa viendo query + schema + samples.

        Devuelve dict con: symbology_type, classification_field, classification_method,
        color_scheme, num_classes, label_field, reasoning. Si no hay LLM,
        devuelve single_symbol como default neutro (no adivina).
        """
        if self.llm_client is None:
            logger.warning(
                "[SymbologyAgent] sin LLM — devuelvo single_symbol neutro."
            )
            # R2.3: degradación MARCADA (no silenciosa) — process() detecta el
            # sentinel [DEGRADED] y lo declara al usuario.
            return dict(_SIN_LLM)

        user_msg = _mensaje_diseno(query, primary_geom, feature_count, field_analysis,
                                   sample_props, estilo_actual)

        try:
            # A3 (structured outputs): el diseño llega como llamada a
            # `design_symbology` con schema — sin brace-slicing. Un fallo de
            # FORMA (StructuredOutputError) cae al except → [DEGRADED] marcado
            # (política R2.3: degradación declarada, no fallo duro).
            parsed = await self._disenar(user_msg)

            parsed = await self._coherente(parsed, field_analysis, partial(self._disenar, user_msg))

            self._sanear_diseno(parsed, field_analysis)

            return _completo(parsed)
        except Exception as exc:  # diseño vía LLM; degradación marcada con [DEGRADED] (R2.3)
            logger.warning(
                f"[SymbologyAgent] _llm_design_symbology error: {exc}", exc_info=True
            )
            # R2.3: degradación marcada, no silenciosa — el fallo del LLM se
            # declara al usuario (process() detecta el sentinel [DEGRADED]).
            return {
                **_DEFAULTS,
                "reasoning": (
                    "[DEGRADED] no pude diseñar la simbología (fallo del "
                    "diseñador) — apliqué un estilo neutro"
                ),
            }

    async def _disenar(self, user_msg: str, correccion: str | None = None) -> dict[str, Any]:
        """Una llamada al diseñador (con la corrección, si es una repregunta)."""
        mensajes = [
            LLMMessage(role="system", content=self._DESIGN_SYMBOLOGY_SYSTEM),
            LLMMessage(role="user", content=user_msg),
        ]
        if correccion:
            mensajes.append(LLMMessage(role="user", content=correccion))
        return await self._llamar(mensajes)

    async def _llamar(self, mensajes: list[LLMMessage]) -> dict[str, Any]:
        """El diseño como structured output de `design_symbology`."""
        from geo_copilot.core.structured_output import structured_call

        return await structured_call(
            self.llm_client,
            mensajes,
            name="design_symbology",
            description="Registra el diseño de simbología para la capa.",
            parameters=_PARAMETROS_DISENO,
        )

    async def _coherente(self, parsed: dict[str, Any], field_analysis: dict, disenar: Any) -> dict[str, Any]:
        """El diseño, re-preguntado una vez si no se puede aplicar a los datos (y degradado si insiste)."""
        # Coherencia con los DATOS (hallazgo de la regresión del núcleo, F1):
        # el LLM a veces pedía `graduated_colors` sobre un campo CATEGÓRICO
        # (p. ej. lotdispers = N/D). Sin rangos numéricos que calcular, la
        # construcción degradaba EN SILENCIO a single_symbol y el usuario
        # veía un color plano sin saber por qué. El código aporta el hecho y
        # el LLM decide de nuevo, una vez; si insiste, la degradación se
        # declara (abajo, [DEGRADED]).
        incoherencia = self._diseno_incoherente(parsed, field_analysis)
        if incoherencia:
            logger.info(f"[SymbologyAgent] diseño incoherente, repregunto: {incoherencia}")
            parsed = await disenar(
                f"Tu diseño no se puede aplicar: {incoherencia} "
                "Vuelve a llamar a `design_symbology` con un diseño coherente con "
                "esos datos y con lo que pidió el usuario."
            )
            incoherencia = self._diseno_incoherente(parsed, field_analysis)
            if incoherencia:
                parsed["reasoning"] = (
                    f"[DEGRADED] el diseño seguía sin poder aplicarse ({incoherencia}) "
                    "— apliqué un estilo neutro"
                )
                parsed["symbology_type"] = "single_symbol"
        return parsed

    def _sanear_diseno(self, parsed: dict[str, Any], field_analysis: dict) -> None:
        """Anti-alucinación: campos que existen, valores de los enums y cortes/colores válidos."""
        # Validar nombres de campos contra el schema (anti-alucinación).
        faltan = []
        for key in ("label_field", "classification_field"):
            val = parsed.get(key)
            if val is not None and val not in field_analysis:
                logger.warning(
                    f"[SymbologyAgent] LLM eligió campo inexistente {key}={val!r} — descartado"
                )
                parsed[key] = None
                faltan.append(str(val))
        if faltan:
            # V3 F5 (regresión 04): «colorea por manzcodigo» sobre una capa que se trajo sin ese
            # campo acababa en un color único con un motivo confuso; el bucle necesita el HECHO
            # para decidir (p. ej. volver a traer la capa con ese campo).
            parsed["reasoning"] = (f"La capa NO tiene el campo «{', '.join(sorted(set(faltan)))}»; sus campos: "
                                   f"{', '.join(list(field_analysis)[:20])}. " + str(parsed.get("reasoning") or ""))

        # Validar enum values (anti-alucinación).
        valid_types = {e.value for e in SymbologyType}
        if parsed.get("symbology_type") not in valid_types:
            logger.warning(
                f"[SymbologyAgent] symbology_type inválido: "
                f"{parsed.get('symbology_type')!r} — fallback a single_symbol"
            )
            parsed["symbology_type"] = "single_symbol"

        valid_methods = {e.value for e in ClassificationMethod} | {None}
        if parsed.get("classification_method") not in valid_methods:
            parsed["classification_method"] = None

        valid_schemes = {e.value for e in ColorScheme}
        if parsed.get("color_scheme") not in valid_schemes:
            logger.warning(
                f"[SymbologyAgent] color_scheme inválido: "
                f"{parsed.get('color_scheme')!r} — fallback a Blues"
            )
            parsed["color_scheme"] = "Blues"

        # Sanitizar manual_class_breaks (umbrales+colores explícitos del
        # usuario). El LLM interpreta "rojo >1000, azul <200" → breaks; el
        # código valida hex/umbrales (hecho/seguridad, no adivinanza).
        parsed["manual_class_breaks"] = self._sanitize_manual_breaks(
            parsed.get("manual_class_breaks")
        )
        parsed["category_colors"] = self._sanitize_category_colors(parsed.get("category_colors"))

    @staticmethod
    def _diseno_incoherente(parsed: dict[str, Any], field_analysis: dict) -> str | None:
        """Hecho verificable que impide aplicar el diseño, o None si es aplicable.

        Solo hechos de los datos (tipo real del campo): la elección de estilo
        sigue siendo del LLM.
        """
        tipo = parsed.get("symbology_type")
        campo = parsed.get("classification_field")
        if tipo in ("graduated_colors", "graduated_symbols") and not parsed.get(
            "manual_class_breaks"
        ):
            if not campo:
                return f"{tipo} necesita un campo numérico para clasificar y no indicaste ninguno."
            info = field_analysis.get(campo) or {}
            numericos = {DataType.NUMERIC_CONTINUOUS.value, DataType.NUMERIC_DISCRETE.value}
            if info.get("data_type") not in numericos:
                valores = info.get("unique_values") or list(info.get("value_counts") or {})[:8]
                return (
                    f"el campo '{campo}' es {info.get('data_type', 'no numérico')} "
                    f"(valores: {valores}) y {tipo} exige un campo NUMÉRICO para "
                    "calcular rangos."
                )
        return None
