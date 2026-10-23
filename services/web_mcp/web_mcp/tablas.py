"""Tablas de un archivo publicado, Excel o CSV (Terranova F5): anexos del DANE y directorios de la DIAN.

- El archivo vigente se descubre en su página índice (`pagina` + `enlace_con`): la DIAN pone la fecha
  en el nombre de su archivo diario y el DANE el mes en el de su anexo.
- `hoja` y `fila_encabezado`: los anexos del DANE traen títulos arriba y el encabezado en la fila 9.
- `rellenar`: las primeras columnas combinadas («Nacional» una vez, para varias filas) se rellenan
  hacia abajo.
- `largo_desde`: una tabla ancha (un año por columna) pasa a una fila por (identificadores, columna,
  valor). Así un año nuevo no rompe la lista blanca de quien la consume.
- `filtros`: igualdad, sin distinguir mayúsculas, sobre las columnas.
"""

from __future__ import annotations

import csv
import io
from typing import Any

MAX_FILAS = 5000
MAX_BYTES = 25_000_000


def _texto(valor: Any) -> str:
    return "" if valor is None else " ".join(str(valor).split())


def _encabezados(fila: tuple[Any, ...]) -> list[str]:
    nombres, vistos = [], set()
    for i, celda in enumerate(fila):
        nombre = _texto(celda) or f"columna_{i + 1}"
        while nombre in vistos:
            nombre += "_"
        vistos.add(nombre)
        nombres.append(nombre)
    return nombres


def filas_crudas(contenido: bytes, nombre: str, hoja: str | None) -> tuple[list[tuple[Any, ...]], list[str]]:
    """(filas, hojas) de un .xlsx o un .csv."""
    if len(contenido) > MAX_BYTES:
        raise ValueError(f"el archivo pesa {len(contenido)} bytes: más de {MAX_BYTES}")
    if nombre.lower().split("?")[0].endswith(".csv"):
        texto = contenido.decode("utf-8-sig", errors="replace")
        separador = ";" if texto[:2000].count(";") > texto[:2000].count(",") else ","
        return [tuple(f) for f in csv.reader(io.StringIO(texto), delimiter=separador)], []
    import openpyxl  # solo para Excel; read_only no carga el archivo entero en memoria

    libro = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    hojas = list(libro.sheetnames)
    if hoja is not None and hoja not in hojas:
        # algunos anexos traen espacios al final del nombre de la hoja («IPM_Sexo »)
        hoja = next((h for h in hojas if h.strip() == hoja.strip()), None)
        if hoja is None:
            raise ValueError(f"no hay hoja con ese nombre; las hojas son: {hojas}")
    filas = list(libro[hoja or hojas[0]].iter_rows(values_only=True))
    libro.close()
    return filas, hojas


def tabla(filas: list[tuple[Any, ...]], fila_encabezado: int = 1, rellenar: int = 0,
          largo_desde: int | None = None) -> list[dict[str, Any]]:
    """Las filas como diccionarios, con las primeras `rellenar` columnas rellenas hacia abajo y, si
    `largo_desde`, en formato largo (columnas desde esa posición, 1-indexada → `columna`, `valor`)."""
    if fila_encabezado < 1 or fila_encabezado > len(filas):
        raise ValueError(f"fila_encabezado fuera del archivo (tiene {len(filas)} filas)")
    nombres = _encabezados(filas[fila_encabezado - 1])
    previos: list[Any] = [None] * rellenar
    salida: list[dict[str, Any]] = []
    for fila in filas[fila_encabezado:]:
        valores = list(fila) + [None] * (len(nombres) - len(fila))
        if all(v is None or _texto(v) == "" for v in valores):
            continue
        for i in range(rellenar):
            if valores[i] is None or _texto(valores[i]) == "":
                valores[i] = previos[i]
            previos[i] = valores[i]
        registro = dict(zip(nombres, valores, strict=False))
        if largo_desde is None:
            salida.append(registro)
            continue
        ids = {n: registro[n] for n in nombres[: largo_desde - 1]}
        for n in nombres[largo_desde - 1:]:
            if registro.get(n) is not None and _texto(registro[n]) != "":
                salida.append({**ids, "columna": n, "valor": registro[n]})
    return salida


def filtrar(registros: list[dict[str, Any]], filtros: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not filtros:
        return registros
    columnas = set().union(*(r.keys() for r in registros)) if registros else set()
    if faltan := set(filtros) - columnas:
        raise ValueError(f"no hay columnas {sorted(faltan)}; las columnas son: {sorted(columnas)}")
    buscados = {c: _texto(v).casefold() for c, v in filtros.items()}
    return [r for r in registros if all(_texto(r.get(c)).casefold() == v for c, v in buscados.items())]
