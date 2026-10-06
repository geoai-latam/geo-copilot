"""La lectura de capas REMOTAS (descarga con tope de tamaño) que usa la ingesta.

Salió de `store.py` (F4 del plan de calidad: store.py tenía 904 líneas), tal cual.
"""

from __future__ import annotations

import json

import httpx

from geo_copilot.platform.workspace.comun import WorkspaceError, srid_de


async def _descargar(uri: str, *, max_bytes: int) -> bytes:
    from geo_copilot.core.security.safe_http import pinned_client

    trozos: list[bytes] = []
    total = 0
    try:
        async with pinned_client(uri, timeout=120.0) as client, client.stream("GET", uri) as r:
            r.raise_for_status()
            async for trozo in r.aiter_bytes():
                total += len(trozo)
                if total > max_bytes:
                    raise WorkspaceError(
                        f"el recurso supera {max_bytes // (1024 * 1024)} MB; "
                        "pide un recorte (bbox/filtro) al origen"
                    )
                trozos.append(trozo)
    except ValueError as exc:  # la URL no pasó la validación SSRF
        raise WorkspaceError(f"URL no permitida: {exc}") from exc
    except httpx.HTTPError as exc:
        raise WorkspaceError(f"no se pudo descargar el recurso: {exc}") from exc
    return b"".join(trozos)


def leer_remoto(contenido: bytes, *, format: str, crs_declarado: str) -> dict:
    """Bytes de un feature_ref → FeatureCollection en el CRS declarado."""
    if format == "geojson":
        fc = json.loads(contenido)
        if fc.get("type") != "FeatureCollection":
            raise WorkspaceError("el GeoJSON remoto no es un FeatureCollection")
        return dict(fc)
    if format not in ("geoparquet", "flatgeobuf"):
        raise WorkspaceError(f"formato remoto no soportado: {format!r}")

    import io

    import geopandas as gpd

    try:
        gdf = (gpd.read_parquet(io.BytesIO(contenido)) if format == "geoparquet"
               else gpd.read_file(io.BytesIO(contenido)))
    except Exception as exc:
        raise WorkspaceError(f"no se pudo leer el {format}: {type(exc).__name__}") from exc
    if gdf.crs is not None:
        declarado = srid_de(crs_declarado)
        propio = gdf.crs.to_epsg()
        if propio is not None and propio != declarado:
            raise WorkspaceError(
                f"el archivo declara EPSG:{propio} y el productor dijo {crs_declarado}: "
                "no se elige uno a ciegas"
            )
    return dict(json.loads(gdf.to_json(drop_id=True)))
