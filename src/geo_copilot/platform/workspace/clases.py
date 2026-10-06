"""Cuántos elementos caen en cada clase de un estilo, contados sobre el dataset ENTERO.

Una capa grande (más de `workspace_inline_max_features`) se estiliza sobre una muestra repartida
(H23): las clases salen bien, pero sus conteos eran los de la muestra. La leyenda de la Malla Vial
de Bogotá (136.956 líneas) decía «M (1972) · B (1236) · R (1102) · SD (690)», que suman 5.000.
Aquí se recuentan en PostGIS con la MISMA regla con que el mapa pinta (`_clase_de` del agente de
simbología, `maplibreSymbology.ts`): la primera clase que contiene el valor, `[lo, hi)` salvo la
última `[lo, hi]`, un rango degenerado es igualdad, y una clase sin límites es un valor único
(comparado como texto, como `String(v) === label` en el cliente).
"""
from __future__ import annotations

import logging
from typing import Any

from geo_copilot.platform.workspace.ops import _tabla
from geo_copilot.platform.workspace.store import _qi

logger = logging.getLogger(__name__)

_NUMERICOS = ("number", "integer")


def _condicion(clase: dict, i: int, ultima: bool, col: str, numerico: bool,
               params: list[Any]) -> str:
    lo, hi = clase.get("min_value"), clase.get("max_value")
    if lo is None and hi is None:
        params.append(str(clase.get("label")))
        return f"{col}::text = ${len(params)}"
    if lo is None or hi is None or not numerico:
        return "false"  # el mapa tampoco pinta un rango con un solo límite
    if lo == hi:
        params.append(float(lo))
        return f"{col}::float8 = ${len(params)}"
    params.extend([float(lo), float(hi)])
    op = "<=" if ultima else "<"
    return f"({col}::float8 >= ${len(params) - 1} AND {col}::float8 {op} ${len(params)})"


async def recontar_clases(store: Any, session_id: str, dataset_id: str, symbology: dict | None) -> dict | None:
    """El estilo con los conteos de sus clases sobre TODO el dataset (o el mismo si no aplica)."""
    from geo_copilot.core.config import get_settings

    clases = (symbology or {}).get("class_breaks") or []
    campo = (symbology or {}).get("classification_field")
    if not (symbology and clases and campo and store is not None):
        return symbology
    try:
        ref = await store.get(session_id, dataset_id)
        if ref is None or (ref.feature_count or 0) <= get_settings().workspace_inline_max_features:
            return symbology  # estilizada con todos sus elementos: los conteos ya son exactos
        tipos = {f.name: f.type for f in ref.fields}
        if campo not in tipos:
            return symbology
        col = f"s.{_qi(campo)}"
        params: list[Any] = []
        casos = " ".join(
            f"WHEN {_condicion(c, i, i == len(clases) - 1, col, tipos[campo] in _NUMERICOS, params)} THEN {i}"
            for i, c in enumerate(clases))
        filas = await store.filas(
            session_id,
            f"SELECT k, count(*) AS n FROM (SELECT CASE {casos} END AS k FROM {_tabla(ref)} s) t "
            f"WHERE k IS NOT NULL GROUP BY k", tuple(params))
    except Exception:  # sin recuento la leyenda conserva los de la muestra; el mapa no cambia
        logger.warning("[clases] no se pudieron recontar las clases de %s", dataset_id, exc_info=True)
        return symbology
    por_clase = {int(f["k"]): int(f["n"]) for f in filas}
    return {**symbology, "class_breaks": [{**c, "count": por_clase.get(i, 0)} for i, c in enumerate(clases)]}
