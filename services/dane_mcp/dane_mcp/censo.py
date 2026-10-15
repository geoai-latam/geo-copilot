"""Censo 2018 por municipio y por manzana (con su geometría del MGN 2025)."""

from __future__ import annotations

import unicodedata
from typing import Any

from dane_mcp import fuentes

#: lo que se da siempre de un municipio (campos del nivel municipio, con prefijo S)
BASICOS_MUNICIPIO = {"STP27_PERS": "personas", "STVIVIENDA": "viviendas", "TSP16_HOG": "hogares"}
COD_MANZANA = ("Código_DANE_concatenado_departamento__municipio__clase__sector_rural__sección_rural__zona_urbana__"
               "sector_urbano__sección_urbana_y_manzana")
#: del MGN 2025 (no del censo): se dan aparte y con su fuente
MGN_2025 = {"Total_viviendas_manzana": "viviendas_recuento_2025",
            "Total_unidades_economicas": "unidades_economicas_cenu_2025",
            "Área_manzana_en_metros_cuadrados": "area_m2"}


class ErrorCenso(ValueError):
    """Pedido que no se puede atender (el mensaje dice qué hay)."""


def plano(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(texto).lower()) if unicodedata.category(c) != "Mn")


def validar_variables(nivel: str, variables: list[str]) -> list[str]:
    disponibles = {c["campo"].upper(): c["campo"] for c in fuentes.campos(nivel)}
    faltan = [v for v in variables if v.upper() not in disponibles]
    if faltan:
        raise ErrorCenso(f"variables que el nivel «{nivel}» no tiene: {', '.join(faltan)} (míralas con dane_variables)")
    return [disponibles[v.upper()] for v in variables]


def _departamentos() -> dict[str, str]:
    return {f["DPTO_CCDGO"]: f["DPTO_CNMBR"] for f in fuentes.consultar_censo(
        "departamento", "1=1", ["DPTO_CCDGO", "DPTO_CNMBR"])}


def municipios(nombre: str | None, codigo: str | None, variables: list[str]) -> list[dict[str, Any]]:
    """Municipios por código DIVIPOLA exacto o por nombre (sin tildes, contiene), con sus totales del censo."""
    extra = validar_variables("municipio", variables) if variables else []
    salida = list(BASICOS_MUNICIPIO) + [v for v in extra if v not in BASICOS_MUNICIPIO]
    campos = ["MPIO_CDPMP", "MPIO_CNMBR", "DPTO_CCDGO", "LATITUD", "LONGITUD", *salida]
    if codigo:
        if not codigo.isdigit() or len(codigo) != 5:
            raise ErrorCenso("el código DIVIPOLA de un municipio tiene 5 dígitos (p. ej. 25175, Chía)")
        filas = fuentes.consultar_censo("municipio", f"MPIO_CDPMP='{codigo}'", campos)
    elif nombre:
        buscado = plano(nombre)
        filas = [f for f in fuentes._en_cache(("todos_mpios", tuple(campos)), lambda: fuentes.consultar_censo(
            "municipio", "1=1", campos)) if buscado in plano(f.get("MPIO_CNMBR") or "")]
    else:
        raise ErrorCenso("falta `nombre` o `codigo` del municipio")
    deptos = _departamentos()
    return [{"codigo": f["MPIO_CDPMP"], "municipio": f["MPIO_CNMBR"],
             "departamento": deptos.get(f["DPTO_CCDGO"], f["DPTO_CCDGO"]),
             "centroide": [f.get("LONGITUD"), f.get("LATITUD")],
             **{BASICOS_MUNICIPIO.get(k, k): f.get(k) for k in salida}} for f in filas]


def manzanas(bbox: list[float], variables: list[str], maximo: int) -> tuple[dict, dict[str, Any]]:
    """Manzanas MGN 2025 del rectángulo con las variables del CNPV 2018 unidas por código."""
    variables = validar_variables("manzana", variables)
    feats = fuentes.manzanas_mgn(bbox, maximo)
    for f in feats:  # el WFS rompe algunas claves («Ãrea»): se reparan como los nombres
        f["properties"] = {fuentes.reparar(k): v for k, v in (f.get("properties") or {}).items()}
    codigos = [str(f["properties"].get(COD_MANZANA) or "") for f in feats]
    censo: dict[str, dict] = {}
    for i in range(0, len(codigos), 300):
        lote = ",".join(f"'{c}'" for c in codigos[i:i + 300] if c.isdigit())
        if lote:
            for fila in fuentes.consultar_censo("manzana", f"COD_DANE_A IN ({lote})", ["COD_DANE_A", *variables]):
                censo[fila["COD_DANE_A"]] = fila
    salida = []
    for f, cod in zip(feats, codigos, strict=True):
        props = f["properties"]
        dato = censo.get(cod)
        nuevas = {"cod_manzana": cod, "con_dato_censo_2018": dato is not None,
                  **{v: (dato or {}).get(v) for v in variables},
                  **{corto: props.get(largo) for largo, corto in MGN_2025.items()}}
        salida.append({"type": "Feature", "geometry": f.get("geometry"), "properties": nuevas})
    return {"type": "FeatureCollection", "features": salida}, {"vinieron": len(feats), "tope": maximo}


def sumas(fc: dict, variables: list[str]) -> dict[str, Any]:
    """Sumas del área: censo 2018 (solo manzanas con dato) y MGN 2025, cada una con su fuente."""
    feats = fc["features"]
    con_dato = [f for f in feats if f["properties"]["con_dato_censo_2018"]]
    def total(campo: str, de: list) -> float | int:
        return sum(f["properties"].get(campo) or 0 for f in de)
    return {"manzanas": len(feats), "con_dato_censo_2018": len(con_dato),
            "sin_dato_censo_2018": len(feats) - len(con_dato),
            "censo_2018_suma": {v: total(v, con_dato) for v in variables},
            "mgn_2025_suma": {c: total(c, feats) for c in ("viviendas_recuento_2025", "unidades_economicas_cenu_2025")}}
