"""El área (`aoi`) de `sql_query`: filtrar EN EL ORIGEN por la geometría de una capa.

Con geometría en el resultado se filtra el resultado. Sin ella (un `count(*)`, un `avg`…) se filtra la
TABLA antes de resumir: F3 del plan de calidad (verdades ×5) — «¿cuántas sedes hay en Soacha?» traía el
límite del municipio y pedía `SELECT count(*) … aoi=<límite>`, que se rechazaba («el resultado no tiene
geometría»); el agente improvisaba un polígono escrito a mano (55 sedes, eran 41) o traía las 2.000.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg import sql as psql


def geometria_de_aoi(aoi: dict, error: type[Exception]) -> dict:
    """La geometría de un área (Geometry, Feature o FeatureCollection), como la recibe el servidor."""
    if aoi.get("type") == "FeatureCollection":
        geoms = [x.get("geometry") for x in aoi.get("features") or [] if isinstance(x, dict) and x.get("geometry")]
        if not geoms:
            raise error("el área (aoi) no tiene geometría")
        return {"type": "GeometryCollection", "geometries": geoms}
    if aoi.get("type") == "Feature":
        if not aoi.get("geometry"):
            raise error("el área (aoi) no tiene geometría")
        return aoi["geometry"]
    return aoi


def base_con_area(cur: Any, texto: str, geo: str | None, aoi: dict, tablas: list[str],
                  error: type[Exception]) -> Any:
    """El SQL a ejecutar con el área aplicada (componente psycopg)."""
    geojson = json.dumps(geometria_de_aoi(aoi, error))
    if geo:
        # «los lotes de Teusaquillo»: lo que interseca el área (p. ej. el límite del geocodificador)
        return psql.SQL("SELECT * FROM ({}) AS _q WHERE ST_Intersects(_q.{}, ST_Transform(ST_SetSRID("
                        "ST_GeomFromGeoJSON({}), 4326), ST_SRID(_q.{})))").format(
            psql.SQL(texto), psql.Identifier(geo), psql.Literal(geojson), psql.Identifier(geo))  # type: ignore[arg-type]
    if len(tablas) != 1:
        raise error("el resultado no tiene geometría y lee varias tablas: un área (aoi) solo se aplica a un "
                    "resumen (count, avg…) de UNA tabla; selecciona la geometría o quita el área")
    return psql.SQL(_tabla_filtrada(cur, texto, tablas[0], geojson, error))  # type: ignore[arg-type]


def _tabla_filtrada(cur: Any, texto: str, tabla: str, geojson: str, error: type[Exception]) -> str:
    """El mismo SQL con la tabla cambiada por «la tabla dentro del área»: un resumen sobre lo de adentro."""
    import sqlglot
    from sqlglot import exp

    esquema, nombre = tabla.split(".", 1)
    cur.execute("SELECT f_geometry_column FROM geometry_columns WHERE f_table_schema = %s AND f_table_name = %s",
                (esquema, nombre))
    fila = cur.fetchone()
    if not fila:
        raise error(f"{tabla} no tiene geometría: un área (aoi) no se le puede aplicar")
    arbol = sqlglot.parse_one(texto, read="postgres")
    refs = [t for t in arbol.find_all(exp.Table) if t.name == nombre and (t.db or esquema) == esquema]
    if len(refs) != 1:
        raise error("un área (aoi) con un resumen necesita la tabla UNA sola vez en el SQL")
    ref = refs[0]
    interior = sqlglot.parse_one(
        f'SELECT * FROM "{esquema}"."{nombre}" WHERE ST_Intersects("{fila[0]}", ST_Transform(ST_SetSRID('
        f'ST_GeomFromGeoJSON(x), 4326), ST_SRID("{fila[0]}")))', read="postgres")
    marca = next(c for c in interior.find_all(exp.Column) if c.name == "x")
    marca.replace(exp.Literal.string(geojson))
    ref.replace(exp.Subquery(this=interior, alias=exp.TableAlias(this=exp.to_identifier(ref.alias_or_name))))
    return arbol.sql(dialect="postgres")
