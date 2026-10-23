"""Estadísticas por grupo de una capa o tabla ArcGIS, calculadas en el servicio (Terranova: UPRA, IGAC)."""

from __future__ import annotations

import json
import re

from geo_mcp_kit import completitud

from arcgis_mcp.red import cliente
from arcgis_mcp.rest import TIMEOUT_CONSULTA_S, ErrorArcGIS, _contar, _json, url_de_capa

ESTADISTICAS = ("count", "sum", "min", "max", "avg", "stddev", "var")
_CAMPO = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
MAX_GRUPOS = 2000


def _campo(nombre: str) -> str:
    if not _CAMPO.match(nombre or ""):
        raise ErrorArcGIS(f"«{nombre}» no es un nombre de campo", "parametro_invalido")
    return nombre


def estadisticas(url: str, *, agrupar: list[str], medidas: list[dict[str, str]], where: str | None = None,
                 orden: list[str] | None = None, max_grupos: int = MAX_GRUPOS) -> tuple[list[dict], dict]:
    """(filas, hechos): conteos, sumas, promedios… por grupo, calculados EN el servicio.

    Terranova (UPRA, IGAC): hectáreas por rango de precio y vigencia en un municipio sin traer cientos
    de polígonos. `medidas` = [{"tipo": "sum", "campo": "area_ha", "nombre": "hectareas"}, …].
    """
    if not agrupar or not medidas:
        raise ErrorArcGIS("hacen falta `agrupar` (campos) y `medidas`", "parametro_invalido")
    pedidas = []
    for m in medidas:
        if m.get("tipo") not in ESTADISTICAS:
            raise ErrorArcGIS(f"tipo de medida: uno de {list(ESTADISTICAS)}", "parametro_invalido")
        pedidas.append({"statisticType": m["tipo"], "onStatisticField": _campo(m.get("campo", "")),
                        "outStatisticFieldName": _campo(m.get("nombre", ""))})
    grupos = [_campo(c) for c in agrupar]
    capa = url_de_capa(url)
    filtro = (where or "").strip() or "1=1"
    tope = max(1, min(int(max_grupos), MAX_GRUPOS))
    with cliente(capa, timeout=TIMEOUT_CONSULTA_S) as http:
        total = _contar(http, capa, filtro)
        params = {"where": filtro, "groupByFieldsForStatistics": ",".join(grupos),
                  "outStatistics": json.dumps(pedidas)}
        if orden:
            params["orderByFields"] = ",".join(_campo(c.split()[0]) + (" DESC" if c.upper().endswith(" DESC") else "")
                                               for c in orden)
        datos = _json(http, f"{capa}/query", params)
    filas = [f.get("attributes") or {} for f in datos.get("features") or []]
    cortado = len(filas) > tope or bool(datos.get("exceededTransferLimit"))
    filas = filas[:tope]
    hechos = {"capa": capa, "grupos": len(filas), "filas_que_resume": total, "completo": not cortado,
              "completitud": completitud(len(filas), None if cortado else len(filas), truncado=cortado,
                                         motivo=f"max_grupos={tope}" if cortado else None),
              "consulta": {"where": filtro, "agrupar": grupos, "medidas": medidas}}
    return filas, hechos
