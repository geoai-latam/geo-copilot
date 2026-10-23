"""Acceso a APIs web públicas con buenas maneras (Terranova F5, G4).

- Solo dominios de la lista permitida (`WEB_MCP_DOMINIOS`), con el cliente SSRF del kit.
- `robots.txt` de cada sitio se lee una vez por hora y se respeta.
- Una petición por segundo por sitio, como mucho: es una fuente del Estado, no un banco de pruebas.
- Lectura de JSON (p. ej. la API REST de WordPress, que da el total en `X-WP-Total`) y de SDMX-ML
  2.1 (BanRep), en sus dos formas: *generic* y *structure specific*.
"""

from __future__ import annotations

import os
import threading
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from geo_mcp_kit import red

USER_AGENT = "terranova-web-mcp/1 (+https://github.com/geoai-latam/terranova)"
INTERVALO_S = 1.0
ROBOTS_TTL_S = 3600
MAX_FILAS = 5000


class ErrorWeb(ValueError):
    """La petición no se hace: dominio fuera de la lista, `robots.txt` lo prohíbe o la respuesta no sirve."""

    def __init__(self, mensaje: str, codigo: str = "parametro_invalido") -> None:
        super().__init__(mensaje)
        self.codigo = codigo


def dominios() -> list[str]:
    return [d.strip().lower() for d in os.environ.get("WEB_MCP_DOMINIOS", "").split(",") if d.strip()]


_candado = threading.Lock()
_ultima: dict[str, float] = {}
_robots: dict[str, tuple[float, RobotFileParser]] = {}


def _esperar_turno(host: str) -> None:
    with _candado:
        espera = _ultima.get(host, 0.0) + INTERVALO_S - time.monotonic()
        _ultima[host] = time.monotonic() + max(espera, 0.0)
    if espera > 0:
        time.sleep(espera)


def _permitido_por_robots(url: str) -> bool:
    partes = urlsplit(url)
    host = partes.netloc.lower()
    ahora = time.monotonic()
    guardado = _robots.get(host)
    if guardado is None or ahora - guardado[0] > ROBOTS_TTL_S:
        rp = RobotFileParser()
        try:
            r = red.get(f"{partes.scheme}://{host}/robots.txt", dominios=dominios(), timeout=20,
                        user_agent=USER_AGENT, max_bytes=500_000)
            rp.parse(r.text.splitlines())
        except red.ErrorRed:
            rp.parse([])  # sin robots.txt (404) todo está permitido
        _robots[host] = guardado = (ahora, rp)
    return guardado[1].can_fetch(USER_AGENT, url)


def pedir(url: str, params: dict[str, Any] | None) -> Any:
    """GET de una URL de la lista permitida, con `robots.txt` y turno por sitio."""
    if not dominios():
        raise ErrorWeb("web-mcp no tiene dominios permitidos (WEB_MCP_DOMINIOS)", "error_interno")
    if not urlsplit(url).scheme == "https":
        raise ErrorWeb("solo se aceptan URL https")
    if not _permitido_por_robots(url):
        raise ErrorWeb(f"el robots.txt del sitio no permite leer {url}")
    _esperar_turno(urlsplit(url).netloc.lower())
    # httpx con `params` REEMPLAZA la consulta de la URL (norma.php?i=56882 perdía su `i` y Función
    # Pública redirigía a su página de error): los parámetros de la URL se unen con los pedidos
    partes = urlsplit(url)
    unidos = {**dict(parse_qsl(partes.query, keep_blank_values=True)),
              **{k: v for k, v in (params or {}).items() if v is not None}}
    base = urlunsplit(partes._replace(query=""))
    try:
        return red.get(base, unidos or None, dominios=dominios(), timeout=90, user_agent=USER_AGENT)
    except red.UrlNoPermitida as exc:
        raise ErrorWeb(f"dominio fuera de la lista permitida: {exc}") from exc


def aplanar(valor: Any, prefijo: str = "") -> dict[str, Any]:
    """Un objeto JSON a columnas: `{"title": {"rendered": "x"}}` → `{"title.rendered": "x"}`."""
    if not isinstance(valor, dict):
        return {prefijo or "valor": valor}
    salida: dict[str, Any] = {}
    for k, v in valor.items():
        clave = f"{prefijo}.{k}" if prefijo else str(k)
        if isinstance(v, dict):
            salida.update(aplanar(v, clave))
        else:
            salida[clave] = v
    return salida


def filas_json(datos: Any, filas_en: str | None) -> list[dict[str, Any]]:
    actual = datos
    for parte in (filas_en or "").split("."):
        if parte:
            actual = actual.get(parte) if isinstance(actual, dict) else None
    if isinstance(actual, dict):
        actual = [actual]
    if not isinstance(actual, list):
        raise ErrorWeb("la respuesta no trae una lista de elementos donde se pidió (`filas_en`)",
                       "fuente_respuesta_invalida")
    return [aplanar(f) for f in actual[:MAX_FILAS]]


def total_de_cabeceras(cabeceras: Any) -> int | None:
    for nombre in ("X-WP-Total", "X-Total-Count"):
        valor = cabeceras.get(nombre)
        if valor is not None and str(valor).isdigit():
            return int(valor)
    return None


def _local(etiqueta: str) -> str:
    return etiqueta.rsplit("}", 1)[-1]


def filas_sdmx(xml: str) -> list[dict[str, Any]]:
    """Observaciones de un mensaje SDMX-ML 2.1: una fila por observación, con las dimensiones de su serie."""
    try:
        raiz = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ErrorWeb(f"la respuesta no es SDMX-ML válido ({exc})", "fuente_respuesta_invalida") from exc
    filas: list[dict[str, Any]] = []
    for serie in (e for e in raiz.iter() if _local(e.tag) == "Series"):
        clave: dict[str, Any] = dict(serie.attrib)  # structure specific: dimensiones como atributos
        for llave in (h for h in serie if _local(h.tag) == "SeriesKey"):  # generic: SeriesKey/Value
            clave.update({v.attrib["id"]: v.attrib.get("value") for v in llave if "id" in v.attrib})
        for obs in (o for o in serie if _local(o.tag) == "Obs"):
            fila = dict(clave)
            fila.update(obs.attrib)  # structure specific: TIME_PERIOD, OBS_VALUE como atributos
            for hijo in obs:  # generic: ObsDimension y ObsValue como hijos
                nombre = _local(hijo.tag)
                if nombre == "ObsDimension":
                    fila["TIME_PERIOD"] = hijo.attrib.get("value")
                elif nombre == "ObsValue":
                    fila["OBS_VALUE"] = hijo.attrib.get("value")
            filas.append(fila)
            if len(filas) >= MAX_FILAS:
                return filas
    return filas
