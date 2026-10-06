"""
Proxy de teselas de imagery ArcGIS (MAP-IMAGERY-PROXY).

Los servidores ArcGIS externos (p. ej. IGAC `mapas2.igac.gov.co`) NO envían
cabeceras CORS, así que MapLibre GL no puede cargar sus teselas raster
directamente: el `fetch` que WebGL necesita para la textura falla con
"Failed to fetch (0)". Este endpoint hace el fetch **server-side** (donde CORS
no aplica), valida el host contra la allowlist anti-SSRF
(`settings.allowed_domains`, la misma que usan los conectores de Discovery) y
reemite el PNG.

    GET /api/v1/proxy/imagery
        ?service=<url base ImageServer/MapServer>
        &endpoint=exportImage|export
        &bbox=<xmin,ymin,xmax,ymax en EPSG:3857>

El frontend deja `{bbox-epsg-3857}` como token LITERAL en el urlTemplate del
raster source para que MapLibre lo sustituya por tesela; el resto de parámetros
de `/exportImage` los fija este backend (el cliente no puede alterarlos).
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from geo_copilot.api.auth import require_principal
from geo_copilot.api.limiter import limiter
from geo_copilot.core.config import Settings, get_settings
from geo_copilot.core.logging import get_logger
from geo_copilot.core.security import URLValidator

logger = get_logger(__name__)

router = APIRouter(
    prefix="/proxy",
    tags=["Proxy"],
    dependencies=[Depends(require_principal)],
)

# Endpoints ArcGIS que sabemos reemitir. Fija el conjunto para no reenviar
# rutas arbitrarias del servicio.
_ALLOWED_ENDPOINTS = {"exportImage", "export"}

# bbox proyectado EPSG:3857: exactamente cuatro números (float, notación
# científica admitida) separados por coma. Nada más — evita que el cliente
# cuele parámetros extra dentro del valor.
_BBOX_RE = re.compile(
    r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?"
    r"(?:,-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?){3}$"
)

# Parámetros fijos de /exportImage — mismos valores que maplibreImagery
# EXPORT_QUERY, pero fijados server-side para que el cliente no los altere.
_EXPORT_PARAMS = {
    "bboxSR": "3857",
    "imageSR": "3857",
    "size": "256,256",
    "format": "png",
    "transparent": "true",
    "f": "image",
}

_FETCH_TIMEOUT = 15.0
# Una tesela PNG 256x256 jamás pesa esto; corta respuestas anómalas.
_MAX_BYTES = 8 * 1024 * 1024

# R0.10 (AUD-07): tipos que este proxy reemite. Formatos RÁSTER exclusivamente.
# NO añadir `image/svg+xml` ni ningún tipo que el navegador pueda ejecutar:
# se sirven desde el origen de la aplicación.
_ALLOWED_IMAGE_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
})

# SEC-05: cota de conexiones de los clients del proxy. Sin límite, un pico de
# teselas (rate-limit 1200/min) con upstreams lentos (timeout 180s en las
# teselas NDVI) podía acumular sockets/FDs abiertos. `max_connections` acota el
# pool por client; el rate-limit por minuto es la cota primaria de tasa.
_PROXY_LIMITS = httpx.Limits(max_connections=16, max_keepalive_connections=8)


@router.get("/imagery")
@limiter.limit("1200/minute")
async def proxy_imagery(
    request: Request,
    service: str = Query(..., description="URL base del ImageServer/MapServer"),
    endpoint: str = Query("exportImage", description="exportImage | export"),
    bbox: str = Query(..., description="bbox EPSG:3857 xmin,ymin,xmax,ymax"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Reemite una tesela raster de un servicio ArcGIS allowlisted con CORS."""
    if endpoint not in _ALLOWED_ENDPOINTS:
        raise HTTPException(status_code=400, detail="endpoint no permitido")
    if not _BBOX_RE.match(bbox):
        raise HTTPException(status_code=400, detail="bbox inválido")

    base = service.rstrip("/")
    # SSRF: valida esquema/allowlist y resuelve el host UNA vez, quedándose una
    # IP validada. Fijarla cierra el TOCTOU: sin esto, httpx re-resolvía al
    # conectar y podía dar una IP privada (DNS rebinding) tras pasar el chequeo.
    try:
        safe_ip, hostname = URLValidator.resolve_validated_ip(
            base, settings.allowed_domains)
    except ValueError as exc:
        logger.warning("proxy_imagery bloqueó %s: %s", base, exc)
        raise HTTPException(status_code=400, detail="servicio no permitido") from exc

    upstream = f"{base}/{endpoint}"
    params = {"bbox": bbox, **_EXPORT_PARAMS}
    # Conectar a la IP validada, con Host + SNI = hostname original (TLS/cert
    # siguen validando contra el nombre, no contra la IP) — #25.
    _p = urlparse(upstream)
    _netloc_ip = safe_ip + (f":{_p.port}" if _p.port else "")
    pinned_url = urlunparse(_p._replace(netloc=_netloc_ip))
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, limits=_PROXY_LIMITS) as client:
            resp = await client.get(
                pinned_url, params=params,
                headers={"Host": hostname},
                extensions={"sni_hostname": hostname})
    except httpx.HTTPError as exc:
        logger.warning("proxy_imagery upstream error %s: %s", upstream, exc)
        raise HTTPException(status_code=502, detail="upstream no disponible") from exc

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"upstream {resp.status_code}")

    body = resp.content
    if len(body) > _MAX_BYTES:
        raise HTTPException(status_code=502, detail="tesela demasiado grande")
    # ArcGIS devuelve un JSON de error incluso con f=image si algo falla; solo
    # reemitimos bytes de imagen.
    content_type = resp.headers.get("content-type", "")
    # R0.10 (auditoría 2026-07-26, AUD-07): allowlist CERRADA de tipos, no
    # `startswith("image/")`. `image/svg+xml` empieza por "image/" y es
    # CONTENIDO ACTIVO: admite <script>. Reemitido desde el origen de la
    # aplicación —al que el BFF de nginx ya adjunta la API key— un SVG servido
    # por cualquier host de `allowed_domains` (match por sufijo, 30+ dominios)
    # se ejecutaba con nuestro origen y podía llamar a /api/v1/* autenticado.
    base_type = content_type.split(";", 1)[0].strip().lower()
    if base_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=502,
            detail="upstream no devolvió una imagen de un tipo permitido",
        )

    # La app ya monta CORSMiddleware; el navegador pide estas teselas al mismo
    # origen (/api/v1), así que basta con reemitir la imagen cacheable.
    return Response(
        content=body,
        media_type=base_type,
        headers={
            "Cache-Control": "public, max-age=3600",
            # R0.10: aunque el tipo ya está en allowlist, impedimos que el
            # navegador re-adivine el tipo y que se ejecute nada si algún día
            # se ensancha la lista por error.
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


# Params de /identify que reemitimos (allowlist). El resto se descarta —
# el cliente no puede colar rutas ni parámetros arbitrarios. `f` se fija a json.
_IDENTIFY_PARAMS = {
    "geometry", "geometryType", "sr", "tolerance", "mapExtent",
    "imageDisplay", "layers", "returnGeometry", "returnCatalogItems",
}
_MAX_JSON_BYTES = 2 * 1024 * 1024


@router.get("/imagery-identify")
@limiter.limit("600/minute")
async def proxy_imagery_identify(
    request: Request,
    service: str = Query(..., description="URL base del ImageServer/MapServer"),
    settings: Settings = Depends(get_settings),
) -> Response:
    """FRT-03: reemite un ``/identify`` de un servicio ArcGIS allowlisted.

    Espeja el proxy de teselas (SSRF + IP-pinning), pero devuelve el JSON de
    atributos/valor-de-píxel para el popup de capas raster. Los params de
    identify (geometry/tolerance/mapExtent/…) los pone el cliente; sólo se
    reemiten los de la allowlist y ``f=json`` se fija server-side.
    """
    base = service.rstrip("/")
    try:
        safe_ip, hostname = URLValidator.resolve_validated_ip(
            base, settings.allowed_domains)
    except ValueError as exc:
        logger.warning("proxy_imagery_identify bloqueó %s: %s", base, exc)
        raise HTTPException(status_code=400, detail="servicio no permitido") from exc

    # Sólo los params de la allowlist (el resto se ignora); f forzado a json.
    params = {
        k: v for k, v in request.query_params.items() if k in _IDENTIFY_PARAMS
    }
    params["f"] = "json"

    upstream = f"{base}/identify"
    _p = urlparse(upstream)
    _netloc_ip = safe_ip + (f":{_p.port}" if _p.port else "")
    pinned_url = urlunparse(_p._replace(netloc=_netloc_ip))
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT, limits=_PROXY_LIMITS) as client:
            resp = await client.get(
                pinned_url, params=params,
                headers={"Host": hostname},
                extensions={"sni_hostname": hostname})
    except httpx.HTTPError as exc:
        logger.warning("proxy_imagery_identify upstream error %s: %s", upstream, exc)
        raise HTTPException(status_code=502, detail="upstream no disponible") from exc

    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"upstream {resp.status_code}")
    body = resp.content
    if len(body) > _MAX_JSON_BYTES:
        raise HTTPException(status_code=502, detail="respuesta demasiado grande")

    return Response(
        content=body,
        media_type="application/json",
        headers={"Cache-Control": "public, max-age=60"},
    )


