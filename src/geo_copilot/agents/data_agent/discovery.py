"""
DiscoveryAgent — agente LLM que traduce NL → plan de búsqueda Hub.

Sub-módulo del DataAgent (no es un agente del grafo LangGraph). El LLM
produce un plan completo de parámetros para `arcgis_search_items` del servidor MCP de ArcGIS (text_query,
tags_any, service_types, source_any, etc.) junto con queries alternativas
que se prueban si la primaria devuelve 0 resultados.

Requiere LLM. Sin LLM el método `.discover()` falla con error claro —
preferimos no servir resultados sesgados desde una heurística determinista.
"""

from __future__ import annotations

from typing import Any

from geo_copilot.core.llm_client import LLMClient, LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import parse_json_from_llm

from .catalogo_regiones import OFFICIAL_OWNERS_CO
from .hub_items import HubItem, count_place_matches, rank_results
from .servicio_arcgis import buscar_en_hub

logger = get_logger(__name__)

# F4: el plan de búsqueda (pistas, región, plan del LLM y sus parámetros, ficha y orden) vive en
# plan_hub; se reexporta porque las pruebas y otros módulos lo usan desde aquí.
from .plan_hub import (  # noqa: F401
    DiscoveryHints,
    DiscoveryResponse,
    Intent,
    _catalog_context_for_llm,
    _extract_region_from_query,
    _ficha,
    _llm_build_hub_plan,
    _ordenar_por_juicio,
    _plan_to_search_params,
    _resolve_region,
)

#: Candidatos que ve el juez (los primeros por hechos) y búsquedas extra que puede pedir.
CANDIDATOS_A_JUZGAR = 20
MAX_REBUSQUEDAS = 2


_JUEZ_SYSTEM = """Juzgas candidatos de una búsqueda de datos geográficos (ArcGIS Online y Hub) para un
usuario que pidió algo concreto. Cada candidato trae hechos: título, tipo de servicio, de quién es
(créditos u organización), cuenta, vistas, si es de una sola capa, fecha y un trozo de descripción.

Tu trabajo:
1. `relevantes`: los NÚMEROS de los candidatos que sirven de verdad para lo pedido (tema Y lugar),
   del mejor al peor. Deja fuera los que no tratan de lo pedido, aunque compartan palabras (p. ej.
   «Velocidades de tráfico» no es «la red vial»; una capa de otro municipio no es la del lugar pedido).
   Para elegir entre varios que sirven, pesa quién lo publica (una entidad que lo produce frente a una
   copia personal o un ejercicio de clase), su uso, su fecha y si es el dataset en sí y no un derivado.
2. `otra_busqueda`: SOLO si ningún candidato sirve bien (o los que sirven son copias dudosas), el texto
   de UNA nueva búsqueda que probablemente lo encuentre: el nombre técnico con que lo titularía quien lo
   publica, un sinónimo, el lugar escrito de otra forma o un nivel geográfico más amplio. Las búsquedas
   comparan palabras con títulos y etiquetas y EXIGEN QUE APAREZCAN TODAS (medido: «hospitales Barranquilla»
   da 0; «salud Barranquilla» da 15): usa 2-3 palabras, un término más amplio antes que una frase. No
   repitas lo ya buscado. Si los relevantes bastan: null.
3. `razon`: una frase para el usuario, en español, sobre qué encontraste (no sobre tus reglas).

Responde SOLO JSON: {"relevantes": [3, 1], "otra_busqueda": "texto" | null, "razon": "..."}"""


