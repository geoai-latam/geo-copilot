"""Cliente HTTP con protección SSRF por IP-pinning: vive en el kit (`geo_mcp_kit.red`), compartido con
ogc-mcp. Aquí solo se fija la identificación de este servidor."""

from __future__ import annotations

import httpx
from geo_mcp_kit.red import PUERTOS_SEGUROS, REDES_BLOQUEADAS, UrlNoPermitida, ip_validada
from geo_mcp_kit.red import cliente as _cliente

__all__ = ["PUERTOS_SEGUROS", "REDES_BLOQUEADAS", "UrlNoPermitida", "cliente", "ip_validada"]


def cliente(url: str, *, dominios: list[str] | None = None, timeout: float = 20.0) -> httpx.Client:
    """`httpx.Client` fijado a la IP validada del host de `url` (lanza `UrlNoPermitida`)."""
    return _cliente(url, dominios=dominios, timeout=timeout, user_agent="geo-copilot-arcgis-mcp/1")
