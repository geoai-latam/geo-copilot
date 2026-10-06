"""Los HECHOS del resultado de `sql_query`: lo que el LLM necesita saber para no narrar algo falso.

Separado del servidor (F3 del plan de calidad) porque es una responsabilidad propia: el servidor
valida y ejecuta; aquí se describe lo que salió (tope alcanzado, geometría escrita a mano, tablas
leídas, un cero que no significa «no hay»).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg import sql as psql

logger = logging.getLogger("postgis_mcp")

#: Una geometría escrita en el texto del SQL (no una capa): ST_MakeEnvelope, WKT, GeoJSON literal.
GEOMETRIA_ESCRITA = re.compile(r"ST_MakeEnvelope|ST_GeomFromText|ST_GeomFromGeoJSON|POLYGON\s*\(|ST_Point\s*\(",
                               re.IGNORECASE)


def del_resultado(fuente: str, veredicto: Any, texto: str, filas: list, *, completo: bool, limite: int,
                  otras: list[str], geo: str | None, aoi: dict | None,
                  conectar: Callable[[], Any], timeout_s: int) -> dict[str, Any]:
    """Los hechos del resultado de `sql_query`: lo que el LLM necesita para no narrar algo falso."""
    hechos: dict[str, Any] = {
        "fuente": fuente, "filas": len(filas), "completo": completo, "columnas": otras,
        **({"filtro_espacial": "solo lo que interseca el área (aoi), aplicado en la base"} if aoi else {}),
        # V5 (sql): un rectángulo escrito a mano trajo 34.738 lotes «de Teusaquillo» que no eran la localidad
        **({"aviso_area": _aviso_area(aoi)} if GEOMETRIA_ESCRITA.search(texto) else {}),
        # qué tablas se LEYERON (del análisis del SQL): V3 F6 (E0.2) contó sedes educativas cuyo
        # nombre dice «hospital» y lo narró como «la tabla hospitales», que no existe
        "tablas_consultadas": sorted(veredicto.get("tablas") or []),
        **({} if completo else {"aviso": f"se alcanzó el tope de {limite} filas: NO es el total"})}
    if not aoi and not _tiene_filtro(texto):
        # F3 (verdades ×5): `SELECT count(*) FROM educacion.sedes_educativas` → «en Soacha hay 2000» (son las
        # de todo Cundinamarca). El juez revisa «N en <lugar>» contra este hecho (como en `archivos`).
        hechos["sin_filtro"] = ("el SQL no filtra (sin WHERE ni área): el resultado cubre TODA la tabla, no un "
                                "lugar ni una condición")
    if not filas:
        hechos["aviso"] = ("la consulta no devolvió filas. Si filtraste por un texto, puede que el valor se "
                           "escriba distinto (mayúsculas, tildes): compara con UPPER()/ILIKE o mira los "
                           "valores reales con SELECT DISTINCT")
    elif not geo:
        # V3 F5: pidió ST_AsGeoJSON(geom) y trató de cargar ese texto como URL `data:`
        hechos["nota"] = ("el resultado no tiene columna de geometría: es una TABLA. Para verlo en el mapa, "
                          "selecciona la columna de geometría tal cual (sin ST_AsGeoJSON/ST_AsText)")
    if _vacio(filas, geo):
        parecidos = _valores_parecidos(conectar, timeout_s, texto, sorted(veredicto.get("tablas") or []))
        if parecidos:
            # F3 (verdades ×5): `count(*) … WHERE nombre_mun = 'Soacha'` da UNA fila con 0 (el aviso de
            # «sin filas» no salta) y el agente respondía «no hay sedes en Soacha»: hay 41, como 'SOACHA'.
            hechos["valores_parecidos"] = parecidos
            hechos["aviso_texto"] = ("este 0 NO significa que no haya: el filtro de texto es EXACTO y el dato se "
                                     "escribe distinto (valores_parecidos); repite con el valor real")
    # Los avisos PRIMERO: quien los lee (el juez de la respuesta) recibe cada resultado recortado, y con
    # muchas columnas un aviso al final quedaba fuera
    primero = ("aviso", "sin_filtro", "valores_parecidos")
    return {k: hechos[k] for k in sorted(hechos, key=lambda k: not k.startswith(primero))}


def _aviso_area(aoi: dict | None) -> str:
    """Una geometría escrita en el SQL. F3: con «Si representa un lugar…» el juez aceptaba «en Soacha hay
    55» porque se había traído el límite de Soacha; lo decisivo es que ESTE resultado no lo usó."""
    texto = ("el SQL lleva una geometría escrita en el texto (coordenadas: un rectángulo o un polígono). Si "
             "representa un lugar, NO es su límite: dilo así, o pasa el límite real como capa en `aoi`")
    if aoi:
        return texto
    # lo decisivo DELANTE: el resultado llega recortado a quien lo juzga
    return ("esta cifra NO está contada dentro del límite de ningún lugar (aunque se haya traído uno): no se "
            "pasó ninguna capa en `aoi` y el área es una geometría ESCRITA A MANO en el SQL, una aproximación")


def _tiene_filtro(texto: str) -> bool:
    """¿El SQL filtra filas (WHERE, HAVING o JOIN con condición)? Ante la duda (no se pudo leer), sí."""
    import sqlglot
    from sqlglot import exp

    try:
        arbol = sqlglot.parse_one(texto, read="postgres")
    except sqlglot.errors.ParseError:
        return True
    return any(True for _ in arbol.find_all(exp.Where, exp.Having, exp.Join))


def _vacio(filas: list, geo: str | None) -> bool:
    """Sin filas, o una sola fila de agregados en cero o nulos (`count(*)` = 0)."""
    if not filas:
        return True
    return not geo and len(filas) == 1 and all(v in (0, None) for v in filas[0])


def _sin_tildes(texto: str) -> str:
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def _comparaciones_de_texto(texto: str) -> list[tuple[str, str]]:
    """Las condiciones `columna = 'texto'` del SQL (columna, valor)."""
    import sqlglot
    from sqlglot import exp

    try:
        arbol = sqlglot.parse_one(texto, read="postgres")
    except sqlglot.errors.ParseError:
        return []
    pares = []
    for eq in arbol.find_all(exp.EQ):
        for col, lit in ((eq.left, eq.right), (eq.right, eq.left)):
            if isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string:
                pares.append((col.name, lit.this))
    return pares[:3]


def _valores_parecidos(conectar: Callable[[], Any], timeout_s: int, texto: str,
                       tablas: list[str]) -> dict[str, list[str]]:
    """Para cada `columna = 'texto'`, hasta 5 valores de la tabla que lo contienen sin mirar mayúsculas
    ni tildes. Solo con UNA tabla leída (con varias, la columna sería ambigua)."""
    pares = _comparaciones_de_texto(texto)
    if len(tablas) != 1 or not pares:
        return {}
    esquema, tabla = tablas[0].split(".", 1)
    salida: dict[str, list[str]] = {}
    try:
        with conectar() as con, con.cursor() as cur:
            cur.execute(psql.SQL("SET LOCAL statement_timeout = {}").format(psql.Literal(f"{timeout_s}s")))
            for col, valor in pares:
                cur.execute(psql.SQL(
                    "SELECT DISTINCT {c}::text FROM {e}.{t} WHERE translate(lower({c}::text), "
                    "'áéíóúüñàèìòù', 'aeiouunaeiou') LIKE {p} LIMIT 5").format(
                    c=psql.Identifier(col), e=psql.Identifier(esquema), t=psql.Identifier(tabla),
                    p=psql.Literal(f"%{_sin_tildes(valor)}%")))
                encontrados = [r[0] for r in cur.fetchall() if r[0] != valor]
                if encontrados:
                    salida[f"{col} = '{valor}'"] = encontrados
    except psycopg.Error:
        logger.warning("valores parecidos: no se pudieron buscar", exc_info=True)
        return {}
    return salida
