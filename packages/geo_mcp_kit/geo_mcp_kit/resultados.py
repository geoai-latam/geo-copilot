"""Capas grandes por referencia: el GeoJSON se escribe en disco y el núcleo lo descarga del propio servidor.

Lo repetían arcgis-mcp y ogc-mcp (lo grande no cabe en la respuesta MCP: 1882 polígonos del RUNAP =
110 MB). `Resultados.capa(nombre, fc, hechos)` devuelve el `structuredContent` con la capa en línea o como
`feature_ref` a `/resultados/<id>.geojson`; `Resultados.ruta(requires_tool)` es la ruta HTTP que la sirve.
Caducan a la hora (el núcleo los descarga al momento).
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from typing import Any

from geo_mcp_kit.asgi import ExtraRoute, respond_json
from geo_mcp_kit.geo import feature_collection, feature_ref, geo_result

_RE = re.compile(r"^/resultados/([0-9a-f]{32})\.geojson$")


class Resultados:
    def __init__(self, directorio: str, *, max_en_linea: int = 5000, max_bytes_en_linea: int = 8_000_000,
                 ttl_s: int = 3600) -> None:
        self.directorio = directorio
        self.max_en_linea = max_en_linea
        self.max_bytes_en_linea = max_bytes_en_linea
        self.ttl_s = ttl_s
        self._candado = threading.Lock()

    def capa(self, nombre: str, fc: dict, hechos: dict[str, Any], *, crs: str = "EPSG:4326") -> dict[str, Any]:
        """La capa en la respuesta o, si es grande (por número o por peso), como GeoJSON por referencia.

        `crs`: el de las coordenadas tal como vienen (el núcleo las lleva a 4326 al materializar)."""
        texto = json.dumps(fc, ensure_ascii=False, separators=(",", ":"))
        if len(fc["features"]) <= self.max_en_linea and len(texto) <= self.max_bytes_en_linea:
            return geo_result([feature_collection(nombre, fc, crs=crs)], facts=hechos)
        os.makedirs(self.directorio, exist_ok=True)
        self.purgar()
        ident = uuid.uuid4().hex
        with open(os.path.join(self.directorio, f"{ident}.geojson"), "w", encoding="utf-8") as fh:
            fh.write(texto)
        return geo_result([feature_ref(nombre, f"/resultados/{ident}.geojson", fmt="geojson", crs=crs,
                                       feature_count=len(fc["features"]))], facts=hechos)

    def purgar(self) -> None:
        with self._candado:
            limite = time.time() - self.ttl_s
            for nombre in os.listdir(self.directorio):
                ruta = os.path.join(self.directorio, nombre)
                try:
                    if os.path.getmtime(ruta) < limite:
                        os.remove(ruta)
                except OSError:
                    continue

    def ruta(self, requires_tool: str) -> ExtraRoute:
        """La ruta HTTP `/resultados/<id>.geojson` (hereda el scope de `requires_tool`)."""
        async def servir(scope, send, ident, _key):
            ruta = os.path.join(self.directorio, f"{ident}.geojson")
            if not os.path.isfile(ruta):
                await respond_json(send, 404, {"error": "resultado no encontrado o vencido; repite la consulta"})
                return
            with open(ruta, "rb") as fh:
                datos = fh.read()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/geo+json"),
                                    (b"content-length", str(len(datos)).encode()), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": datos})

        return ExtraRoute(lambda p: (m.group(1) if (m := _RE.match(p)) else None), servir,
                          requires_tool=requires_tool, weight=0.2)
