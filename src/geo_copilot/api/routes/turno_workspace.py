"""El WORKSPACE del turno: materializar el resultado geográfico como dataset y traer de vuelta las
capas del workspace que el cliente manda por su id (completas o, si son grandes, una muestra).

Salió de `api/routes/query.py` (F4 del plan de calidad), tal cual.
"""

from __future__ import annotations

from geo_copilot.core.config import get_settings
from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.api.routes.query")


async def _materializar_resultado(
    session_id: str, query: str, result: dict, *, ya_en_workspace: dict[str, dict] | None = None,
) -> dict | None:
    """Guarda el GeoJSON del turno como dataset del workspace; devuelve su LayerRef.

    Solo capas con features. El GeoJSON del grafo viaja en EPSG:4326 por
    contrato (conectores con `to_crs(4326)`, SQL con `ST_Transform(…, 4326)`).

    Si el turno devuelve la MISMA capa que llegó del workspace (un re-estilo
    no cambia la geometría), se reutiliza ese dataset: sin duplicados por
    turno ni ids nuevos para la misma capa.
    """
    geojson = result.get("geojson")
    if not (isinstance(geojson, dict) and geojson.get("features")):
        return None
    from geo_copilot.api.dependencies import get_app_state

    store = get_app_state().dataset_store
    if store is None:
        return None
    huella = _huella(geojson)
    for dataset_id, fc in (ya_en_workspace or {}).items():
        if _huella(fc) == huella:
            try:
                ref_previa = await store.get(session_id, dataset_id)
            except Exception:  # noqa: BLE001 — sin catálogo, se materializa como siempre
                ref_previa = None
            if ref_previa is not None:
                datos: dict | None = ref_previa.model_dump(mode="json")
                return datos
    from datetime import UTC, datetime

    from geo_copilot.platform.contracts import Provenance
    from geo_copilot.platform.workspace import WorkspaceError

    nombre = str(result.get("layer_name") or query[:60] or "Resultado")
    try:
        ref = await store.ingest_features(
            session_id, nombre, geojson, crs="EPSG:4326",
            provenance=Provenance(
                capability=f"core.{result.get('intent') or 'consulta'}",
                produced_at=datetime.now(UTC),
                sql=result.get("sql") or None,
                code=result.get("python_code") or None,
                arguments={"query": query[:500]},
            ),
        )
    except WorkspaceError as exc:
        logger.warning(f"[Query] no se materializó el resultado en el workspace: {exc}")
        return None
    except Exception:  # el workspace es aditivo: un fallo inesperado se registra y la respuesta sigue
        logger.warning("[Query] fallo inesperado materializando en el workspace", exc_info=True)
        return None
    datos = ref.model_dump(mode="json")
    return datos


async def _geojson_del_workspace(session_id: str, dataset_id: str) -> dict | None:
    """El dataset del workspace de la sesión como FeatureCollection, o None.

    None si no hay workspace, si el id no es de esta sesión o si venció: el
    turno sigue con lo que tenga (metadatos), como con una capa sin datos.
    """
    from geo_copilot.api.dependencies import get_app_state
    from geo_copilot.platform.workspace import WorkspaceError

    store = get_app_state().dataset_store
    if store is None:
        return None
    tope = get_settings().max_geojson_features
    try:
        ref = await store.get(session_id, dataset_id)
        if ref is None:
            logger.info(f"[Query] dataset {dataset_id!r} no está en el workspace de la sesión")
            return None
        if (ref.feature_count or 0) > tope:
            # Truncarla daría un análisis sobre una parte sin decirlo. Sin data,
            # la capa queda con metadatos y el SQL la usa entera en el workspace.
            logger.info(
                f"[Query] dataset {dataset_id!r} ({ref.feature_count} features) supera "
                f"{tope}: se opera en el workspace, no se hidrata"
            )
            return None
        datos: dict | None = await store.to_geojson(session_id, dataset_id, limit=tope)
        return datos
    except WorkspaceError as exc:
        logger.info(f"[Query] dataset {dataset_id!r} no disponible en el workspace: {exc}")
        return None
    except Exception:  # el workspace es aditivo: sin él, el turno sigue con metadatos
        logger.warning("[Query] fallo leyendo el workspace", exc_info=True)
        return None


def _huella(fc: dict) -> tuple:
    """Identidad barata de un FeatureCollection: cuántas y las geometrías de los extremos."""
    import json

    feats = fc.get("features") or []
    if not feats:
        return (0,)
    extremos = [feats[0].get("geometry"), feats[-1].get("geometry")]
    return (len(feats), json.dumps(extremos, sort_keys=True, default=str))


async def _muestra_para_estilo(session_id: str, dataset_id: str) -> tuple[dict, int] | None:
    """(muestra, total) de un dataset grande del workspace, SOLO para diseñar su estilo.

    H23 (V5 F2): "colorea esos puntos por estado" sobre 41.033 puntos: la capa no
    se hidrata (analizar una parte truncada sería mentir) y la simbología se
    quedaba sin datos para calcular las clases. Los valores de las clases son
    propiedad de los datos: una muestra repartida alcanza, y el estilo se aplica
    a TODAS las teselas.
    """
    from geo_copilot.api.dependencies import get_app_state

    store = get_app_state().dataset_store
    if store is None:
        return None
    try:
        ref = await store.get(session_id, dataset_id)
        if ref is None:
            return None
        fc = await store.to_geojson(
            session_id, dataset_id, limit=get_settings().workspace_inline_max_features, muestra=True,
        )
    except Exception:  # sin muestra la simbología dirá que no hay datos (honesto)
        logger.warning("[Query] no se pudo muestrear el dataset para estilo", exc_info=True)
        return None
    return fc, int(ref.feature_count or 0)
