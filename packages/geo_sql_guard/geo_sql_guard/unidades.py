"""Las UNIDADES y el SRID de los argumentos espaciales: el nombre y los argumentos de una función,
la clase de unidades de un argumento (grados o metros), el SRID declarado de las columnas y el
SRID de una expresión, para detectar SRIDs mezclados y medidas en grados.

Salió de `ast.py` (F4 del plan de calidad: ast.py tenía 615 líneas), tal cual.
"""

from __future__ import annotations

import logging

from sqlglot import expressions as exp

from geo_sql_guard.crs import check_measurable_crs
from geo_sql_guard.funciones import _TRANSPARENTES_A_LAS_UNIDADES

logger = logging.getLogger("geo_sql_guard")


def _nombre_de_funcion(nodo: exp.Expression | None) -> str:
    """Nombre en minúsculas de un nodo de función, o cadena vacía.

    sqlglot representa como `Anonymous` las funciones que no conoce de forma
    nativa (ahí caen casi todas las de PostGIS) y con una clase propia las que
    sí (`Count`, `Coalesce`, `StDistance`...). Hay que leer el nombre de forma
    distinta en cada caso: usar `sql_name()` sobre un Anonymous devuelve
    literalmente "ANONYMOUS", que no es el nombre de nada.
    """
    if not isinstance(nodo, exp.Func):
        return ""
    if isinstance(nodo, exp.Anonymous):
        return (nodo.name or "").lower()
    try:
        return str(nodo.sql_name()).lower()
    except Exception:  # `analizar` no debe lanzar nunca; el nombre de clase no está en la allowlist, así que el fallback rechaza (falla cerrado)
        logger.warning(
            "[SQL-AST] sql_name() falló para %s; se usa el nombre de clase",
            type(nodo).__name__, exc_info=True,
        )
        return type(nodo).__name__.lower()


def _argumentos(nodo: exp.Expression) -> list[exp.Expression]:
    """Argumentos posicionales de una llamada, en orden.

    Otra vez la asimetría de sqlglot: un `Anonymous` guarda la lista completa en
    `expressions`, mientras que las clases nativas reparten los argumentos en
    `this` / `expression`. `ST_Length` es Anonymous y `ST_Distance` no lo es, así
    que sin unificar esto la comprobación de unidades se saltaría justo una de
    las cuatro funciones.
    """
    if isinstance(nodo, exp.Anonymous):
        return [a for a in (nodo.expressions or []) if isinstance(a, exp.Expression)]

    argumentos: list[exp.Expression] = []
    for clave in ("this", "expression"):
        valor = nodo.args.get(clave)
        if isinstance(valor, exp.Expression):
            argumentos.append(valor)
    for valor in nodo.args.get("expressions") or []:
        if isinstance(valor, exp.Expression):
            argumentos.append(valor)
    return argumentos


def _sin_parentesis(nodo: exp.Expression | None) -> exp.Expression | None:
    """Quitar los `Paren` envolventes: `ST_Area((geom::geography))` es lo mismo."""
    while isinstance(nodo, exp.Paren):
        nodo = nodo.this
    return nodo


def _clase_de_unidades(arg: exp.Expression | None) -> tuple[str, str]:  # noqa: C901
    """En qué unidades queda la geometría que recibe una función métrica.

    Devuelve `(clase, detalle)` con `clase` en:

      ``geography``  el valor es de tipo `geography`: PostGIS mide sobre el
                     elipsoide y devuelve metros.
      ``metros``     es una `geometry` en un SRID proyectado y métrico.
      ``grados``     todo lo demás, incluido lo que no se puede verificar.

    La distinción entre las dos primeras NO es cosmética: `geography` deja de
    serlo en cuanto alguien le pega un `::geometry`, y una `geometry` en 9377
    sobrevive a ese mismo cast. Ver la rama de `Cast`.

    `detalle` explica el rechazo para el log del modo sombra, que es donde se
    va a medir cuánto SQL real rompería esto.
    """
    arg = _sin_parentesis(arg)
    if arg is None:
        return "geography", ""  # no hay argumento que inspeccionar

    # H15 (V5 de F2): una SUBCONSULTA escalar vale lo que su primera columna.
    # Sin esto, `ST_DWithin(a::geography, (SELECT b::geography …), 300)` se
    # rechazaba como "grados" y el bucle agotaba sus llamadas sin traer nada.
    if isinstance(arg, exp.Alias):
        return _clase_de_unidades(arg.this)
    if isinstance(arg, exp.Subquery):
        interior = arg.this
        if isinstance(interior, exp.Select) and interior.expressions:
            return _clase_de_unidades(interior.expressions[0])
        return "grados", "subconsulta sin columna verificable"

    if isinstance(arg, exp.Cast):
        destino = arg.args.get("to")
        if isinstance(destino, exp.DataType) and destino.this == exp.DataType.Type.GEOGRAPHY:
            return "geography", ""

        clase, detalle = _clase_de_unidades(arg.this)
        if clase == "geography":
            # `geography → geometry` devuelve la geometría en EPSG:4326, o sea
            # GRADOS. Es el remate que un LLM añade cuando el resultado tiene
            # que seguir alimentando un ST_AsGeoJSON o un ST_Intersects, y la
            # primera versión de este guard lo bendecía: recursaba hacia dentro,
            # encontraba el `::geography` y decía "métrico".
            tipo = getattr(destino, "this", None)
            nombre_tipo = getattr(tipo, "value", tipo)
            return "grados", (
                f"un cast de geography a {str(nombre_tipo).lower()} devuelve "
                "EPSG:4326, o sea grados"
            )
        # Un cast entre tipos geometry (`::geometry`) no mueve el SRID: si lo de
        # abajo estaba en 9377, sigue en 9377.
        return clase, detalle

    nombre = _nombre_de_funcion(arg)

    if nombre == "st_transform":
        argumentos = _argumentos(arg)
        if len(argumentos) < 2:
            return "grados", "ST_Transform sin SRID de destino"
        srid_nodo = _sin_parentesis(argumentos[1])
        if (
            isinstance(srid_nodo, exp.Literal)
            and not srid_nodo.is_string
            and str(srid_nodo.this).isdigit()
        ):
            ok, detalle = check_measurable_crs(int(srid_nodo.this))
            return ("metros", "") if ok else ("grados", detalle)
        return "grados", "el SRID de ST_Transform no es una constante verificable"

    if nombre in _TRANSPARENTES_A_LAS_UNIDADES:
        argumentos = _argumentos(arg)
        if argumentos:
            return _clase_de_unidades(argumentos[0])

    return "grados", "falta ::geography o ST_Transform a un SRID métrico"


