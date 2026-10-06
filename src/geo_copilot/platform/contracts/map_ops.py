"""Operaciones sobre el mapa compartido (plan de plataforma §2.7, FH.1).

`MapCommand` = lo que el AGENTE le pide al mapa; `MapAction` = una operación ya
hecha sobre el mapa, por el usuario o por el agente, tal como queda en el
registro. Las dos pasan por el mismo reducer del frontend: eso garantiza la
paridad «lo que hace el agente lo puede hacer el usuario, y al revés», y que
todo se pueda deshacer.

Cada operación declara sus argumentos (unión discriminada por `op`): un
`set_style` trae un `StyleSpec` válido, un `set_opacity` un número en [0, 1]…
Lo que no cumple, no llega al mapa (en F4 `set_style.args` era un dict libre).
Las operaciones de specs posteriores (selección, filtro, tiempo, comparar,
pedir un dato) se añaden aquí con sus argumentos cuando se implementen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from geo_copilot.platform.contracts.common import BBox, Strict
from geo_copilot.platform.contracts.layers import StyleSpec

#: Operaciones que hoy tienen gesto manual Y orden del agente (FH.1).
MapOp = Literal[
    "add_layer", "remove_layer", "set_style", "set_visibility", "set_opacity",
    "reorder", "set_label", "zoom_to", "select", "clear_selection",
    # FH.3: solo del usuario (el registro las muestra; el agente no las ordena)
    "rename_layer", "edit_geometry",
    # FH.5
    "set_filter",
    # FH.10
    "save_view", "compare", "end_compare", "set_time",
]


class _Cmd(Strict):
    #: Capa sobre la que actúa (su [id] en el mapa). `zoom_to` puede ir sin capa, con bbox.
    layer_id: str | None = None
    #: Por qué lo hace el agente (se muestra en el registro de operaciones).
    reason: str | None = None


class SetStyleArgs(Strict):
    style: StyleSpec


class SetStyle(_Cmd):
    op: Literal["set_style"] = "set_style"
    args: SetStyleArgs


class SetVisibilityArgs(Strict):
    visible: bool


class SetVisibility(_Cmd):
    op: Literal["set_visibility"] = "set_visibility"
    args: SetVisibilityArgs


class SetOpacityArgs(Strict):
    opacity: float = Field(ge=0, le=1)


class SetOpacity(_Cmd):
    op: Literal["set_opacity"] = "set_opacity"
    args: SetOpacityArgs


class ReorderArgs(Strict):
    #: A dónde va en el orden de dibujo: arriba del todo, abajo del todo, o
    #: justo encima/debajo de otra capa.
    to: Literal["top", "bottom", "above", "below"]
    #: La capa de referencia para `above`/`below`.
    relative_to: str | None = None


class Reorder(_Cmd):
    op: Literal["reorder"] = "reorder"
    args: ReorderArgs


class SetLabelArgs(Strict):
    #: Campo cuyo valor se escribe sobre cada elemento; `null` quita las etiquetas.
    field: str | None = None


class SetLabel(_Cmd):
    op: Literal["set_label"] = "set_label"
    args: SetLabelArgs


class ZoomToArgs(Strict):
    #: Sin capa: encuadra esta extensión (EPSG:4326).
    bbox: BBox | None = None


class ZoomTo(_Cmd):
    op: Literal["zoom_to"] = "zoom_to"
    args: ZoomToArgs = Field(default_factory=ZoomToArgs)


class RemoveLayerArgs(Strict):
    pass


class RemoveLayer(_Cmd):
    op: Literal["remove_layer"] = "remove_layer"
    args: RemoveLayerArgs = Field(default_factory=RemoveLayerArgs)


class Predicado(Strict):
    """Condición sobre un atributo: así viaja una selección grande (FH.2), sin ids
    ni geometrías. El valor va como parámetro, nunca concatenado en SQL."""

    field: str = Field(min_length=1, max_length=128)
    op: Literal["=", "!=", ">", ">=", "<", "<=", "in", "contains"]
    value: str | float | int | bool | list[str | float | int]


class SelectArgs(Strict):
    #: Elementos por su id en el mapa (índice en capas en memoria; `fid` en teselas).
    ids: list[int] | None = Field(default=None, max_length=5000)
    #: …o por condición (las selecciones grandes: no viajan ids).
    where: Predicado | None = None
    #: replace = la selección pasa a ser ésta; add = se suma; toggle = invierte esos ids.
    mode: Literal["replace", "add", "toggle"] = "replace"
    #: De dónde viene (clic, caja, lazo, tabla, consulta, agente): se muestra y lo ve el agente.
    origin: Literal["click", "box", "lasso", "table", "query", "agent", "link"] = "agent"
    #: Cuántos quedan seleccionados (lo calcula quien selecciona; con condición sobre
    #: teselas, el mapa no puede contarlos por sí mismo).
    count: int | None = Field(default=None, ge=0)


class Select(_Cmd):
    op: Literal["select"] = "select"
    args: SelectArgs


class ClearSelectionArgs(Strict):
    pass


class ClearSelection(_Cmd):
    """Sin `layer_id`: limpia la selección de todas las capas."""

    op: Literal["clear_selection"] = "clear_selection"
    args: ClearSelectionArgs = Field(default_factory=ClearSelectionArgs)


class SetFilterArgs(Strict):
    #: Condiciones que TODOS los elementos visibles cumplen (AND). Vacía = sin filtro.
    #: Como una «definition query»: la capa filtrada ES ese subconjunto, en el mapa y
    #: para las herramientas.
    where: list[Predicado] = Field(default_factory=list, max_length=10)
    #: Cuántos quedan (lo calcula quien filtra).
    count: int | None = Field(default=None, ge=0)


class SetFilter(_Cmd):
    op: Literal["set_filter"] = "set_filter"
    args: SetFilterArgs = Field(default_factory=SetFilterArgs)


class SaveViewArgs(Strict):
    #: Nombre de la vista (marcador) que se guarda con la extensión que se ve ahora.
    nombre: str = Field(min_length=1, max_length=80)


class SaveView(_Cmd):
    """FH.10: guarda la vista actual como marcador (ir a una: `zoom_to` con su bbox)."""

    op: Literal["save_view"] = "save_view"
    args: SaveViewArgs


class CompareArgs(Strict):
    #: Las dos capas a comparar con la cortina: su [id], su nombre EXACTO o `activa`.
    left: str = Field(min_length=1, max_length=200)
    right: str = Field(min_length=1, max_length=200)


class Compare(_Cmd):
    """FH.10: comparación con cortina (swipe): `left` a la izquierda, `right` a la derecha."""

    op: Literal["compare"] = "compare"
    args: CompareArgs


class EndCompareArgs(Strict):
    pass


class EndCompare(_Cmd):
    op: Literal["end_compare"] = "end_compare"
    args: EndCompareArgs = Field(default_factory=EndCompareArgs)


class SetTimeArgs(Strict):
    #: El instante (ISO, p. ej. 2026-06-12) de la serie temporal que se muestra; `play` la anima.
    time: str | None = Field(default=None, max_length=40)
    play: bool = False


class SetTime(_Cmd):
    """FH.10: control de tiempo sobre las capas con fecha (una serie de imagery)."""

    op: Literal["set_time"] = "set_time"
    args: SetTimeArgs = Field(default_factory=SetTimeArgs)


#: FH.9: lo que el agente puede pedir al usuario EN el mapa.
ModoPedido = Literal["pick_point", "draw_area", "pick_layer", "pick_features"]


class RequestInputArgs(Strict):
    mode: ModoPedido
    #: La pregunta al usuario (se muestra sobre el mapa y en el chat).
    prompt: str = Field(min_length=1, max_length=300)


class RequestInput(_Cmd):
    """FH.9: el agente suspende el turno y pide algo en el mapa; la respuesta del usuario
    llega en el turno siguiente (`map_context.respuesta_mapa`), con la consulta original.
    `layer_id` (opcional) acota pick_features a una capa."""

    op: Literal["request_input"] = "request_input"
    args: RequestInputArgs


MapCommand = Annotated[
    SetStyle | SetVisibility | SetOpacity | Reorder | SetLabel | ZoomTo | RemoveLayer | Select | ClearSelection
    | SetFilter | RequestInput | SaveView | Compare | EndCompare | SetTime,
    Field(discriminator="op"),
]


class MapAction(Strict):
    """Una operación del registro, como la ve el agente en el turno siguiente.

    No lleva el estado completo (la capa ya está descrita en `map_context.layers`):
    lleva QUIÉN hizo QUÉ sobre CUÁL capa y si luego se deshizo.
    """

    op: MapOp
    layer_id: str | None = None
    #: Nombre de la capa en el momento de la operación (la capa pudo desaparecer).
    layer_name: str | None = None
    args: dict[str, Any] = Field(default_factory=dict)
    at: datetime
    author: Literal["user", "agent"] = "user"
    #: `True` si se deshizo después (Ctrl+Z): el agente debe saber que su cambio no quedó.
    undone: bool = False
