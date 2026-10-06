"""Datasets del workspace visibles para el SQL de ESTA sesión (S2.2).

El nodo SQL fija, al empezar, qué tablas del workspace pertenecen a la sesión en
curso; el validador AST las suma a la allowlist del semantic layer. Va por una
ContextVar porque la validación ocurre lejos del nodo (en `_execute_sql`, sin
la sesión a mano) y cada petición corre en su propia tarea asyncio: una sesión
nunca ve las tablas de otra.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

from geo_copilot.platform.contracts import LayerRef, WorkspaceTable

if TYPE_CHECKING:
    from geo_copilot.platform.workspace.store import DatasetStore

# El store lo instala la app al arrancar (como el EventSink): el núcleo lo usa
# sin importar la API.
_store: DatasetStore | None = None


def instalar_store(store: DatasetStore | None) -> None:
    global _store
    _store = store


def store_actual() -> DatasetStore | None:
    return _store


tablas_de_sesion: ContextVar[frozenset[str]] = ContextVar("tablas_de_sesion", default=frozenset())


def fijar_datasets(refs: list[LayerRef]) -> frozenset[str]:
    """Registra las tablas del workspace de la sesión para el validador."""
    tablas = frozenset(
        f"{r.storage.schema_name}.{r.storage.table}".lower()
        for r in refs if isinstance(r.storage, WorkspaceTable)
    )
    tablas_de_sesion.set(tablas)
    return tablas


def bloque_para_llm(refs: list[LayerRef], filtros: dict[str, str] | None = None,
                    valores: dict[str, str] | None = None) -> str:
    """Los datasets de la sesión como contexto de esquema para el generador SQL.

    `filtros` (FH.5): {dataset_id: «estrato = 3»} de las capas que el usuario tiene
    FILTRADAS en el mapa (la tabla guarda todas las filas; el mapa muestra ese subconjunto).
    """
    lineas = []
    for r in refs:
        if not isinstance(r.storage, WorkspaceTable):
            continue
        cols = ["fid (integer)"] + [f"{f.name} ({f.type})" for f in r.fields]
        if r.storage.geometry_column:
            cols.append(f"{r.storage.geometry_column} (geometry {r.geometry_type or ''}, SRID 4326)")
        filtro = (filtros or {}).get(r.id)
        vals = (valores or {}).get(r.id)
        lineas.append(
            f"- {r.storage.schema_name}.{r.storage.table} — «{r.name}» "
            f"({r.feature_count} elementos); columnas: {', '.join(cols)}"
            + (f" — en el mapa del usuario está FILTRADA: solo muestra los que cumplen {filtro}" if filtro else "")
            # V5 (sql/lugares): con el límite de «UPZ Teusaquillo» el SQL filtró `w.nombre = 'Teusaquillo'`
            # (adivinado) y dio 0 lotes donde hay 5197. Los valores reales son hechos para el filtro.
            + (f" — valores: {vals}" if vals else "")
            + (" — es UN solo elemento: crúzalo entero, sin filtrar por sus atributos" if r.feature_count == 1 else "")
        )
    if not lineas:
        return ""
    return (
        "\n\nDATASETS DEL WORKSPACE DE ESTA SESIÓN (resultados de turnos anteriores, "
        "ya materializados; úsalos con su nombre calificado y crúzalos con las tablas "
        "de la BD cuando el usuario se refiera a ellos — 'lo que traje', 'esas "
        "construcciones', el nombre de la capa):\n" + "\n".join(lineas) + "\n"
        # V3 F2 (H14): el LLM recalculó "ese buffer" desde la BD transformando
        # un solo lado a 32618, y el cruce con `shape` (4326) dio 0 sin error.
        "- Si la pregunta es sobre un resultado anterior ('ese buffer', 'esos lotes'), "
        "usa SU tabla de aquí tal cual: no lo recalcules desde la BD.\n"
        "- Estas tablas están en SRID 4326, igual que la BD: compáralas directo "
        "(ST_Intersects(t.shape, w.geom)); para metros usa ::geography. Nunca "
        "transformes un solo lado de un predicado espacial."
    )
