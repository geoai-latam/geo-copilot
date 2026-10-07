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


def _cfg(trust: str):
    return SimpleNamespace(id="imagery", trust=trust, tiles=SimpleNamespace(prefixes=["/tiles-rgb/"]))


def _prov():
    return SimpleNamespace(model_dump=lambda mode=None: {"capability": "mcp.imagery.imagery_scene_view"})


_COG = {"version": 1, "tipo": "rgb8", "bandas": [{"url": "https://bucket.s3.test/TCI.tif"}]}
_ART = {"kind": "raster_tiles", "tiles": "/tiles-rgb/S2X/true_color/{z}/{x}/{y}.png", "bounds": [0, 0, 1, 1], "cog": _COG}


def test_el_cog_llega_a_la_capa_solo_desde_un_servidor_de_confianza_y_por_https():
    from geo_copilot.platform.mcp.materializar import _teselas

    delta: dict = {}
    _teselas(_cfg("trusted"), _ART, "color", _prov(), delta, [])
    assert delta["external_imagery"]["cog"] == _COG

    delta, avisos = {}, []
    _teselas(_cfg("untrusted"), _ART, "color", _prov(), delta, avisos)
    assert "cog" not in delta["external_imagery"] and avisos                # pinta con sus teselas

    delta = {}
    inseguro = {**_ART, "cog": {**_COG, "bandas": [{"url": "http://intranet/x.tif"}]}}
    _teselas(_cfg("trusted"), inseguro, "color", _prov(), delta, [])
    assert "cog" not in delta["external_imagery"]


def test_la_capa_del_contrato_lleva_su_cog():
    from geo_copilot.platform.artefactos import _capa_raster

    ref = _capa_raster({"service_url": "/api/v1/proxy/mcp/imagery/tiles-rgb/S2X/true_color/{z}/{x}/{y}.png",
                        "name": "color", "extent": {"xmin": 0, "ymin": 0, "xmax": 1, "ymax": 1}, "cog": _COG})
    assert ref.layer.storage.cog == _COG
