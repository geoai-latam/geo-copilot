"""El PLAN de búsqueda en ArcGIS Hub: las pistas y la respuesta del discovery, la región (de las
pistas o de la consulta, por el LLM), el contexto del catálogo, el plan que diseña el LLM y su
traducción a parámetros, y la ficha y el orden con que se juzgan los candidatos.

Salió de `discovery.py` (F4 del plan de calidad: discovery.py tenía 638 líneas), tal cual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import parse_json_from_llm
from geo_copilot.prompts import cargar_prompt

from .catalogo_regiones import all_official_owners
from .hub_items import HubItem

logger = get_logger("geo_copilot.agents.data_agent.discovery")

Intent = Literal[
    "entity_focused",
    "topic_focused",
    "zone_focused",
    "service_focused",
    "imagery_focused",
    "recent_focused",
    "exploratory",
]

_HUB_PLAN_SYSTEM = cargar_prompt("discovery_plan_hub")


@dataclass
class DiscoveryHints:
    """Hints opcionales que el usuario puede pasar desde el panel UI."""

    service_types: list[str] | None = None  # ["Feature Service", "Map Service", "Image Service"]
    bbox: list[float] | None = None
    only_official_co: bool = False  # chip UI "Oficial CO" → expande a owner_any de todas las entidades oficiales
    owners: list[str] | None = None
    tags_any: list[str] | None = None  # filter[tags]=any(...) — más preciso que bbox
    max_results: int = 50

    # Multi-región (cambio 2026-05-24):
    # `region` selecciona el catálogo a usar para detección de entidades/zonas
    # y para el sesgo regional automático. None → región activa por defecto
    # del producto. "global" → desactiva el sesgo regional (búsqueda mundial).
    region: str | None = None
    # `global_mode=True` es equivalente a region="global" — atajo legible.
    global_mode: bool = False


@dataclass
class DiscoveryResponse:
    """Respuesta unificada del DiscoveryAgent."""

    items: list[HubItem] = field(default_factory=list)
    intent: Intent = "exploratory"
    authority_warning: bool = False
    # True cuando el usuario mencionó un lugar específico (ej. "zipaquira")
    # y NINGÚN item devuelto lo menciona en título/descripción/tags. Sirve
    # para que la capa de presentación advierta en lugar de engañar.
    place_mismatch: bool = False
    place_queried: str | None = None
    suggested_refinements: list[dict[str, Any]] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)
    # El juicio del LLM sobre los candidatos: por qué ese orden, qué otras búsquedas hizo y
    # cuántos sirven de verdad para lo pedido (None = no hubo juicio: orden por hechos).
    criterio: str | None = None
    otras_busquedas: list[str] = field(default_factory=list)
    relevantes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [it.to_dict() for it in self.items],
            "intent": self.intent,
            "authority_warning": self.authority_warning,
            "place_mismatch": self.place_mismatch,
            "place_queried": self.place_queried,
            "suggested_refinements": self.suggested_refinements,
            "debug": self.debug,
            "criterio": self.criterio,
            "otras_busquedas": self.otras_busquedas,
            "relevantes": self.relevantes,
        }


def _resolve_region(hints: DiscoveryHints) -> str | None:
    """Resolver qué región usar para detección y sesgo.

    - hints.global_mode=True   → "global" (sin sesgo regional)
    - hints.region="global"    → "global"
    - hints.region="X" (en cat)→ X
    - hints.region="X" (NO cat)→ "global" (F2.2: el usuario pidió EXPLÍCITAMENTE
      una región para la que no hay catálogo; búsqueda neutral/global. NUNCA
      colapsar a la región por defecto — eso anclaría una query de Perú/México
      al catálogo colombiano en silencio.)
    - hints.region=None (sin especificar) → región activa configurada del
      producto (default del despliegue; sobrescribible vía settings). Esto NO
      es un hardcode: es el default configurado, y el usuario puede anular por
      sesión pasando `region`.
    """
    from .catalogo_regiones import REGIONS, get_active_region
    if hints.global_mode:
        return "global"
    if hints.region:
        if hints.region in REGIONS:
            return hints.region
        # F2.2 kill switch: región explícita sin catálogo → global, no default.
        logger.warning(
            f"[discovery] region '{hints.region}' no está en el catálogo "
            f"({list(REGIONS)}) — búsqueda GLOBAL/neutral (sin asumir "
            f"la región por defecto para no anclar a otro país)"
        )
        return "global"
    return get_active_region()


async def _extract_region_from_query(
    query: str, llm: LLMClient, regions: list[str]
) -> str | None:
    """#6 (audit 2026-06-13): detecta si el usuario menciona EXPLÍCITAMENTE un
    país/región en el TEXTO de la consulta.

    Colombia (el default del despliegue) es la PRIORIDAD: si el usuario no
    menciona ninguna región, devolvemos ``None`` y el flujo mantiene el default.
    Solo si nombra explícitamente otra región devolvemos su clave de catálogo,
    o ``'global'`` si no está catalogada (el kill-switch de ``_resolve_region``
    evita anclar a otro país). Cierra F2.2b: "hospitales en Lima, Perú" ya no
    colapsa a Colombia en silencio.

    Returns:
        Clave de región catalogada, ``'global'``, o ``None`` si no se mencionó.
    """
    cat = ", ".join(regions) or "(ninguna)"
    prompt = (
        "¿El usuario menciona EXPLÍCITAMENTE un país o región geográfica "
        "para su búsqueda de datos?\n"
        f"Regiones catalogadas: {cat}.\n"
        f'Consulta: "{query}"\n\n'
        'Responde SOLO JSON {"region": <valor>} donde <valor> es:\n'
        "- la clave EXACTA de una región catalogada si la menciona "
        "(ej. 'Bogotá'/'Colombia' -> 'colombia');\n"
        "- 'global' si menciona un país/región que NO está catalogado "
        "(ej. 'Lima, Perú', 'México', 'España');\n"
        "- null si NO menciona ninguna región."
    )
    try:
        resp = await llm.chat([LLMMessage(role="user", content=prompt)])
        data = parse_json_from_llm(resp.content, default=None)
        if isinstance(data, dict):
            reg = data.get("region")
            if isinstance(reg, str) and reg.strip():
                return reg.strip().lower()
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[discovery] _extract_region_from_query falló: {exc}")
        return None


def _catalog_context_for_llm(region: str | None) -> str:
    """Resumen del catálogo regional + indicadores de sesgo geográfico.

    El LLM usa esto para:
    - decidir qué entidades aplicar como source_any
    - saber qué zonas son "conocidas" del catálogo
    - **siempre** anclar la búsqueda al país/región (sin esto, Hub devuelve
      ortofotos suecas para una query colombiana)
    """
    from .catalogo_regiones import REGIONS, get_region

    if region == "global" or region not in REGIONS:
        return (
            "Modo búsqueda: GLOBAL. El usuario activó búsqueda mundial — "
            "no apliques sesgo regional ni source_any de un solo país."
        )

    r = get_region(region)
    parts: list[str] = [
        f"Región activa del producto: **{r.label}** (key: `{region}`).",
        f"⚠️ ANCLAJE GEOGRÁFICO OBLIGATORIO: este producto se usa desde {r.label}. "
        f"TODA query del usuario asume contexto {r.label} salvo que el usuario "
        f"pida explícitamente otro país. Si no nombra país, asume {r.label}.",
    ]

    if r.entities:
        ent_lines = []
        for ek, ent in r.entities.items():
            aliases = ", ".join(ent.aliases[:4]) if ent.aliases else ""
            srcs_sample = ", ".join(ent.sources[:2]) if ent.sources else "—"
            ent_lines.append(
                f"  • `{ek}` (aliases: {aliases}; sources ejemplo: {srcs_sample})"
            )
        parts.append("Entidades oficiales del país:\n" + "\n".join(ent_lines))

    if r.zones:
        zone_lines = []
        for zk, zone in r.zones.items():
            aliases = ", ".join(zone.aliases[:3]) if zone.aliases else ""
            zone_lines.append(f"  • `{zk}` (aliases: {aliases})")
        parts.append("Zonas conocidas del catálogo:\n" + "\n".join(zone_lines))

    return "\n\n".join(parts)


async def _llm_build_hub_plan(
    query: str,
    *,
    llm: LLMClient | None,
    region: str | None,
    conversation_history: list[dict] | None = None,
) -> dict[str, Any] | None:
    """Pedirle al LLM el plan completo de búsqueda Hub.

    Devuelve None si el LLM no está disponible o el JSON sale mal. En ese
    caso el caller cae al builder determinista (`_build_search_params`).
    """
    if llm is None:
        return None
    catalog_ctx = _catalog_context_for_llm(region)
    # #6 (audit 2026-06-14): contexto conversacional para resolver referencias
    # elípticas (ej. "busca datos tipo feature" tras hablar de Zipaquirá hereda el
    # lugar). Mismo formato que el router; el LLM resuelve la elipsis (LLM-pilar).
    conv_block = ""
    if conversation_history:
        _lines = ["CONVERSACIÓN RECIENTE (úsala para resolver referencias del usuario):"]
        for _m in conversation_history[-6:]:
            _role = "Usuario" if _m.get("role") == "user" else "Asistente"
            _lines.append(f"  {_role}: {(_m.get('content') or '')[:300]}")
        conv_block = "\n".join(_lines) + "\n\n"
    user_msg = f"{conv_block}Query del usuario: {query}\n\n{catalog_ctx}\n\nDevuelve el JSON del plan:"
    try:
        resp = await llm.chat(
            [
                LLMMessage(role="system", content=_HUB_PLAN_SYSTEM),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.1,
            max_tokens=600,
        )
        plan = parse_json_from_llm(resp.content, default={})
        if not isinstance(plan, dict) or "primary" not in plan:
            logger.warning(f"[discovery] LLM plan sin 'primary': {resp.content[:200]}")
            return None
        if not isinstance(plan.get("primary"), dict):
            return None
        # Validación mínima: text_query debe existir (puede ser cualquier string).
        primary = plan["primary"]
        if "text_query" not in primary:
            primary["text_query"] = query
        # Normalizar lists
        for k in ("tags_any", "service_types", "source_any"):
            v = primary.get(k)
            if v is None:
                continue
            if not isinstance(v, list):
                primary[k] = None
        # alternatives debe ser lista (puede estar vacía)
        if not isinstance(plan.get("alternatives"), list):
            plan["alternatives"] = []
        return plan
    except Exception as exc:  # proveedor LLM puede lanzar cualquier cosa; sin plan el caller usa _build_search_params
        logger.warning(f"[discovery] _llm_build_hub_plan failed: {exc}", exc_info=True)
        return None


def _plan_to_search_params(
    plan_step: dict[str, Any],
    *,
    hints: DiscoveryHints,
) -> dict[str, Any]:
    """Un paso del plan del LLM (primario o alternativa) → argumentos de `arcgis_search_items`."""
    params: dict[str, Any] = {
        "max_results": hints.max_results,
        "only_loadable": True,
    }
    tq = (plan_step.get("text_query") or "").strip()
    if tq:
        params["text_query"] = tq
    for k in ("tags_any", "service_types", "source_any"):
        v = plan_step.get(k)
        if isinstance(v, list) and v:
            params[k] = list(v)
    if plan_step.get("modified_after"):
        params["modified_after"] = plan_step["modified_after"]
        params["sort"] = "-modified"
    # Hints del panel UI sobrescriben (input humano explícito).
    if hints.service_types:
        params["service_types"] = list(hints.service_types)
    if hints.owners:
        params["owner_any"] = list(hints.owners)
    if hints.tags_any:
        params["tags_any"] = list(hints.tags_any)
    if hints.bbox:
        params["bbox"] = hints.bbox
    if hints.only_official_co:
        # chip «Oficial CO» del panel: los publicadores oficiales del catálogo de la región
        params["owner_any"] = sorted(set(params.get("owner_any") or []) | set(all_official_owners()))
    return params


def _ficha(it: HubItem) -> str:
    """Un candidato en una línea, con los hechos para juzgarlo (no un veredicto)."""
    partes = [it.title or "(sin título)", it.service_type or it.type_raw or ""]
    quien = (it.credits or "").strip() or it.org or it.owner
    if quien:
        partes.append(f"de: {quien[:80]}")
    if it.owner and it.owner != quien:
        partes.append(f"cuenta: {it.owner}")
    if isinstance(it.views, int):
        partes.append(f"{it.views} vistas")
    if it.single_layer is True:
        partes.append("una capa")
    if it.modified:
        partes.append(f"modificado {str(it.modified)[:10]}")
    if it.description:
        partes.append(f"«{' '.join(str(it.description).split())[:160]}»")
    return " · ".join(p for p in partes if p)


def _ordenar_por_juicio(items: list[HubItem], relevantes: list[int]) -> list[HubItem]:
    """Los que el LLM juzgó relevantes, en su orden; después el resto, en el orden por hechos."""
    elegidos = [items[i] for i in relevantes if 0 <= i < len(items)]
    ids = {id(it) for it in elegidos}
    return elegidos + [it for it in items if id(it) not in ids]