# ---------------------------------------------------------------------------
# S3.5: proxy GENÉRICO de teselas de los servidores MCP.
#
#     GET /api/v1/proxy/mcp/{server_id}/{path}
#
# Reemite hacia el servidor registrado inyectando SU credencial (que nunca
# llega al navegador), solo bajo los prefijos que el servidor declaró en
# `tiles.prefixes` del YAML (imagery-mcp incluido: el núcleo no tiene rutas propias).
# ---------------------------------------------------------------------------
_RUTA_MCP_RE = re.compile(r"^[A-Za-z0-9_\-\./]{1,300}$")
_QUERY_MCP_RE = re.compile(r"^[A-Za-z0-9_\-\.,=&%]{0,200}$")
_TIPOS_TESELA = ("image/", "application/vnd.mapbox-vector-tile", "application/x-protobuf")


def _cliente_mcp_proxy(cfg: object = None) -> httpx.AsyncClient:
    """Cliente HTTP del proxy (los tests lo sustituyen por un transporte simulado)."""
    from geo_copilot.platform.conexiones.red import transporte_para

    # F6: las teselas de una conexión de organización van a su IP pública validada y fijada
    return httpx.AsyncClient(timeout=180.0, limits=_PROXY_LIMITS, follow_redirects=False,
                             transport=transporte_para(cfg))


