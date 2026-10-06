"""
Rate limiter centralizado para la API.

Usar este módulo para evitar duplicación del limiter en múltiples archivos.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from geo_copilot.core.config import get_settings


def client_ip_key(request: Request) -> str:
    """IP del cliente para el rate-limit.

    S7: ``get_remote_address`` usa la IP del socket (la del proxy/LB tras
    un reverse proxy), de modo que todos los clientes comparten un único
    bucket. Cuando ``trust_proxy_headers`` está activo (la app corre detrás
    de un proxy de confianza) se usa ``X-Forwarded-For``. Por defecto
    desactivado para que un cliente directo no pueda spoofear su IP con ese header.

    F7 (auditoría): se toma la ÚLTIMA entrada, la que escribe nuestro proxy (nginx la fija a
    ``$remote_addr``). Las anteriores las pone el cliente: con la primera, cada petición con un
    ``X-Forwarded-For`` inventado caía en un bucket nuevo y el límite no se alcanzaba nunca.
    """
    if get_settings().trust_proxy_headers:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            ultima = xff.split(",")[-1].strip()
            if ultima:
                return ultima
    return get_remote_address(request)


def clave_de_limite(request: Request) -> str:
    """Quién gasta el límite: el principal autenticado; la IP, solo sin él.

    F7 (auditoría): en las rutas autenticadas el bucket es de la persona (organización + sub),
    no de la IP: rotar cabeceras no da cuota nueva y una oficina tras un NAT no comparte uno
    solo. `require_principal` (dependencia de la ruta) ya lo fijó cuando slowapi evalúa el
    límite. El principal `dev` (sin autenticación) es el mismo para todos: ahí, la IP.

    Con la API key compartida (``servicio:api-key``, una sola org) TODAS las integraciones que la
    usan son un mismo principal y comparten un bucket: el límite es de quien gasta, y quien
    necesite cupo propio necesita identidad propia (token OIDC). Lo mismo una prueba de carga:
    un token por usuario simulado, no cabeceras inventadas.
    """
    from geo_copilot.platform.identidad.principal import principal_actual

    principal = principal_actual()
    if principal is not None and principal.via != "dev":
        return f"principal:{principal.org_id}:{principal.sub}"
    return client_ip_key(request)


def _almacen_de_limites() -> str:
    """F7 (S7.1): con varios workers, los contadores en memoria son por proceso (N workers = N
    veces el límite). En producción, en Redis; `limits` conecta en perezoso y, sin Redis, el
    arranque ya aborta antes (api/estado.py)."""
    s = get_settings()
    from geo_copilot.api.auth import is_production_environment

    if str(getattr(s, "session_backend", "")).lower() == "redis" or is_production_environment(s):
        return str(s.redis_url)
    return "memory://"


# Limiter singleton - usar en app.py y en los routers
limiter = Limiter(key_func=clave_de_limite, storage_uri=_almacen_de_limites())
