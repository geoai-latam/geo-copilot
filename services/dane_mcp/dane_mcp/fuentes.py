"""Las dos fuentes del DANE y cómo se cruzan.

Medido el 2026-10-15 en geoportal.dane.gov.co:
- CNPV 2018 integrado al MGN (REST `MARCO_INTEGRADO/Serv_DatosCNPV2018_Integrados_MGN2018`): atributos
  censales por nivel (808 manzana … 800 municipio, 335 departamento) pero la GEOMETRÍA viene suprimida
  (`geometry: null`, también en f=json). En municipio y departamento los campos llevan prefijo `S`
  (STP27_PERS = personas).
- MGN 2025 (WFS `MGN2025/Serv_CapasMGN_2025`): las geometrías, con el mismo código de manzana de 22
  dígitos, más el recuento de viviendas 2025 y las unidades económicas (CENU 2025). Su FeatureServer
  falla al consultar manzanas («Unable to perform query operation»), el WFS no.
- Cruce por código: Chapinero 760 de 763 manzanas 2025 con dato 2018 (99,6 %); borde de Soacha 794 de
  812 (97,8 %). Las que no cruzan son nuevas o recodificadas: se cuentan, no se inventan.
- Los nombres llegan con la codificación rota (UTF-8 leído como Latin-1: «CHÃ\\x8dA»): se reparan.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from geo_mcp_kit import red

CENSO = ("https://geoportal.dane.gov.co/mparcgis/rest/services/MARCO_INTEGRADO/"
         "Serv_DatosCNPV2018_Integrados_MGN2018/MapServer")
MGN_WFS = "https://geoportal.dane.gov.co/mparcgis/services/MGN2025/Serv_CapasMGN_2025/MapServer/WFSServer"
CAPAS_CENSO = {"manzana": 808, "seccion_urbana": 807, "sector_urbano": 806, "seccion_rural": 804,
               "sector_rural": 803, "zona_urbana": 802, "clase": 801, "municipio": 800, "departamento": 335}
DOMINIOS = ["geoportal.dane.gov.co"]
USER_AGENT = "geo-copilot-dane-mcp/1"
_cache: dict[tuple, tuple[float, Any]] = {}
_cerrojo = threading.Lock()
TTL_S = 6 * 3600


def reparar(texto: Any) -> Any:
    """«CHÃ\\x8dA» → «CHÍA» (UTF-8 que el servidor entrega como Latin-1); lo que no es así queda igual."""
    if not isinstance(texto, str):
        return texto
    try:
        return texto.encode("latin-1", errors="surrogateescape").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return texto


def _get(url: str, params: dict) -> Any:
    return red.get(url, params, dominios=DOMINIOS, timeout=150, user_agent=USER_AGENT, max_bytes=80_000_000).json()


def _en_cache(clave: tuple, producir) -> Any:
    with _cerrojo:
        if clave in _cache and time.monotonic() - _cache[clave][0] < TTL_S:
            return _cache[clave][1]
    valor = producir()
    with _cerrojo:
        _cache[clave] = (time.monotonic(), valor)
    return valor


def campos(nivel: str) -> list[dict[str, str]]:
    """Los campos del nivel censal con la descripción del DANE (alias del servicio)."""
    def producir() -> list[dict[str, str]]:
        meta = _get(f"{CENSO}/{CAPAS_CENSO[nivel]}", {"f": "json"})
        return [{"campo": f["name"], "descripcion": reparar(f.get("alias") or f["name"]).strip()}
                for f in meta.get("fields") or [] if f.get("name") != "OBJECTID"]

    return _en_cache(("campos", nivel), producir)


def consultar_censo(nivel: str, where: str, campos_salida: list[str]) -> list[dict[str, Any]]:
    """Filas (atributos) del CNPV integrado; POST porque los `IN (…)` de códigos son largos."""
    filas: list[dict] = []
    while True:
        r = red.post(f"{CENSO}/{CAPAS_CENSO[nivel]}/query", {
            "where": where, "outFields": ",".join(campos_salida), "returnGeometry": "false", "f": "json",
            "resultOffset": len(filas), "resultRecordCount": 2000}, dominios=DOMINIOS, timeout=150,
            user_agent=USER_AGENT).json()
        if "error" in r:
            raise red.ErrorRed(
                f"el DANE rechazó la consulta: {r['error'].get('message')}",
                "fuente_respuesta_invalida",
            )
        pagina = [{k: reparar(v) for k, v in f["attributes"].items()} for f in r.get("features") or []]
        filas += pagina
        if not r.get("exceededTransferLimit") or not pagina:
            return filas


def manzanas_mgn(bbox: list[float], maximo: int) -> list[dict]:
    """Manzanas MGN 2025 con geometría (WFS) en un rectángulo [minx, miny, maxx, maxy] EPSG:4326."""
    feats: list[dict] = []
    while len(feats) < maximo:
        n = min(2000, maximo - len(feats))
        fc = _get(MGN_WFS, {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                            "typeNames": "Serv_CapasMGN_2025:Manzana", "outputFormat": "GEOJSON",
                            "srsName": "EPSG:4326", "count": n, "startIndex": len(feats),
                            # WFS 2.0 + urn EPSG:4326 = lat/lon (medido en ogc-mcp)
                            "bbox": f"{bbox[1]},{bbox[0]},{bbox[3]},{bbox[2]},urn:ogc:def:crs:EPSG::4326"})
        pagina = fc.get("features") or []
        feats += pagina
        if len(pagina) < n:
            break
    return feats


def municipio_mgn(codigo: str, lon: float, lat: float) -> dict | None:
    """El límite MGN 2025 de un municipio: se busca alrededor de su centroide y se elige por CÓDIGO."""
    margen = 1.2
    fc = _get(MGN_WFS, {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
                        "typeNames": "Serv_CapasMGN_2025:Municipio", "outputFormat": "GEOJSON", "srsName": "EPSG:4326",
                        "bbox": f"{lat - margen},{lon - margen},{lat + margen},{lon + margen},urn:ogc:def:crs:EPSG::4326"})
    for f in fc.get("features") or []:
        props = f.get("properties") or {}
        if any(str(v) == codigo for k, v in props.items() if "concatenado" in k.lower() or k.endswith("CDPMP")):
            return f
    return None
