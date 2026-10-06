"""Configuración del servicio imagery-mcp (env-first, sin dependencias de la app).

Claves API (autenticación del MCP): env ``IMAGERY_MCP_KEYS`` con JSON:
    [{"name": "geo-copilot-app", "key": "<secreto>",
      "scopes": ["imagery:read", "imagery:compute"], "rate_limit_per_min": 60}]
Las claves llegan en claro por el canal de secretos del despliegue (env/compose
secret) y se guardan en memoria SOLO como sha256.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _fix_proj_on_windows() -> None:
    """Windows dev: el proj.db de PostgreSQL (PROJ viejo) pisa al de rasterio.

    Fijar PROJ_DATA/PROJ_LIB al bundle del wheel ANTES de usar rasterio.warp.
    En el contenedor Linux no aplica (no hay otra instalación de PROJ).
    """
    if os.name != "nt":
        return
    try:
        import rasterio
        bundled = os.path.join(os.path.dirname(rasterio.__file__), "proj_data")
        if os.path.isdir(bundled):
            # ASIGNACIÓN DIRECTA (no setdefault): PostgreSQL suele dejar un
            # PROJ_LIB GLOBAL apuntando a su proj.db viejo, y con setdefault
            # rasterio/rio-tiler seguían leyéndolo (CRSError en tests dev).
            os.environ["PROJ_DATA"] = bundled
            os.environ["PROJ_LIB"] = bundled
    except ImportError:  # pragma: no cover
        pass


_fix_proj_on_windows()


@dataclass(frozen=True)
class Limits:
    """Límites duros v1 — violarlos produce un error tipado honesto."""

    aoi_max_km2: float = 2500.0
    zonal_max_features: int = 5000
    tool_timeout_s: float = 180.0
    # (png_max_px se eliminó junto con el overlay PNG, #33 / re-auditoría.)
    # Retry a nivel de banda: las sondas mostraron truncados HTTP transitorios
    # frecuentes en esta red — 5 intentos con backoff progresivo.
    band_read_retries: int = 5
    default_lookback_days: int = 45   # rango por defecto si no dan fechas
    default_max_cloud_pct: float = 20.0


@dataclass(frozen=True)
class Settings:
    provider: str = "planetary-computer"   # 'planetary-computer' | 'earth-search'
    host: str = "0.0.0.0"
    port: int = 9100
    keys_json: str = "[]"
    limits: Limits = field(default_factory=Limits)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            provider=os.environ.get("IMAGERY_PROVIDER", "planetary-computer"),
            host=os.environ.get("IMAGERY_HOST", "0.0.0.0"),
            port=cls._parse_port(os.environ.get("IMAGERY_PORT", "9100")),
            keys_json=os.environ.get("IMAGERY_MCP_KEYS", "[]"),
        )

    @staticmethod
    def _parse_port(raw: str) -> int:
        """IMAGERY_PORT a int con error claro (antes un valor no numérico daba un
        ValueError críptico de int() al arrancar, #21)."""
        try:
            port = int(raw)
        except ValueError as exc:
            raise ValueError(f"IMAGERY_PORT inválido: {raw!r} (debe ser un entero)") from exc
        if not (1 <= port <= 65535):
            raise ValueError(f"IMAGERY_PORT fuera de rango (1-65535): {port}")
        return port

    def parsed_keys(self) -> list[dict]:
        try:
            data = json.loads(self.keys_json)
        except ValueError as exc:
            raise ValueError(f"IMAGERY_MCP_KEYS no es JSON válido: {exc}") from exc
        if not isinstance(data, list):
            raise ValueError("IMAGERY_MCP_KEYS debe ser una lista JSON")
        return data
