"""Pendiente del acta FH con LLM REAL: tras buscar en el catálogo, el agente dice si lo hallado es
lo pedido o que ninguno lo es (antes: «Encontré 7 datasets…» para la malla vial de Bogotá cuando
ninguno lo era). Se mide como tasa (mayoría en 3), en serie.

Ejecutar: pytest -m llm tests/test_llm_busqueda_juicio.py -s
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

# Lo que devolvió el catálogo en el V5 (títulos reales).
HALLADOS_VIAS = [
    {"name": "Zonas Homogéneas Gestor IGAC Vigencias 2026", "type": "FeatureServer",
     "description": "Zonas Homogéneas Físicas y Geoeconómicas de los municipios del gestor IGAC"},
    {"name": "Cartografía Básica. Municipio de Cota. Escala 1K. 2022 (Feature Layer)", "type": "FeatureServer",
     "description": "Producto cartográfico básico a escala 1:1.000: elementos altimétricos, planimétricos…"},
    {"name": "Equipamiento Cundinamarca", "type": "FeatureServer",
     "description": "Ubicación de centros educativos, centros de salud, colegios, plazas de mercado…"},
    {"name": "Cartografía Básica 10K de Cundinamarca1", "type": "FeatureServer",
     "description": "Producto cartográfico básico a escala 1:10.000"},
]
HALLADOS_SEDES = HALLADOS_VIAS + [
    {"name": "Sedes educativas. Departamento de Cundinamarca. (Capa)", "type": "FeatureServer",
     "description": "Sedes educativas oficiales y no oficiales de Cundinamarca con nombre, dirección y sector"}]


async def _juicio(query: str, hallados: list[dict]) -> str:
    from geo_copilot.orchestrator.nodes.data_agent import juzgar_busqueda

    graph = MagicMock()
    graph.llm = await get_real_llm_or_skip()
    return await juzgar_busqueda(graph, query, hallados, "BASE")


@pytest.mark.asyncio
async def test_si_ninguno_es_lo_pedido_lo_dice():
    buenas = 0
    for _ in range(3):
        texto = (await _juicio("busca la malla vial (vías, líneas) de Bogotá", HALLADOS_VIAS)).lower()
        print(f"\n[vías] {texto}")
        assert texto != "base"
        buenas += any(p in texto for p in ("ninguno", "ninguna", "no corresponde", "no son", "no parece",
                                            "no incluye", "no hay", "no se encontr", "no encontr"))
    assert buenas >= 2, "no dijo que ninguno es la malla vial"


@pytest.mark.asyncio
async def test_si_uno_es_lo_pedido_lo_senala():
    buenas = 0
    for _ in range(3):
        texto = await _juicio("sedes educativas de Cundinamarca", HALLADOS_SEDES)
        print(f"\n[sedes] {texto}")
        buenas += "sedes educativas" in texto.lower()
    assert buenas >= 2, "no señaló la capa de sedes educativas"
