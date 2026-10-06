"""F2.1: SpatialReasoner — CRS/UTM dinámico desde bbox (cualquier país).

VALIDACIÓN CRÍTICA: un cálculo de área en otro país NO sale en silencio
con la zona de Colombia; multi-zona/bbox inválido se declaran honestamente.
"""

import pytest

from geo_copilot.core.spatial import (
    SpatialReasoner,
    utm_epsg,
    utm_epsg_for_bbox,
    utm_zone,
)


@pytest.mark.parametrize("lon,expected_zone", [
    (-74.0, 18),   # Bogotá
    (-77.0, 18),   # Lima
    (-99.0, 14),   # CDMX
    (2.3, 31),     # París
    (139.7, 54),   # Tokio
])
def test_utm_zone_global(lon, expected_zone):
    assert utm_zone(lon) == expected_zone


def test_utm_epsg_north_south():
    assert utm_epsg(-74.0, 4.6) == 32618    # Colombia, Norte → 326xx
    assert utm_epsg(-77.0, -12.0) == 32718  # Perú, Sur → 327xx
    assert utm_epsg(-99.0, 19.4) == 32614   # México, Norte


def test_bbox_single_zone():
    epsg, note = utm_epsg_for_bbox([-74.1, 4.5, -74.0, 4.7])  # Bogotá
    assert epsg == 32618
    assert "METROS" in note


def test_bbox_other_country_not_colombia():
    # CRÍTICO: México NO debe devolver la zona de Colombia (32618).
    epsg, _ = utm_epsg_for_bbox([-99.2, 19.3, -99.0, 19.5])
    assert epsg == 32614
    assert epsg != 32618


def test_bbox_multi_zone_declared_unsupported():
    # bbox que cruza zonas UTM → None + nota honesta (no inventa una zona).
    epsg, note = utm_epsg_for_bbox([-80.0, 0.0, -60.0, 5.0])  # cruza varias zonas
    assert epsg is None
    assert "cruza zonas UTM" in note


def test_bbox_invalid():
    assert utm_epsg_for_bbox(None)[0] is None
    assert utm_epsg_for_bbox([0, 0, 0])[0] is None          # len != 4
    assert utm_epsg_for_bbox([200, 0, 210, 5])[0] is None   # fuera de rango


def test_reasoner_metric_reprojects():
    r = SpatialReasoner.recommend([-74.1, 4.5, -74.0, 4.7], "area")
    assert r["supported"] and r["metric"] and r["target_srid"] == 32618


def test_reasoner_topology_keeps_4326():
    r = SpatialReasoner.recommend([-74.1, 4.5, -74.0, 4.7], "intersects")
    assert r["target_srid"] == 4326 and r["metric"] is False


def test_reasoner_metric_unsupported_declares_honestly():
    r = SpatialReasoner.recommend([-80.0, 0.0, -60.0, 5.0], "area")  # multi-zona
    assert r["supported"] is False and r["target_srid"] is None


def test_format_for_prompt_other_country():
    block = SpatialReasoner.format_for_prompt([-99.2, 19.3, -99.0, 19.5])
    assert "32614" in block and "32618" not in block
