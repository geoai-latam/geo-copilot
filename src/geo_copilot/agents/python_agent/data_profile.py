"""Perfil determinista de datos para el generador de código (A4, Fase 4).

Antes de generar código, se computa un perfil BARATO y DETERMINISTA del
GeoJSON (dtypes, nulos, min/max, top categorías, filas de muestra) y se
inyecta al prompt. Mata la clase de errores "columna inventada / tipo
equivocado / valor imposible": el LLM ve datos REALES, no solo nombres.

Todo es código de HECHOS (conteos, tipos) — cero juicio. El juicio sigue
siendo del LLM que escribe el código.
"""

from __future__ import annotations

import json
from typing import Any

# Tope de features a examinar: suficiente fidelidad, costo O(1) respecto a
# capas grandes (el perfil se computa en cada generación de código).
_MAX_FEATURES = 200
_TOP_VALUES = 5
_SAMPLE_ROWS = 3
_MAX_STR = 60  # truncado de valores largos en el perfil


def _dtype_of(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_STR:
        return value[: _MAX_STR - 1] + "…"
    return value


def profile_columns(features: list[dict], max_features: int = _MAX_FEATURES) -> dict[str, dict]:
    """Perfil por columna sobre (hasta) ``max_features`` features.

    Devuelve ``{col: {dtype, nulls, min, max, top}}`` — min/max solo para
    numéricas; top (valor → conteo) solo para no-numéricas.
    """
    sample = features[:max_features]
    profiles: dict[str, dict] = {}
    for feat in sample:
        props = (feat or {}).get("properties") or {}
        for col, val in props.items():
            p = profiles.setdefault(col, {
                "dtypes": {}, "nulls": 0, "min": None, "max": None, "counts": {},
            })
            dt = _dtype_of(val)
            if dt == "null":
                p["nulls"] += 1
                continue
            p["dtypes"][dt] = p["dtypes"].get(dt, 0) + 1
            if dt in ("int", "float"):
                p["min"] = val if p["min"] is None else min(p["min"], val)
                p["max"] = val if p["max"] is None else max(p["max"], val)
            else:
                key = str(_short(val))
                p["counts"][key] = p["counts"].get(key, 0) + 1

    out: dict[str, dict] = {}
    for col, p in profiles.items():
        dtypes = p["dtypes"]
        # dtype dominante; "mixed(a|b)" si conviven varios (dato real que el
        # LLM debe conocer para castear con cuidado).
        if not dtypes:
            dtype = "null"
        elif len(dtypes) == 1:
            dtype = next(iter(dtypes))
        else:
            dtype = "mixed(" + "|".join(sorted(dtypes)) + ")"
        entry: dict[str, Any] = {"dtype": dtype, "nulls": p["nulls"]}
        if p["min"] is not None:
            entry["min"] = p["min"]
            entry["max"] = p["max"]
        if p["counts"]:
            top = sorted(p["counts"].items(), key=lambda kv: (-kv[1], kv[0]))
            entry["top"] = dict(top[:_TOP_VALUES])
        out[col] = entry
    return out


def build_data_profile(
    geojson: dict | None,
    *,
    name: str = "gdf",
    max_features: int = _MAX_FEATURES,
) -> str:
    """Bloque de texto con el perfil real de la capa, para el prompt.

    Vacío si no hay features (el caller decide si eso ya es un error).
    """
    features = (geojson or {}).get("features") or []
    if not features:
        return ""

    cols = profile_columns(features, max_features=max_features)
    n_profiled = min(len(features), max_features)

    lines = [
        f"PERFIL DE DATOS REAL de `{name}` "
        f"(calculado sobre {n_profiled} de {len(features)} features):",
    ]
    for col, p in cols.items():
        bits = [f"dtype={p['dtype']}"]
        if p["nulls"]:
            bits.append(f"nulos={p['nulls']}")
        if "min" in p:
            bits.append(f"min={p['min']}")
            bits.append(f"max={p['max']}")
        if "top" in p:
            bits.append("top=" + json.dumps(p["top"], ensure_ascii=False))
        lines.append(f"  - {col}: " + ", ".join(bits))

    samples = [
        {k: _short(v) for k, v in ((f or {}).get("properties") or {}).items()}
        for f in features[:_SAMPLE_ROWS]
    ]
    lines.append(
        f"Filas de muestra: {json.dumps(samples, ensure_ascii=False, default=str)[:600]}"
    )
    lines.append(
        "❗Usa EXACTAMENTE estos nombres de columna y respeta los dtypes/rangos "
        "reales de arriba. NO inventes columnas ni asumas valores fuera de rango."
    )
    return "\n".join(lines)