#: SRID de las columnas geométricas de la BD cuando es UNO solo (lo fija el
#: GISAgent al introspeccionar). `None` = desconocido o mixto: una columna
#: desnuda no aporta SRID y solo se comparan SRIDs explícitos.
_SRID_COLUMNAS: int | None = None


def fijar_srid_de_columnas(srid: int | None) -> None:
    global _SRID_COLUMNAS
    _SRID_COLUMNAS = srid


def _literal_entero(nodo: exp.Expression | None) -> int | None:
    nodo = _sin_parentesis(nodo)
    if isinstance(nodo, exp.Literal) and not nodo.is_string and str(nodo.this).isdigit():
        return int(nodo.this)
    return None


def _srid_de(arg: exp.Expression | None) -> int | str | None:  # noqa: C901
    """SRID de la geometría que produce `arg`: int, "geography" o None (no se sabe)."""
    arg = _sin_parentesis(arg)
    if arg is None:
        return None
    if isinstance(arg, exp.Alias):
        return _srid_de(arg.this)
    if isinstance(arg, exp.Subquery):
        interior = arg.this
        if isinstance(interior, exp.Select) and interior.expressions:
            return _srid_de(interior.expressions[0])
        return None
    if isinstance(arg, exp.Cast):
        destino = arg.args.get("to")
        if isinstance(destino, exp.DataType) and destino.this == exp.DataType.Type.GEOGRAPHY:
            return "geography"
        interior = _srid_de(arg.this)
        # geography::geometry vuelve a 4326; geometry::geometry no mueve el SRID.
        return 4326 if interior == "geography" else interior
    if isinstance(arg, exp.Column):
        return _SRID_COLUMNAS
    nombre = _nombre_de_funcion(arg)
    argumentos = _argumentos(arg)
    if nombre in ("st_transform", "st_setsrid") and len(argumentos) >= 2:
        return _literal_entero(argumentos[1])
    if nombre == "st_makeenvelope" and len(argumentos) >= 5:
        return _literal_entero(argumentos[4])
    if nombre in ("st_geomfromtext", "st_geomfromwkb") and len(argumentos) >= 2:
        return _literal_entero(argumentos[1])
    if nombre == "st_geomfromgeojson":
        return 4326
    if nombre in _TRANSPARENTES_A_LAS_UNIDADES and argumentos:
        return _srid_de(argumentos[0])
    return None


def _srid_mezclado(nombre: str, nodo: exp.Expression) -> str | None:
    argumentos = _argumentos(nodo)
    if len(argumentos) < 2:
        return None
    a, b = _srid_de(argumentos[0]), _srid_de(argumentos[1])
    if isinstance(a, int) and isinstance(b, int) and a != b:
        return (
            f"SRID mezclado en {nombre}: {a} contra {b}. Con SRID distintos el filtro "
            "espacial no encuentra nada y el resultado sale vacío o en cero sin error; "
            "lleva los dos lados al mismo SRID (ST_Transform) o mide con ::geography"
        )
    return None


def _argumento_en_metros(arg: exp.Expression | None) -> tuple[bool, str]:
    """`(¿mide en metros?, detalle)` — envoltorio de `_clase_de_unidades`."""
    clase, detalle = _clase_de_unidades(arg)
    return clase in ("geography", "metros"), detalle