# =============================================================================
# DiscoveryAgent
# =============================================================================
class DiscoveryAgent:
    """Agente LLM que traduce NL → plan de búsqueda Hub + retry refinado."""

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm = llm_client

    async def discover(  # noqa: C901, PLR0912, PLR0915
        self,
        query: str,
        *,
        hints: DiscoveryHints | None = None,
        conversation_history: list[dict] | None = None,
    ) -> DiscoveryResponse:
        query = (query or "").strip()
        hints = hints or DiscoveryHints()

        if not query and not any(
            [hints.service_types, hints.bbox, hints.owners, hints.only_official_co]
        ):
            return DiscoveryResponse(
                intent="exploratory",
                suggested_refinements=[
                    {"action": "type_query", "label": "Escribe un tema, entidad o zona"}
                ],
            )

        if self.llm is None:
            # Sin LLM no podemos ser un agente. Antes había una cascada
            # determinista de heurísticas y 6 fallbacks que adivinaban qué
            # filtros relajar — producían resultados sesgados sin que el
            # usuario lo supiera. Mejor falla fuerte aquí.
            raise RuntimeError(
                "DiscoveryAgent requiere un LLM. Verifica la configuración del "
                "cliente LLM (settings.openai_* o azure_*). No hay modo offline "
                "porque las heurísticas deterministas inducían sesgo regional."
            )

        active_region = _resolve_region(hints)

        # #6 (audit 2026-06-13): si el usuario NO pasó región por API y el default
        # está activo, ver si menciona OTRA región en el TEXTO. Colombia sigue
        # siendo prioridad: solo se anula si la nombra explícitamente.
        if hints.region is None and not hints.global_mode:
            from .catalogo_regiones import REGIONS
            detected = await _extract_region_from_query(query, self.llm, list(REGIONS))
            if detected and detected != active_region:
                active_region = detected if detected in REGIONS else "global"
                logger.info(
                    f"[discovery] región explícita en el texto: '{detected}' "
                    f"→ búsqueda en '{active_region}'"
                )

        # El LLM construye el plan completo de Hub: text_query + filtros +
        # alternativas + place_focus. No hay builder determinista — el LLM
        # ve el catálogo regional como contexto y decide qué aplicar.
        hub_plan = await _llm_build_hub_plan(
            query, llm=self.llm, region=active_region,
            conversation_history=conversation_history,
        )
        if not hub_plan:
            raise RuntimeError(
                "DiscoveryAgent: el LLM no produjo un plan válido. Ver logs "
                "para el contenido crudo de la respuesta."
            )

        params = _plan_to_search_params(hub_plan["primary"], hints=hints)
        alternatives = hub_plan.get("alternatives", []) or []
        place_focus_from_plan = hub_plan.get("place_focus")
        intent = hub_plan.get("intent_label", "exploratory")

        skip_search = not any(
            params.get(k) for k in (
                "text_query", "tags_any", "owner_any", "source_any",
                "service_types", "bbox", "only_official_co",
            )
        )
        debug: dict[str, Any] = {
            "llm_plan": hub_plan,
            "search_params": dict(params),
            "region": active_region,
        }

        if skip_search:
            return DiscoveryResponse(
                intent=intent,
                items=[],
                suggested_refinements=[
                    {"action": "broaden", "label": "Quita filtros y busca por palabra libre"},
                ],
                debug=debug,
            )

        async def search_hub(**p: Any) -> list[HubItem]:
            encontrados, avisos = await buscar_en_hub(**p)
            if avisos:  # una página del Hub falló: la lista puede estar incompleta
                debug.setdefault("avisos_hub", []).extend(avisos)
            return encontrados

        items = await search_hub(**params)

        # Si la primaria devolvió 0, recorremos las alternativas del LLM
        # (1-3 queries de respaldo) en orden. El LLM las pensó para este
        # query específico — no hay heurísticas de "quitar bbox" o "subir
        # a tags_any" porque el LLM ya las incluye si tienen sentido.
        if not items and alternatives:
            for alt in alternatives[:3]:
                alt_params = _plan_to_search_params(alt, hints=hints)
                if not any(
                    alt_params.get(k) for k in (
                        "text_query", "tags_any", "service_types",
                        "source_any", "owner_any",
                    )
                ):
                    continue
                alt_items = await search_hub(**alt_params)
                if alt_items:
                    items = alt_items
                    debug.setdefault("llm_alternatives_used", []).append({
                        "params": alt_params,
                        "reason": alt.get("reason"),
                        "found": len(alt_items),
                    })
                    break
                debug.setdefault("llm_alternatives_tried", []).append({
                    "params": alt_params,
                    "reason": alt.get("reason"),
                    "found": 0,
                })

        # place_focus dispara dos cosas:
        # 1. Bonus de score para items que mencionen el lugar.
        # 2. Si los items NO mencionan el lugar pedido, pedimos al LLM una
        #    query refinada (ej. Zipaquirá → "Cundinamarca ortofoto"). Si
        #    el retry trae items que SÍ matchean, los preferimos sobre los
        #    engañosos.
        place_name = place_focus_from_plan
        # E4 (audit 2026-06-13): bbox de la región activa para penalizar en el
        # ranking items con extent fuera de ella (NO descarte). 'global' → None.
        from .catalogo_regiones import get_region
        _region_bbox = (
            get_region(active_region).country_bbox
            if active_region and active_region != "global" else None
        )
        # #4 (audit 2026-06-14): términos del TEMA (no del lugar/país) para
        # bonificar relevancia temática en el ranking; sin esto "hospitales" traía
        # genéricos de país ("Carnavales Colombia").
        # DAT-04 (auditoría E2E): los términos temáticos los produce el LLM como
        # parte del plan de discovery (mismo plan que place_focus/intent_label),
        # no una heurística. Antes se derivaban con split + stopwords ES
        # hardcoded + len>=4, que descartaba acrónimos institucionales cortos y
        # muy relevantes del catálogo (SGC, PNN, IDF, MDT, río) y asumía español.
        # El LLM ya ve el catálogo → decide el tema sin esos sesgos (LLM-pilar).
        _theme_keywords = [
            str(w).strip()
            for w in (hub_plan.get("theme_keywords") or [])
            if str(w).strip()
        ]
        ranked = rank_results(
            items, place=place_name, region_bbox=_region_bbox,
            theme_keywords=_theme_keywords,
        )
        matches = count_place_matches(ranked, place_name)
        place_mismatch = bool(place_name) and matches == 0 and bool(ranked)

        # El LLM JUZGA los candidatos viendo sus hechos (lo que el código no sabe: si «Velocidades
        # Bitcarrier» sirve para «vías de Bogotá»). Ordena los que sirven y, si ninguno sirve bien,
        # pide OTRA búsqueda (término técnico, sinónimo, lugar): se busca y se vuelve a juzgar sobre
        # todo lo encontrado. Sin juicio (el LLM falló), queda el orden por hechos y se dice.
        criterio: str | None = None
        otras: list[str] = []
        relevantes: int | None = None
        intentadas = {str(params.get("text_query") or "").strip().lower()}
        for ronda in range(1 + MAX_REBUSQUEDAS):
            juicio = await self._juzgar(query, place_name, ranked[:CANDIDATOS_A_JUZGAR], otras)
            if juicio is None:
                break
            ranked = _ordenar_por_juicio(ranked, juicio["relevantes"])
            criterio, relevantes = juicio["razon"], len(juicio["relevantes"])
            debug.setdefault("juicio", []).append({**juicio, "ronda": ronda})
            nueva = (juicio.get("otra_busqueda") or "").strip()
            if not nueva or nueva.lower() in intentadas or ronda == MAX_REBUSQUEDAS:
                break
            intentadas.add(nueva.lower())
            otras.append(nueva)
            nuevos = await search_hub(**{**params, "text_query": nueva})
            vistos = {it.id for it in ranked}
            ranked = rank_results(
                ranked + [it for it in nuevos if it.id not in vistos], place=place_name,
                region_bbox=_region_bbox, theme_keywords=_theme_keywords,
            )
        matches = count_place_matches(ranked, place_name)
        place_mismatch = bool(place_name) and matches == 0 and bool(ranked)

        top = ranked[0] if ranked else None
        # Se avisa cuando el primero no trae NADA que diga de quién es: ni cuenta institucional conocida
        # ni créditos declarados. Con créditos («Secretaría Distrital de Movilidad») la tarjeta los
        # muestra y juzga quien la lee; la lista fija de cuentas no puede conocer todas las entidades.
        authority_warning = bool(
            top and not (
                (top.owner and any(top.owner in v for v in OFFICIAL_OWNERS_CO.values()))
                or (top.credits or "").strip()
            )
        )

        suggestions: list[dict[str, Any]] = []
        if not ranked:
            suggestions = [
                {"action": "official_co", "label": "Limitar a cuentas oficiales CO"},
                {"action": "drop_bbox", "label": "Quitar filtro de zona"},
                {"action": "free_text", "label": f"Buscar libre: '{query}'"} if query else
                {"action": "type_query", "label": "Escribe un tema, entidad o zona"},
            ]
        elif place_mismatch:
            suggestions = [
                {
                    "action": "broaden_to_region",
                    "label": f"Buscar en la región (no hay datos específicos de '{place_name}')",
                },
                {"action": "free_text", "label": f"Buscar libre: '{place_name}'"},
            ]

        return DiscoveryResponse(
            items=ranked,
            intent=intent,
            authority_warning=authority_warning,
            place_mismatch=place_mismatch,
            place_queried=place_name,
            suggested_refinements=suggestions,
            debug=debug,
            criterio=criterio,
            otras_busquedas=otras,
            relevantes=relevantes,
        )

    async def _juzgar(self, query: str, lugar: str | None, candidatos: list[HubItem],
                      ya_buscado: list[str]) -> dict[str, Any] | None:
        """{relevantes: [índices 0-based, mejor primero], otra_busqueda, razon} o None si no hubo juicio."""
        if self.llm is None:
            return None
        lista = "\n".join(f"{i + 1}. {_ficha(it)}" for i, it in enumerate(candidatos)) or "(ninguno)"
        user_msg = (
            f"Pedido del usuario: {query}\n"
            + (f"Lugar pedido: {lugar}\n" if lugar else "")
            + (f"Ya se buscó también: {', '.join(ya_buscado)}\n" if ya_buscado else "")
            + f"\nCandidatos encontrados ({len(candidatos)}):\n{lista}\n\nDevuelve el JSON:"
        )
        try:
            resp = await self.llm.chat(
                [LLMMessage(role="system", content=_JUEZ_SYSTEM), LLMMessage(role="user", content=user_msg)],
                temperature=0.1,
                max_tokens=400,
            )
        except Exception as exc:  # proveedor LLM puede lanzar cualquier cosa; sin juicio queda el orden por hechos
            logger.warning(f"[discovery] el juicio de candidatos falló: {exc}", exc_info=True)
            return None
        datos = parse_json_from_llm(resp.content, default={})
        if not isinstance(datos, dict) or not isinstance(datos.get("relevantes"), list):
            logger.warning(f"[discovery] juicio sin 'relevantes': {(resp.content or '')[:200]}")
            return None
        indices: list[int] = []
        for n in datos["relevantes"]:
            if isinstance(n, int) and 1 <= n <= len(candidatos) and n - 1 not in indices:
                indices.append(n - 1)
        otra = datos.get("otra_busqueda")
        return {"relevantes": indices, "otra_busqueda": otra.strip() if isinstance(otra, str) else None,
                "razon": str(datos.get("razon") or "")[:300]}


__all__ = ["DiscoveryAgent", "DiscoveryHints", "DiscoveryResponse"]
