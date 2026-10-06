"""Sobre qué capa se actúa: UNA sola regla (S1.4 del plan de plataforma).

Había cinco implementaciones y NO coincidían. Simbología e imagery preferían los
datos del turno actual; python_agent prefería la fuente que el estado declaraba
activa. Con una capa activa en el mapa y una consulta nueva en el mismo turno
("trae los lotes y hazles un buffer"), simbología operaba sobre los lotes nuevos
y python_agent sobre la capa vieja del mapa.

La regla única:
  1. la capa OBJETIVO que el usuario nombró (FRT-04, validada contra map_layers);
  2. los datos del turno actual de la BD (`geojson`);
  3. los datos externos cargados (`external_geojson`, incluida la capa activa
     del mapa que `routes/query.py` inyecta ahí);
  4. la capa heredada del turno anterior (`previous_geojson`);
  5. solo si el llamador lo pide: la zona visible del mapa (AOI de imagery).

En F2 estos slots se sustituyen por datasets del workspace; la regla queda.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

Origen = Literal["target", "internal", "external", "previous", "viewport"]


@dataclass(frozen=True)
class CapaEnFoco:
    geojson: dict[str, Any]
    #: Nombre para narrar; `None` si el estado no trae uno (el llamador pone el suyo).
    name: str | None
    origen: Origen
    #: Id de la capa del mapa cuando es la objetivo nombrada.
    layer_id: str | None = None


def id_de_capa(target: Any, state: Mapping[str, Any]) -> str | None:
    """El id de capa del mapa para `target`, validado contra `map_layers`.

    T2.0a: el LLM ve junto a cada capa su dataset del workspace y a veces
    devuelve ESE id (`ds_…`) como capa objetivo; se traduce a la capa que lo
    lleva. Un id que no corresponde a ninguna capa cargada → None.
    """
    # El contexto lista las capas como «[id] "nombre"»: el LLM a veces copia los
    # corchetes (V5 FH.2: '[seleccion]' se descartaba y midió la capa entera).
    target = str(target or "").strip().strip("[]").strip()
    if not target:
        return None
    capas = state.get("map_layers") or {}
    if target in capas:
        return str(target)
    for lyr in ((state.get("map_context") or {}).get("layers") or []):
        if lyr.get("dataset_id") == target and lyr.get("id") in capas:
            return str(lyr["id"])
    return None


def muestra_para_estilo(state: Mapping[str, Any]) -> tuple[dict, int, str] | None:
    """(muestra, total, layer_id) de la capa en foco cuando es demasiado grande para
    hidratarse (H23). SOLO la usa la simbología: los valores de las clases salen
    de una muestra repartida; analizar con ella sería hacerlo sobre una parte.

    Capa en foco = la objetivo nombrada, o si no la activa del mapa.
    """
    capas = state.get("map_layers") or {}
    candidatas = [state.get("target_layer_id")] + [
        lyr.get("id") for lyr in ((state.get("map_context") or {}).get("layers") or [])
        if lyr.get("is_active")
    ]
    for lid in candidatas:
        entrada = capas.get(lid) if lid else None
        if entrada and entrada.get("muestra") and not entrada.get("data"):
            return entrada["muestra"], int(entrada.get("total") or 0), str(lid)
    return None


def _tiene_features(gj: Any) -> bool:
    return bool(isinstance(gj, dict) and (gj.get("features") or []))


def hay_capa_vectorial(state: Mapping[str, Any]) -> bool:
    """¿Hay alguna capa VECTORIAL sobre la que operar? (hecho, no juicio)

    Una sola definición para el nodo router y la arista que enruta (estaban duplicadas y solo
    contaban capas con `dataset_id`: una capa vectorial del mapa sin dataset —un dibujo, un GeoJSON
    subido— hacía decir «no hay una capa cargada» con la capa en pantalla). Un raster no cuenta:
    no se simboliza ni se cruza como vector.
    """
    if state.get("has_external_data") or state.get("geojson"):
        return True
    if _tiene_features(state.get("previous_geojson")):
        return True
    if any(_tiene_features((c or {}).get("data")) for c in (state.get("map_layers") or {}).values()):
        return True
    return any(
        lyr.get("dataset_id") or str(lyr.get("kind") or "vector").startswith("vector")
        for lyr in ((state.get("map_context") or {}).get("layers") or [])
    )


def resolver_capa(
    state: dict[str, Any], *, con_features: bool = False, viewport: bool = False,
) -> CapaEnFoco | None:
    """La capa sobre la que actuar, según la regla del módulo.

    `con_features=True`: descarta capas vacías (un AOI necesita geometría).
    `viewport=True`: si no hay capa, usa la zona visible del mapa como polígono.
    """

    def valida(gj: Any) -> bool:
        return _tiene_features(gj) if con_features else bool(gj)

    target = state.get("target_layer_id")
    capas = state.get("map_layers") or {}
    if target and target in capas:
        entrada = capas[target]
        if valida(entrada.get("data")):
            return CapaEnFoco(entrada["data"], entrada.get("name") or None, "target", target)

    for clave, origen, nombre in (
        ("geojson", "internal", "active_source_name"),
        ("external_geojson", "external", "external_source_name"),
        ("previous_geojson", "previous", "active_source_name"),
    ):
        gj = state.get(clave)
        # La capa heredada solo cuenta si tiene features (mismo criterio que
        # tenía el Smart Router en python_agent): un FeatureCollection vacío
        # del turno anterior no es "la capa que el usuario ve".
        if (_tiene_features(gj) if origen == "previous" else valida(gj)):
            return CapaEnFoco(gj, state.get(nombre) or None, origen)  # type: ignore[arg-type]

    # Sin objetivo nombrado ni datos del turno: la capa ACTIVA del mapa, si su copia viajó.
    # V5 FH.5: «¿cuántos lotes hay?» con la capa filtrada a mano en una sesión sin estado previo →
    # analyze_layer respondía «no hay datos cargados» con la capa en pantalla, y el agente se rendía.
    activa = next((c.get("id") for c in ((state.get("map_context") or {}).get("layers") or [])
                   if isinstance(c, dict) and c.get("is_active")), None)
    if activa and activa in capas and valida(capas[activa].get("data")):
        return CapaEnFoco(capas[activa]["data"], capas[activa].get("name") or None, "target", activa)

    if viewport:
        bbox = (((state.get("map_context") or {}).get("viewport") or {}).get("bbox"))
        if bbox and len(bbox) == 4:
            poligono = {"type": "Polygon", "coordinates": [[
                [bbox[0], bbox[1]], [bbox[2], bbox[1]], [bbox[2], bbox[3]],
                [bbox[0], bbox[3]], [bbox[0], bbox[1]],
            ]]}
            return CapaEnFoco(poligono, None, "viewport")
    return None


def capa_del_ultimo_dibujo(working: dict) -> str | None:
    """FH.15 (bench de deixis): la referencia `dibujo` = la capa que el usuario DIBUJÓ más
    recientemente (la última de su tipo en el orden del mapa). «lo que dibujé» terminaba en
    `activa` o `seleccion` porque no había cómo nombrarla como a `punto`."""
    capas = (working.get("map_context") or {}).get("layers") or []
    dibujos = [c for c in capas if ((c.get("origin") or {}).get("capability") == "user.sketch") and c.get("id")]
    return str(dibujos[-1]["id"]) if dibujos else None


def copia_de_capa_actualizada(working: dict, ds: str, geojson: dict) -> dict | None:
    """`map_layers` con la copia de la capa del mapa al día (V5, auditoría F4).

    La capa sigue siendo el objetivo y la resolución de capa lee su copia del mapa ANTES que
    `geojson`: sin esto, tras «añade el área» la simbología veía la copia vieja, sin `area_m2`, y
    coloreaba de un solo color («el campo no es numérico»). La copia es la vista del mapa: con su
    filtro, si lo tiene.
    """
    from geo_copilot.platform.seleccion import filtrar_capa, filtro_de_dataset

    capa = capa_de_dataset(working, ds)
    capas = working.get("map_layers") or {}
    if capa is None or capa not in capas:
        return None
    filtro = filtro_de_dataset(ds, working)
    datos = filtrar_capa(geojson, filtro) if filtro else geojson
    return {**capas, capa: {**capas[capa], "data": datos}}


def capa_de_dataset(working: Mapping[str, Any], ds: str) -> str | None:
    """La capa del mapa que lleva el dataset `ds` del workspace."""
    return next((c.get("id") for c in ((working.get("map_context") or {}).get("layers") or [])
                 if c.get("dataset_id") == ds), None)