@router.get("/mcp/{server_id}/{path:path}")
@limiter.limit("1200/minute")
async def proxy_mcp_tiles(request: Request, server_id: str, path: str) -> Response:
    from geo_copilot.platform.mcp.hub import hub_actual

    hub = hub_actual()
    con = hub.conexiones.get(server_id) if hub is not None else None
    if con is None:
        raise HTTPException(404, "servidor no registrado")
    ruta = "/" + path
    if (not _RUTA_MCP_RE.match(path) or ".." in path.split("/")
            or not any(ruta.startswith(p) for p in con.cfg.tiles.prefixes)):
        raise HTTPException(404, "ruta no permitida para este servidor")
    query = request.url.query
    from urllib.parse import unquote

    # Se valida DECODIFICADO: codificado, `%3Cscript%3E` pasaba la regex.
    if not _QUERY_MCP_RE.match(query) or not _QUERY_MCP_RE.match(unquote(query)):
        raise HTTPException(400, "parámetros de tesela inválidos")

    base = con.cfg.url.rstrip("/").removesuffix("/mcp")
    url = f"{base}{ruta}" + (f"?{query}" if query else "")
    headers = {}
    if con.cfg.auth.type == "bearer":
        secreto = con.cfg.auth.resolver()
        if not secreto:
            raise HTTPException(503, "servidor sin credencial configurada")
        headers["Authorization"] = f"Bearer {secreto}"
    try:
        async with _cliente_mcp_proxy(con.cfg) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning(f"[proxy mcp/{server_id}] upstream falló: {type(exc).__name__}")
        raise HTTPException(502, "Tesela no disponible") from exc

    if resp.status_code == 204:
        return Response(status_code=204)
    if resp.status_code != 200:
        raise HTTPException(resp.status_code if resp.status_code < 500 else 502, "Tesela no disponible")
    tipo = resp.headers.get("content-type", "")
    if not tipo.startswith(_TIPOS_TESELA) or len(resp.content) > _MAX_BYTES:
        raise HTTPException(502, "Respuesta de tesela anómala")
    return Response(content=resp.content, media_type=tipo.split(";")[0],
                    headers={"Cache-Control": resp.headers.get("cache-control", "public, max-age=3600")})
