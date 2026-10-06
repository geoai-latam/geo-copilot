"""Lo COMÚN del workspace: roles, límites de teselas y operaciones, errores, cuotas, y las utilidades
de identificadores SQL, SRID y esquema que comparten el almacén y sus mixins.

Salió de `store.py` (F4 del plan de calidad: store.py tenía 904 líneas), tal cual.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

ROL_ESCRITURA = "geo_workspace"


ROL_LECTURA = "gis_readonly"


# Teselas MVT de datasets (S2.3).
MVT_EXTENT = 4096


MVT_MAX_FEATURES = 20_000


MVT_TIMEOUT_MS = 15_000


# Capacidades espaciales deterministas (S2.5).
OPS_TIMEOUT_MS = 120_000


# Tipo PostgreSQL (udt_name) → FieldType del contrato.
_TIPO_PG: dict[str, str] = {
    "int2": "integer", "int4": "integer", "int8": "integer",
    "float4": "number", "float8": "number", "numeric": "number",
    "bool": "boolean", "text": "string", "varchar": "string", "bpchar": "string",
    "date": "date", "timestamp": "datetime", "timestamptz": "datetime",
    "json": "json", "jsonb": "json",
}


def _muestra(v: Any) -> Any:
    """Valor de muestra serializable (Decimal/fecha → número/texto)."""
    from decimal import Decimal

    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


class WorkspaceError(Exception):
    """Error honesto del workspace (se narra al usuario tal cual)."""


class QuotaExceeded(WorkspaceError):
    pass


@dataclass(frozen=True)
class WorkspaceLimits:
    ttl_hours: float = 24.0
    max_features_per_dataset: int = 500_000
    max_features_per_workspace: int = 2_000_000
    max_datasets_per_workspace: int = 200


def schema_de(workspace_id: str) -> str:
    """Esquema de la sesión: `ws_` + hash. No se deriva del id en claro para que
    no sea enumerable ni dependa de qué caracteres traiga el id."""
    if not workspace_id:
        raise WorkspaceError("workspace_id vacío")
    return "ws_" + hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:16]


def _qi(nombre: str) -> str:
    """Identificador SQL citado. Solo se usa con nombres generados aquí."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", nombre):
        raise WorkspaceError(f"identificador no permitido: {nombre!r}")
    return f'"{nombre}"'


def srid_de(crs: str) -> int:
    """EPSG:NNNN / OGC:CRS84 → SRID. Otros CRS: error honesto (no se adivina)."""
    c = crs.strip().upper()
    if c in ("OGC:CRS84", "EPSG:4326"):
        return 4326
    m = re.fullmatch(r"EPSG:(\d{4,6})", c)
    if not m:
        raise WorkspaceError(f"CRS no soportado para ingestar: {crs!r} (usa EPSG:NNNN)")
    return int(m.group(1))


_RESERVADAS = {"fid", "geom"}


def _nombre_columna(clave: str, usadas: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", clave.strip().lower()).strip("_") or "campo"
    if base[0].isdigit():
        base = f"c_{base}"
    base = base[:55]
    nombre, n = base, 1
    while nombre in usadas or nombre in _RESERVADAS:
        n += 1
        nombre = f"{base}_{n}"
    usadas.add(nombre)
    return nombre


def _tipo(valores: list[Any]) -> tuple[str, str]:
    """(tipo SQL, tipo del contrato) a partir de los valores reales (hecho)."""
    presentes = [v for v in valores if v is not None]
    if not presentes:
        return "text", "unknown"
    if all(isinstance(v, bool) for v in presentes):
        return "boolean", "boolean"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in presentes):
        return "bigint", "integer"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in presentes):
        return "double precision", "number"
    if any(isinstance(v, (dict, list)) for v in presentes):
        return "jsonb", "json"
    return "text", "string"


def _esquema_de_propiedades(filas: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """[(clave original, columna, tipo SQL, tipo contrato)] en orden de aparición."""
    claves: list[str] = []
    for props in filas:
        for k in props:
            if k not in claves:
                claves.append(k)
    usadas: set[str] = set()
    salida = []
    for k in claves:
        sql_t, c_t = _tipo([p.get(k) for p in filas])
        salida.append((k, _nombre_columna(str(k), usadas), sql_t, c_t))
    return salida


def _a_texto(valor: Any, tipo_sql: str) -> str | None:
    if valor is None:
        return None
    if tipo_sql == "jsonb":
        return json.dumps(valor, ensure_ascii=False, default=str)
    return str(valor)
