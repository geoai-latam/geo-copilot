"""S3.3 — hello-geo: el servidor de ejemplo cumple el contrato GeoMCP (G1)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "hello_geo"))

from hello_geo import server


def test_declara_meta_geo_y_anotaciones():
    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert set(tools) == {"hello_circle", "hello_about"}
    circ = tools["hello_circle"]
    assert circ.meta["geo"]["outputs"] == ["feature_collection"]
    assert circ.annotations.readOnlyHint is True
    assert set(tools) <= set(server.SCOPES)  # fail-closed: toda tool tiene scope


def test_circulo_devuelve_geo_result_con_area_correcta():
    r = server.hello_circle(-74.08, 4.6, 500)
    assert r["geo_result"] == "1" and r["artifacts"][0]["crs"] == "EPSG:4326"
    poly = r["artifacts"][0]["data"]["features"][0]["geometry"]
    assert poly["type"] == "Polygon" and poly["coordinates"][0][0] == poly["coordinates"][0][-1]
    assert r["facts"]["area_ha"] == pytest.approx(78.5398, rel=1e-4)
    # radio real en metros ≈ 500 (contra pyproj en UTM, independiente)
    from pyproj import Transformer
    t = Transformer.from_crs(4326, 32618, always_xy=True).transform
    cx, cy = t(-74.08, 4.6)
    x, y = t(*poly["coordinates"][0][16])
    assert ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 == pytest.approx(500, rel=5e-3)


def test_parametros_absurdos_error_honesto():
    assert "fuera de rango" in server.hello_circle(0, 0, -5)["error"]


def test_sin_claves_no_arranca(monkeypatch):
    monkeypatch.delenv("HELLO_GEO_KEYS", raising=False)
    with pytest.raises(RuntimeError, match="sin claves"):
        server.build_app()
