"""Un bbox escrito como texto («-74.23,4.46,-74.0,4.84») es un bbox, no una referencia de capa.

V5 (ogc-mcp, 2026-10-15): el usuario dio el rectángulo en números, el LLM lo pasó como texto y el hub lo
trató como nombre de capa: lo rechazó («no es una capa de esta sesión») y el agente acabó pidiéndole al
usuario que lo dibujara.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from geo_copilot.platform.mcp.hub import _resolver_geo

EST = SimpleNamespace(geo={"inputs": {"bbox": {"accepts": ["bbox", "layer_ref"]},
                                      "aoi": {"accepts": ["geometry"]}}})


@pytest.mark.asyncio
@pytest.mark.parametrize("texto", ["-74.23,4.46,-74.0,4.84", "[-74.23, 4.46, -74.0, 4.84]", " -74.23 ,4.46,-74,4.84 "])
async def test_cuatro_numeros_son_un_bbox(texto):
    args, problema = await _resolver_geo(EST, {}, {"bbox": texto})
    assert problema is None and args["bbox"] == [-74.23, 4.46, -74.0, 4.84]


@pytest.mark.asyncio
@pytest.mark.parametrize("texto", ["-74.0,4.46,-74.23,4.84", "1,2,3", "-74,4,-73,95"])
async def test_lo_que_no_es_un_bbox_valido_sigue_siendo_una_referencia(texto):
    _, problema = await _resolver_geo(EST, {}, {"bbox": texto})
    assert "no es una capa de esta sesión" in problema  # invertido, incompleto o fuera de rango: no se adivina


@pytest.mark.asyncio
async def test_un_argumento_que_solo_acepta_geometria_no_toma_numeros():
    _, problema = await _resolver_geo(EST, {}, {"aoi": "-74.23,4.46,-74.0,4.84"})
    assert "no es una capa de esta sesión" in problema
