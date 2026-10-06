"""Items del catálogo de ArcGIS Hub y su ORDEN para el usuario (T5.2).

La búsqueda la hace el servidor MCP de ArcGIS (`arcgis_search_items`); aquí queda lo que usa
el conocimiento del núcleo: el catálogo de la región (qué publicadores son oficiales, su
extensión) para ordenar los items y contar cuáles mencionan el lugar pedido. El plan de
búsqueda (qué filtros, qué tema, qué lugar) lo decide el LLM en `DiscoveryAgent`.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .catalogo_regiones import OFFICIAL_OWNERS_CO, OFFICIAL_SOURCES_CO


@dataclass
class HubItem:
    """Item de hub normalizado al shape que consume el frontend."""

    id: str
    source: str  # "hub" | "arcgis_online" | "socrata" | "internal"
    org: str
    title: str
    description: str
    service_type: str  # "FeatureServer" | "MapServer" | "ImageServer" | ...
    service_url: str
    layer_id: int | None = None
    owner: str = ""
    source_field: str = ""  # el campo `source` original del item
    tags: list[str] = field(default_factory=list)
    type_raw: str = ""  # "Feature Service", etc.
    modified: str | None = None  # ISO
    created: str | None = None
    thumbnail_url: str | None = None
    extent: list[float] | None = None  # [minX, minY, maxX, maxY]
    hub_url: str | None = None
    redirected_from: str | None = None
    # hechos para juzgar la fuente (los trae ArcGIS Online; el Hub v3 no los da)
    credits: str = ""
    views: int | None = None
    completeness: int | None = None  # 0-100: lo completos que están sus metadatos
    single_layer: bool | None = None
    sources: list[str] = field(default_factory=list)  # qué buscadores lo encontraron
    raw: dict[str, Any] = field(default_factory=dict)
    rank_score: float = 0.0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> HubItem:
        """Un item tal como lo devuelve `arcgis_search_items` del servidor de ArcGIS."""
        campos = set(cls.__dataclass_fields__) - {"raw", "rank_score"}
        return cls(**{k: v for k, v in d.items() if k in campos})

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "org": self.org,
            "title": self.title,
            "description": self.description,
            "service_type": self.service_type,
            "service_url": self.service_url,
            "layer_id": self.layer_id,
            "owner": self.owner,
            "source_field": self.source_field,
            "tags": self.tags,
            "type_raw": self.type_raw,
            "modified": self.modified,
            "created": self.created,
            "thumbnail_url": self.thumbnail_url,
            "extent": self.extent,
            "hub_url": self.hub_url,
            "redirected_from": self.redirected_from,
            "credits": self.credits,
            "views": self.views,
            "completeness": self.completeness,
            "single_layer": self.single_layer,
            "sources": self.sources,
            "rank_score": round(self.rank_score, 3),
        }


def bbox_intersecta(item_extent: list[float] | None, bbox: list[float]) -> bool:
    """Test estricto de intersección 2D entre extent del item y bbox solicitado.

    Reglas (estrictas a propósito — la versión laxa dejaba pasar items USA
    cuando el usuario pedía Cundinamarca):

    1. Si el item NO tiene extent declarado, **se descarta**. Sin extent no
       hay forma de saber si es del área pedida; dejar pasar inundaba el
       panel con items globales sin metadatos geográficos.
    2. Si los bboxes no intersectan, descartar.
    3. Si el extent del item es "global-ish" (cubre >180° en longitud o
       >90° en latitud, ej. [-180,-90,180,90]) y el bbox pedido es
       pequeño, descartar. Items con extent global suelen ser layers
       mundiales (MODIS, weather) que no son específicos del área.
    4. Si el área del extent del item es >50× el área del bbox pedido,
       descartar — el item es regional/continental, no específico.
    """
    if not item_extent or len(item_extent) != 4:
        return False
    ix0, iy0, ix1, iy1 = item_extent
    bx0, by0, bx1, by1 = bbox
    # Intersección 2D estándar
    if ix1 < bx0 or ix0 > bx1 or iy1 < by0 or iy0 > by1:
        return False

    item_w = max(0.0, ix1 - ix0)
    item_h = max(0.0, iy1 - iy0)
    bbox_w = max(0.001, bx1 - bx0)
    bbox_h = max(0.001, by1 - by0)

    # Global-ish extent → descartar si el bbox pedido es pequeño (<60° lado).
    if (item_w > 180 or item_h > 90) and (bbox_w < 60 and bbox_h < 60):
        return False

    # Área-ratio: item demasiado grande relativo a la zona pedida. El
    # umbral 200× deja pasar items de nivel país (Colombia ≈ 66× Cundinamarca)
    # pero descarta items continentales / globales (USA ≈ 375× Cundinamarca).
    item_area = item_w * item_h
    bbox_area = bbox_w * bbox_h
    if bbox_area > 0 and item_area > bbox_area * 200:
        return False

    return True


def _modified_recency_bonus(modified: str | None) -> float:
    if not modified:
        return 0.0
    try:
        # Hub devuelve epoch ms o ISO; intentamos ambos.
        if isinstance(modified, (int, float)):
            ts = float(modified) / 1000.0
            dt = datetime.fromtimestamp(ts, tz=UTC)
        else:
            dt = datetime.fromisoformat(str(modified).replace("Z", "+00:00"))
    # Fecha no parseable o epoch fuera de rango: sin bonus de recencia.
    except (ValueError, OverflowError, OSError):
        return 0.0
    if dt.tzinfo is None:
        # ArcGIS Online da la fecha sola («2024-07-23»): sin zona, es UTC (si no, restar con now(UTC) lanza)
        dt = dt.replace(tzinfo=UTC)
    age_days = (datetime.now(UTC) - dt).days
    if age_days < 0:
        return 0.0
    if age_days <= 365:
        # Lineal: hoy = +2.0, hace 1 año = 0.0
        return max(0.0, 2.0 * (1 - age_days / 365))
    return 0.0


def _normalize(s: str) -> str:
    """Lowercase + sin tildes para matching laxo de nombres de lugar."""
    if not s:
        return ""
    out = s.lower()
    for a, b in (("á","a"),("é","e"),("í","i"),("ó","o"),("ú","u"),("ñ","n")):
        out = out.replace(a, b)
    return out


def _place_match_bonus(item: HubItem, place: str | None) -> float:
    """Bonus si el item realmente menciona el lugar pedido.

    Antes el ranker era ciego al lugar — buscar 'ortofotos de zipaquira'
    devolvía top-10 Medellín porque Hub matchea fuzzy y Medellín tiene más
    ortofotos publicadas. Con este bonus, items que contienen 'zipaquira'
    en título / descripción / tags suben mucho; los que NO contienen, se
    quedan en su score base.
    """
    if not place:
        return 0.0
    p = _normalize(place)
    if not p or len(p) < 3:
        return 0.0
    title = _normalize(item.title)
    desc = _normalize(item.description)
    tags = " ".join(_normalize(t) for t in item.tags)
    bonus = 0.0
    if p in title:
        bonus += 8.0  # match en título es señal fuerte
    if p in desc:
        bonus += 3.0
    if p in tags:
        bonus += 4.0
    return bonus


def _theme_match_bonus(item: HubItem, theme_keywords: list[str] | None) -> float:
    """Bonus si el item realmente trata del TEMA pedido (no solo del lugar/país).

    #4 (audit 2026-06-14): sin esto, "hospitales" devolvía "Carnavales Colombia" /
    "Colombia Country" arriba (genéricos de país) porque el ranker premiaba el
    lugar pero NO si el item menciona el tema. Premiamos items cuyo título/desc/
    tags contienen los términos temáticos. El código computa el match; el LLM/
    usuario ya decidió cuál es el tema (LLM-pilar).
    """
    if not theme_keywords:
        return 0.0
    title = _normalize(item.title)
    desc = _normalize(item.description)
    tags = " ".join(_normalize(t) for t in item.tags)
    bonus = 0.0
    for kw in theme_keywords:
        k = _normalize(kw)
        # DAT-04: umbral bajado de 4→3 para no perder acrónimos institucionales
        # cortos y muy relevantes del catálogo (sgc, pnn, idf, mdt, rio). Para
        # que un término corto no case FRAGMENTOS ("rio" dentro de "prioridad"),
        # los cortos (<4) exigen palabra COMPLETA; los largos mantienen el match
        # por substring flexible (plural/singular).
        if not k or len(k) < 3:
            continue
        stem = k
        if len(k) > 4 and k.endswith("es"):
            stem = k[:-2]
        elif len(k) > 4 and k.endswith("s"):
            stem = k[:-1]
        cands = (k, stem) if stem != k else (k,)
        whole_word = len(k) < 4

        def _hit(text: str, _cands=cands, _ww=whole_word) -> bool:
            if _ww:
                return any(re.search(rf"\b{re.escape(c)}\b", text) for c in _cands)
            return any(c in text for c in _cands)

        if _hit(title):
            bonus += 5.0  # tema en el título = señal fuerte
        elif _hit(desc):
            bonus += 2.0
        elif _hit(tags):
            bonus += 2.0
    return bonus


def _score(  # noqa: C901, PLR0912
    item: HubItem,
    place: str | None = None,
    region_bbox: list[float] | None = None,
    theme_keywords: list[str] | None = None,
) -> float:
    score = 0.0

    # Lugar pedido (señal más fuerte cuando aplica)
    score += _place_match_bonus(item, place)

    # #4: el item trata del TEMA pedido (no solo del lugar).
    score += _theme_match_bonus(item, theme_keywords)

    # E4 (audit 2026-06-13/14): en una búsqueda REGIONAL, penalizar items que no
    # son de la región. Penalización en el ranking, NO descarte. Solo aplica
    # cuando hay region_bbox (region-neutral: sin región activa no penaliza).
    # - extent FUERA de la región (o global/continental) → -4.0
    # - SIN extent (sin metadato geográfico) → -2.0: no debe rankear neutral
    #   junto a los que SÍ están en la región (ej. "Hospitales de Murcia" sin extent).
    if region_bbox:
        if item.extent and len(item.extent) == 4:
            if not bbox_intersecta(item.extent, region_bbox):
                score -= 4.0
        else:
            score -= 2.0

    # Owner institucional
    if item.owner and any(item.owner in v for v in OFFICIAL_OWNERS_CO.values()):
        score += 5.0

    # Source institucional
    if item.source_field and any(
        item.source_field in v for v in OFFICIAL_SOURCES_CO.values()
    ):
        score += 3.0

    # Tipo cargable. FeatureServer gana — son consultables, expoden atributos
    # y simbología nativa. ImageServer también prima (raster es escaso).
    # MapServer se mantiene neutro: hay miles en Colombia y dominaban el
    # top-N sin esto.
    if item.service_type == "FeatureServer":
        score += 2.5
    elif item.service_type == "ImageServer":
        score += 2.0
    elif item.service_type == "MapServer":
        score += 1.0
    if item.type_raw in {"Web Map", "Web Mapping Application", "Dashboard"}:
        score -= 5.0

    # Recencia
    score += _modified_recency_bonus(item.modified)

    # Uso y autoría declarada (hechos de ArcGIS Online). V5 «malla vial Bogotá»: una copia personal
    # reciente (501 vistas, sin créditos) quedaba por encima de la Malla Vial de la Secretaría de
    # Movilidad (60.010 vistas, créditos declarados) solo por la recencia. Escala logarítmica: de
    # 500 a 60.000 vistas son ~1,7 puntos, no 120 veces más.
    if item.views and item.views > 0:
        score += min(4.0, 0.8 * math.log10(1 + item.views))
    if (item.credits or "").strip():
        score += 1.0
    if isinstance(item.completeness, (int, float)):
        score += max(0.0, min(1.0, item.completeness / 100))

    # Tiene URL útil
    if item.service_url:
        score += 1.0

    return score


def count_place_matches(items: list[HubItem], place: str | None) -> int:
    """Cuántos items del listado mencionan el lugar pedido (título/desc/tags)."""
    if not place:
        return 0
    p = _normalize(place)
    if not p or len(p) < 3:
        return 0
    count = 0
    for it in items:
        if (p in _normalize(it.title)
            or p in _normalize(it.description)
            or any(p in _normalize(t) for t in it.tags)):
            count += 1
    return count


def _diversify_by_type(items: list[HubItem], window: int = 12) -> list[HubItem]:
    """Re-intercalar items por tipo de servicio dentro de los primeros ``window``.

    Si el top viene dominado por un tipo (ej. 12 MapServers seguidos),
    barajamos round-robin entre Feature/Map/Image antes de devolver. No
    altera el orden global más allá de ``window`` — solo mejora el primer
    pantallazo que ve el usuario.
    """
    if len(items) <= 2:
        return items

    head = items[:window]
    tail = items[window:]

    by_type: dict[str, list[HubItem]] = {
        "FeatureServer": [],
        "ImageServer": [],
        "MapServer": [],
        "Other": [],
    }
    for it in head:
        bucket = it.service_type if it.service_type in by_type else "Other"
        by_type[bucket].append(it)

    # Round-robin priorizando FeatureServer → ImageServer → MapServer → Other
    order = ["FeatureServer", "ImageServer", "MapServer", "Other"]
    interleaved: list[HubItem] = []
    while any(by_type[k] for k in order):
        for k in order:
            if by_type[k]:
                interleaved.append(by_type[k].pop(0))

    return interleaved + tail


def rank_results(
    items: list[HubItem],
    place: str | None = None,
    region_bbox: list[float] | None = None,
    theme_keywords: list[str] | None = None,
) -> list[HubItem]:
    """Ordenar items por score compuesto + diversificar por tipo en el top.

    1. Cada item recibe ``rank_score``. Si se pasa ``place``, items que lo
       mencionen ganan bonus fuerte (ver ``_place_match_bonus``).
    2. Sort estable DESC por score.
    3. Diversificación round-robin en el top-12 para que el usuario no vea
       12 MapServers seguidos (sesgo del catálogo colombiano).
    """
    for it in items:
        it.rank_score = _score(it, place=place, region_bbox=region_bbox, theme_keywords=theme_keywords)
    sorted_items = sorted(items, key=lambda x: x.rank_score, reverse=True)
    return _diversify_by_type(sorted_items, window=12)
