"""Cargar una URL EXTERNA en el nodo de datos: imágenes (al servicio de imágenes), un FeatureServer
con varias capas (se ofrecen para elegir) o una capa (se trae completa), y los mensajes del nodo.

Salió de `nodes/data_agent.py` (F4 del plan de calidad: data_agent.py tenía 636 líneas), tal cual.
"""

from __future__ import annotations

import re

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.orchestrator.nodes.data_agent")


def _msg(content: str, *, success: bool, data: dict | None = None) -> dict:
    """Atajo para construir un mensaje del agente."""
    return {"agent": "data_agent", "content": content, "data": data, "success": success}


def _is_imagery_url(url: str) -> str | None:
    """Devolver 'MapServer' / 'ImageServer' si la URL apunta a uno de esos.

    Heurística simple por URL: MapServer y ImageServer son servicios de
    tipo imagen — no se consultan vía /query (o no soportan GeoJSON).
    El nodo los enruta directo a la capa imagery del mapa.
    """
    u = url.lower()
    if "imageserver" in u:
        return "ImageServer"
    if "mapserver" in u:
        return "MapServer"
    return None


async def _build_imagery_state(
    url: str, service_name: str, service_kind: str
) -> dict:
    """State con un descriptor imagery listo para el frontend (lo arma el servidor MCP de ArcGIS)."""
    from geo_copilot.agents.data_agent import servicio_arcgis

    try:
        d = await servicio_arcgis.describir(url)
    except servicio_arcgis.ArcGISNoDisponible as exc:
        msg = f"No pude abrir el servicio de imagen {service_name}: {exc}"
        return {"current_agent": "data_agent", "error": msg, "final_response": msg,
                "messages": [_msg(msg, success=False)]}
    caja = d.get("extent_4326")
    extent = {"xmin": caja[0], "ymin": caja[1], "xmax": caja[2], "ymax": caja[3]} if caja else None
    descriptor = dict(d.get("imagen") or {"type": "imagery", "service_url": url.rstrip("/"), "export_url": None})
    descriptor["extent"] = extent
    descriptor["service_kind"] = service_kind
    descriptor["name"] = service_name

    logger.info(
        f"[DataAgent] {service_kind} → imagery layer: {service_name} ({url})"
    )
    return {
        "current_agent": "data_agent",
        "external_imagery": descriptor,
        "external_source_url": url,
        "external_source_name": service_name,
        "has_external_data": True,
        # Limpiar found_services: ya no aplica una vez que el usuario
        # eligió uno. Sin esto, el responder re-incluye la lista y el chat
        # muestra las 10 cards otra vez aunque ya cargamos la imagen.
        "found_services": None,
        "final_response": (
            f"Cargué **{service_name}** al mapa como capa de imagen "
            f"({service_kind}). Es un servicio renderizado en servidor: "
            f"se ve sobre el mapa pero no expone features individuales."
        ),
        "messages": [_msg(
            f"{service_kind} cargado como imagery: {service_name}",
            success=True,
            data={
                "source": url,
                "service_name": service_name,
                "service_kind": service_kind,
                "imagery": True,
                "external": True,
            },
        )],
    }


async def _traer_capa(url: str) -> tuple[dict | None, dict, str]:
    """(GeoJSON, hechos, error) de una capa ArcGIS vía el servidor MCP de ArcGIS."""
    from geo_copilot.agents.data_agent import servicio_arcgis

    try:
        geojson, hechos = await servicio_arcgis.consultar_capa(url)  # completa (max_external_features)
    except servicio_arcgis.ArcGISNoDisponible as exc:
        return None, {}, str(exc)
    if not geojson.get("features"):
        return None, hechos, "el servicio no devolvió elementos con geometría"
    return geojson, hechos, ""


def _resumen(n: int, hechos: dict) -> str:
    """«N features» y, si no vino todo, cuántos hay: una muestra no se presenta como el total."""
    total = hechos.get("total_en_servicio")
    if hechos.get("completo") is False and total:
        return f"{n} features (MUESTRA: el servicio tiene {total})"
    return f"{n} features"


_CONECTORES = {"de", "del", "la", "las", "el", "los", "y", "en"}


def _palabras(texto: str) -> set[str]:
    """Las palabras de un nombre, sin tildes, mayúsculas, signos ni conectores: «Malla Vial Integral de
    Bogotá D.C.» y «Malla Vial Integral Bogota D_C» dicen lo mismo."""
    import unicodedata

    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()
    return {p for p in re.split(r"[^a-z0-9]+", re.sub(r"(?<=[a-z])[._](?=[a-z])", "", sin_tildes)) if p} - _CONECTORES


