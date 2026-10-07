"""La observación de una capa raster de un MCP le dice al LLM que YA está en el mapa y dónde.

V5 (explorador Sentinel-2, 2026-10-07): con solo el nombre, el agente encuadró «la escena» con el
id de otra capa raster del mapa, concluyó que la suya no se había añadido y repitió la llamada.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from geo_copilot.platform.mcp.materializar import _observacion


def test_la_capa_raster_queda_dicha_como_añadida_y_con_su_bbox():
    delta = {"external_imagery": {"name": "ndwi 2026-08-02", "time": "2026-08-02",
                                  "extent": {"xmin": -71.7, "ymin": 2.62, "xmax": -71.1, "ymax": 3.62}}}
    texto = _observacion(SimpleNamespace(id="imagery"), {"facts": {}}, [], None, delta, [])
    obs = json.loads(texto[texto.index("{"):])
    assert obs["capa_raster"] == {"nombre": "ndwi 2026-08-02", "estado": "añadida al mapa del usuario en este paso",
                                  "fecha": "2026-08-02", "bbox": [-71.7, 2.62, -71.1, 3.62]}


def test_sin_extension_no_se_inventa_un_bbox():
    texto = _observacion(SimpleNamespace(id="imagery"), {"facts": {}}, [], None,
                         {"external_imagery": {"name": "x", "extent": None}}, [])
    assert '"bbox"' not in texto
