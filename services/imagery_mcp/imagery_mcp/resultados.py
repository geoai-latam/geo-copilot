"""Capas grandes por referencia: el GeoJSON queda en disco y el núcleo lo descarga de
`/resultados/{id}.geojson` con su credencial (como arcgis-mcp). La cuadrícula del mundo son
~29.000 polígonos: en la respuesta MCP iría dos veces (texto y estructura) y pasaría del tope.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid

from geo_mcp_kit import respond_json

TTL_S = 3600
DIR = os.environ.get("IMAGERY_RESULTADOS_DIR") or os.path.join(tempfile.gettempdir(), "imagery-resultados")
_RUTA = re.compile(r"^/resultados/([0-9a-f]{32})\.geojson$")
_candado = threading.Lock()


def guardar(fc: dict) -> str:
    """Guarda la capa y devuelve su ruta en este servicio."""
    os.makedirs(DIR, exist_ok=True)
    _purgar()
    ident = uuid.uuid4().hex
    with open(os.path.join(DIR, f"{ident}.geojson"), "w", encoding="utf-8") as fh:
        json.dump(fc, fh, ensure_ascii=False, separators=(",", ":"))
    return f"/resultados/{ident}.geojson"


def _purgar() -> None:
    """Fuera lo más viejo que el TTL (el núcleo lo descarga al momento)."""
    with _candado:
        limite = time.time() - TTL_S
        for nombre in os.listdir(DIR):
            ruta = os.path.join(DIR, nombre)
            try:
                if os.path.getmtime(ruta) < limite:
                    os.remove(ruta)
            except OSError:
                continue


def parse_ruta(path: str) -> str | None:
    m = _RUTA.match(path)
    return m.group(1) if m else None


async def servir(scope, send, ident, _key):
    ruta = os.path.join(DIR, f"{ident}.geojson")
    if not os.path.isfile(ruta):
        await respond_json(send, 404, {"error": "resultado no encontrado o vencido; repite la consulta"})
        return
    with open(ruta, "rb") as fh:
        datos = fh.read()
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"application/geo+json"),
                            (b"content-length", str(len(datos)).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": datos})