def _ofrecer_capas(titulo: str, url: str, capas: list[dict], *, motivo: str = "") -> dict:
    """No se carga nada: se ofrecen las capas con geometría para que elija quien pidió."""
    lista = "; ".join(f"{c.get('id')}: «{c.get('nombre')}» ({c.get('tipo_geometria')})" for c in capas[:40])
    texto = ((f"{motivo[:1].upper()}{motivo[1:]}. " if motivo else "")
             + f"El servicio «{titulo}» tiene {len(capas)} capa(s) con geometría; no se cargó ninguna: "
             f"elige cuál con su id. Capas: {lista}")
    return {
        "current_agent": "data_agent",
        "service_layers": capas,
        "service_layers_of": {"name": titulo, "url": url, "motivo": motivo},
        "final_response": texto,
        "messages": [_msg(texto, success=True, data={"source": url, "layers": capas})],
    }


def _es_raiz_feature_server(url: str) -> bool:
    return url.rstrip("/").lower().endswith("/featureserver")


async def _capas_del_servicio(url: str) -> list[dict] | None:
    """Las capas CON geometría de un FeatureServer (None si no se pudo describir: se dice al cargar)."""
    from geo_copilot.agents.data_agent import servicio_arcgis

    try:
        hechos = await servicio_arcgis.describir(url)
    except servicio_arcgis.ArcGISNoDisponible as exc:
        logger.warning(f"[DataAgent] no se pudo describir {url}: {exc}")
        return None
    return [c for c in hechos.get("capas") or [] if isinstance(c, dict) and c.get("tipo_geometria")]


async def _handle_external_url(url: str, nombre: str | None = None) -> dict:
    """Fetch directo de un servicio dado por URL."""
    # MapServer / ImageServer → capa imagery (no GeoJSON).
    kind = _is_imagery_url(url)
    if kind:
        name = nombre or url.rstrip("/").split("/")[-1] or "Servicio externo"
        return await _build_imagery_state(url, name, kind)

    # Rama arcgis-busqueda (V5): un FeatureServer sin capa se cargaba siempre por su capa 0 — en la
    # cartografía de Cota eran PUNTOS, y se narró «las vías de Bogotá». Con varias capas, elige quien
    # pidió (el LLM, viendo sus nombres y geometrías); con una sola, esa.
    if _es_raiz_feature_server(url):
        capas = await _capas_del_servicio(url)
        if capas and len(capas) > 1:
            return _ofrecer_capas(nombre or url.rstrip("/").split("/")[-2], url, capas)
        if capas and len(capas) == 1:
            url = f"{url.rstrip('/')}/{capas[0].get('id')}"

    logger.debug(f"[DataAgent] Fetching from: {url}")
    geojson, hechos, error_msg = await _traer_capa(url)
    raiz, _, ultimo = url.rstrip("/").rpartition("/")
    if geojson is None and "no tiene geometría" in error_msg and ultimo.isdigit() and _es_raiz_feature_server(raiz):
        # V5: la capa indicada era un GRUPO de capas (RUNAP «sin solapa»); el error decía «describe el
        # servicio», pero el bucle no tiene esa herramienta: se le dan las capas que sí tienen geometría
        capas = await _capas_del_servicio(raiz)
        if capas:
            return _ofrecer_capas(nombre or raiz.split("/")[-2], raiz, capas,
                                  motivo=f"la capa {ultimo} no tiene geometría (es un grupo o una tabla)")
    if geojson is not None:
        feature_count = len(geojson.get("features", []))
        logger.info(f"[DataAgent] Data fetched: {feature_count} features")
        props = next((f.get("properties") for f in geojson.get("features", []) if isinstance(f, dict)), None) or {}
        capa = str(hechos.get("capa") or "")
        # el nombre de la FUENTE (antes «Servicio externo», y la capa acababa llamándose como la pregunta)
        fuente = nombre or capa or "Servicio externo"
        if nombre and capa and not _palabras(capa) <= _palabras(nombre):
            fuente = f"{nombre} · {capa}"
        return {
            "current_agent": "data_agent",
            "raw_data": geojson.get("features", []),
            "geojson": geojson,
            "external_geojson": geojson,
            "external_source_url": url,
            "external_source_name": fuente,
            "layer_name": fuente,  # en el mapa y el workspace, con el nombre de la fuente (no la pregunta)
            "has_external_data": True,
            # para verificar que lo cargado es lo pedido (V5: puntos de Cota narrados como vías de Bogotá)
            "external_layer_facts": {
                "servicio": nombre, "capa": capa, "geometria": hechos.get("tipo_geometria"),
                "campos": [k for k in props if not str(k).lower().startswith(("shape", "objectid", "globalid"))][:15],
                "total_en_servicio": hechos.get("total_en_servicio"),
            },
            "messages": [_msg(
                f"Datos externos obtenidos de «{fuente}»: {_resumen(feature_count, hechos)}",
                success=True,
                data={"source": url, "feature_count": feature_count,
                      "total_en_servicio": hechos.get("total_en_servicio"), "external": True},
            )],
        }

    logger.error(f"[DataAgent] Fetch error: {error_msg}")
    return {
        "current_agent": "data_agent",
        "error": error_msg,
        "final_response": f"Error al obtener datos: {error_msg}",
        "messages": [_msg(f"Error: {error_msg}", success=False)],
    }
