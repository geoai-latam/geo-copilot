"""Registro de servidores MCP (`config/mcp_servers.yaml`, plan §3.2).

Un servidor se enchufa por configuración: dónde está, cómo se autentica (por
REFERENCIA a un secreto, nunca el valor), qué tools se permiten (allowlist
obligatoria), su política de riesgo/HITL, límites y, si sirve teselas, qué
prefijos. El nivel de conformidad (G0/G1/G2) dice qué puede hacer el núcleo
con sus resultados.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

Riesgo = Literal["read", "compute", "write", "external_egress"]
Decision = Literal["auto", "approve"]


class _Estricto(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthConfig(_Estricto):
    #: F6 (S6.3): `token_exchange` = cada llamada de un usuario va con un token SUYO para este
    #: servidor (RFC 8693, audiencia `audience`); `secret_ref` queda para lo que hace el sistema
    #: sin usuario (descubrir las tools).
    type: Literal["bearer", "none", "token_exchange"] = "bearer"
    audience: str | None = None
    #: `env:NOMBRE_VARIABLE`. El secreto nunca va en el YAML ni en logs (§3.6.5).
    secret_ref: str | None = None
    #: F6 (S6.2): el secreto YA DESCIFRADO de una conexión de organización (viene cifrado de la
    #: BD). Solo en memoria: no se serializa (exclude) y SecretStr no lo muestra en repr ni logs.
    valor: SecretStr | None = Field(default=None, exclude=True)

    @field_validator("audience")
    @classmethod
    def _audiencia(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None

    def resolver(self) -> str | None:
        """El secreto, o None si la referencia no apunta a nada configurado."""
        if self.type == "none":
            return None
        if self.valor is not None:
            return self.valor.get_secret_value() or None
        if not self.secret_ref:
            return None
        esquema, _, nombre = self.secret_ref.partition(":")
        if esquema != "env" or not nombre:
            raise ValueError(f"secret_ref no soportado: {self.secret_ref!r} (usa env:NOMBRE)")
        return os.environ.get(nombre) or None


class ToolsConfig(_Estricto):
    allow: list[str] = Field(min_length=1)
    deny: list[str] = Field(default_factory=list)
    #: De las permitidas, cuáles ve el AGENTE como herramientas (None = todas). Las demás solo
    #: las llama el núcleo desde sus propios flujos (T5.2: el discovery llama al servidor de
    #: ArcGIS, pero el LLM carga servicios por `load_external`, con su control de procedencia).
    agent: list[str] | None = None

    def permite(self, nombre: str) -> bool:
        if any(fnmatch.fnmatchcase(nombre, p) for p in self.deny):
            return False
        return any(fnmatch.fnmatchcase(nombre, p) for p in self.allow)

    def para_agente(self, nombre: str) -> bool:
        if not self.permite(nombre):
            return False
        return self.agent is None or any(fnmatch.fnmatchcase(nombre, p) for p in self.agent)


class HitlPolicy(_Estricto):
    read: Decision = "auto"
    compute: Decision = "auto"
    write: Decision = "approve"
    external_egress: Decision = "approve"

    @field_validator("write")
    @classmethod
    def _escritura_siempre_aprueba(cls, v: str) -> str:
        # §3.6.3: `write` (y destructiveHint) SIEMPRE pide aprobación.
        if v != "approve":
            raise ValueError("write debe ser 'approve' (no negociable)")
        return v


class Policy(_Estricto):
    default_risk: Riesgo = "compute"
    hitl: HitlPolicy = Field(default_factory=HitlPolicy)
    timeout_s: float = Field(default=60, gt=0, le=900)
    max_result_mb: float = Field(default=20, gt=0, le=200)


class TilesConfig(_Estricto):
    prefixes: list[str] = Field(default_factory=list)


class RecursosConfig(_Estricto):
    """T5.6: rutas del PROPIO servidor desde las que el núcleo descarga un `feature_ref` relativo
    (p. ej. `/resultados/`), con la credencial del servidor. Solo las declaradas."""

    prefixes: list[str] = Field(default_factory=list)


class ServerConfig(_Estricto):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    transport: Literal["streamable_http"] = "streamable_http"
    #: T3.10: lo que devuelve un servidor NO confiable no puede desencadenar, por sí solo,
    #: llamadas a herramientas de OTROS servidores en el mismo turno (piden aprobación).
    trust: Literal["trusted", "untrusted"] = "untrusted"
    url: str = Field(pattern=r"^https?://")
    #: Una línea para el prompt (resumen por servidor, S3.7). Sin ella, la del servidor.
    description: str | None = None
    auth: AuthConfig = Field(default_factory=AuthConfig)
    conformance: Literal["G0", "G1", "G2"] = "G0"
    tools: ToolsConfig
    policy: Policy = Field(default_factory=Policy)
    tiles: TilesConfig = Field(default_factory=TilesConfig)
    recursos: RecursosConfig = Field(default_factory=RecursosConfig)
    #: T5.4: `tabular_geo` para servidores que devuelven FILAS (Snowflake, BigQuery…): el LLM
    #: declara la columna de geometría del resultado y el núcleo la valida y la materializa.
    adapter: Literal["tabular_geo"] | None = None
    #: `geometry`: declaración por defecto ({column, encoding, crs, lat_column}) si el LLM no da una.
    adapter_options: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    #: F6 (S6.2): conexión dada de alta por una ORGANIZACIÓN (no por la plataforma): solo va a
    #: destinos públicos por https y a la IP validada (platform/conexiones/red.py).
    red_restringida: bool = False


class McpConfig(_Estricto):
    servers: list[ServerConfig] = Field(default_factory=list)

    @field_validator("servers")
    @classmethod
    def _ids_unicos(cls, v: list[ServerConfig]) -> list[ServerConfig]:
        ids = [s.id for s in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"ids de servidor repetidos: {ids}")
        return v


def cargar(ruta: str | Path) -> McpConfig:
    """Lee el YAML; un archivo ausente es "sin servidores", uno inválido falla alto."""
    import yaml

    p = Path(ruta)
    if not p.exists():
        return McpConfig()
    datos = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return McpConfig.model_validate(datos)
