"""Las OPERACIONES espaciales que ejecutan las capacidades del catálogo: medir, buffer, overlay,
join, agregar, autocorrelación y añadir una medida, y el aviso de lo que falta para hacerlas.

Salió de `capabilities_espaciales.py` (F4 del plan de calidad: capabilities_espaciales.py tenía 600 líneas), tal cual.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from geo_copilot.core.logging import get_logger
from geo_copilot.platform.capabilities import ToolOutcome

logger = get_logger("geo_copilot.orchestrator.capabilities_espaciales")


def _ce():
    """`capabilities_espaciales` importa este módulo; lo que las pruebas sustituyen ahí (`store_actual`,
    `_subconjunto_de_la_bd`, …) se resuelve al usarlo."""
    from geo_copilot.orchestrator import capabilities_espaciales

    return capabilities_espaciales


async def _ejecutar(*args: Any, **kwargs: Any) -> ToolOutcome:
    """El `_ejecutar` de capabilities_espaciales (se busca allí al llamarlo: las pruebas lo sustituyen)."""
    return cast(ToolOutcome, await _ce()._ejecutar(*args, **kwargs))

def _falta(nombre: str) -> ToolOutcome:
    # V5 F5: «lotes del catastro a menos de 200 m de las sedes» pasó `catastro.lotes` a
    # ws_spatial_join y el bucle se desvió; cruzar una TABLA de la BD con un dataset lo hace otra
    # herramienta, y eso se dice justo aquí (donde aplica).
    tabla = "." in nombre and not nombre.startswith("ds_") and " " not in nombre
    return ToolOutcome(
        f"`{nombre}` no es un dataset del workspace; usa `activa` (la capa en foco del turno), "
        "`seleccion` (lo seleccionado), `punto` (el punto marcado), `viewport` (la zona visible) "
        "o un id `ds_…` de 'DATASETS DEL WORKSPACE'"
        + (f". `{nombre}` parece una TABLA de la base de datos: para cruzarla con un dataset del "
           "workspace usa `query_database`, que ve las tablas de la BD y estos datasets a la vez"
           if tabla else ""), success=False,
    )


async def _medir(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.platform.workspace import ops

    ds = await _ce()._resolver(args.get("dataset"), working)
    if ds is None:
        return _falta(str(args.get("dataset")))
    return await _ejecutar(working, args, lambda s, ws: ops.medir(s, ws, ds))


async def _agregar_medida(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.platform.workspace import ops

    # el campo va al dataset DE LA CAPA (todas sus filas): un filtro es solo una vista
    ds = await _ce()._resolver_crudo(args.get("dataset"), working)
    if ds is None:
        return _falta(str(args.get("dataset")))
    medida = str(args.get("measure") or "")
    return await _ejecutar(working, args, lambda s, ws: ops.agregar_medida(s, ws, ds, medida))


async def _buffer(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.platform.workspace import ops

    ds = await _ce()._resolver(args.get("dataset"), working)
    if ds is None:
        return _falta(str(args.get("dataset")))
    return await _ejecutar(working, args, lambda s, ws: ops.buffer(
        s, ws, ds, float(args.get("meters") or 0), disolver=bool(args.get("dissolve")),
    ))


async def _overlay(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.platform.workspace import ops

    a, b = await _ce()._resolver(args.get("dataset_a"), working), await _ce()._resolver(args.get("dataset_b"), working)
    if a is None or b is None:
        return _falta(str(args.get("dataset_a") if a is None else args.get("dataset_b")))
    return await _ejecutar(working, args, entradas=(a, b), operar=lambda s, ws: ops.overlay(
        s, ws, a, b, modo=args.get("mode") or "intersection",
    ))


async def _join(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.platform.workspace import ops

    a, b = await _ce()._resolver(args.get("dataset_a"), working), await _ce()._resolver(args.get("dataset_b"), working)
    if a is None or b is None:
        return _falta(str(args.get("dataset_a") if a is None else args.get("dataset_b")))
    metros = args.get("meters")
    return await _ejecutar(working, args, entradas=(a, b), operar=lambda s, ws: ops.union_espacial(
        s, ws, a, b, predicado=args.get("predicate") or "intersects",
        metros=float(metros) if metros is not None else None,
    ))


async def _agregar(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.platform.workspace import ops

    z, d = await _ce()._resolver(args.get("zones"), working), await _ce()._resolver(args.get("data"), working)
    if z is None or d is None:
        return _falta(str(args.get("zones") if z is None else args.get("data")))
    return await _ejecutar(working, args, entradas=(z, d), operar=lambda s, ws: ops.agregar_por_zonas(
        s, ws, z, d, estadistico=args.get("statistic") or "count", campo=args.get("field"),
    ))


async def _autocorrelacion(graph: Any, working: dict, args: dict) -> ToolOutcome:
    from geo_copilot.platform.workspace import ops

    ds = await _ce()._resolver(args.get("dataset"), working)
    if ds is None:
        return _falta(str(args.get("dataset")))
    metodo: Literal["lisa", "gi"] = "gi" if args.get("method") == "gi" else "lisa"
    out = await _ejecutar(working, args, lambda s, ws: ops.autocorrelacion(
        s, ws, ds, str(args.get("field") or ""), metodo=metodo,
    ))
    if out.success and out.delta.get("result_layer_ref"):
        # T3.0 (principio agéntico): la capa sale SIN estilo decidido por código.
        # Se informa el hecho; simbolizarla (y cómo) lo decide el LLM.
        campo = "lisa_clase" if metodo == "lisa" else "gi_clase"
        out.observation += (
            f" | La capa nueva aún no tiene estilo: sus clases están en «{campo}». Si el "
            "usuario quiere ver los clusters, simbolízala con apply_symbology."
        )
    return out
