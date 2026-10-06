"""FH.8 con LLM REAL: tras una respuesta, 2–3 siguientes pasos útiles, cortos, en español,
anclados a lo que hay en el mapa; y ninguno inventado cuando no hay un paso claro.

Ejecutar: pytest -m llm tests/test_llm_fh8_sugerencias.py -s
"""
from __future__ import annotations

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

CAPA = {"id": "layer-1", "name": "Lotes Manzana 008510017", "kind": "vector-geojson", "geometry_type": "Polygon",
        "feature_count": 27, "fields": ["lotcodigo", "lotupredia", "area_m2"], "visible": True, "is_active": True}


@pytest.mark.asyncio
async def test_sugiere_siguientes_pasos_sobre_la_capa_que_hay():
    from geo_copilot.platform.sugerencias import sugerir

    llm = await get_real_llm_or_skip()
    s = await sugerir(llm, consulta="calcula el área de cada lote",
                      respuesta="Añadí area_m2 a los 27 lotes: de 176 a 2483 m², 22 819 m² en total.",
                      map_context={"layers": [CAPA]})
    print(f"\n[FH.8 sugerencias] {s}")
    assert 1 <= len(s) <= 3
    assert all(len(x) <= 180 for x in s)
    # ancladas: hablan de los lotes / su área, no de algo ajeno al mapa
    assert sum(any(p in x.lower() for p in ("lote", "área", "area", "m²", "manzana")) for x in s) >= len(s) - 1


@pytest.mark.asyncio
@pytest.mark.parametrize("capas", [[], [CAPA]], ids=["mapa-vacio", "con-capas"])
async def test_una_despedida_no_necesita_siguientes_pasos_inventados(capas):
    from geo_copilot.platform.sugerencias import sugerir

    llm = await get_real_llm_or_skip()
    s = await sugerir(llm, consulta="gracias, eso era todo", respuesta="¡Con gusto! Aquí estoy si necesitas algo más.",
                      map_context={"layers": capas})
    print(f"\n[FH.8 sin paso claro] {s}")
    assert len(s) <= 1


@pytest.mark.asyncio
async def test_con_puntos_no_sugiere_areas():
    """Pendiente del acta FH (V5 con sedes educativas): sugirió «el área promedio de las sedes»."""
    from geo_copilot.platform.sugerencias import sugerir

    puntos = {"id": "layer-3", "name": "Sedes educativas Cundinamarca", "kind": "vector-geojson",
              "geometry_type": "Point", "feature_count": 2000, "fields": ["nom_col", "sector", "zona", "nombre_mun"],
              "visible": True, "is_active": True}
    malas = 0
    for _ in range(3):
        s = await sugerir(llm=await get_real_llm_or_skip(), consulta="colorea las sedes educativas por sector",
                          respuesta="Coloreé las sedes por sector: OFICIAL (1941) y NO OFICIAL (59).",
                          map_context={"layers": [puntos]})
        print(f"\n[FH.8 puntos] {s}")
        malas += any(("área" in x.lower() or "area " in x.lower() or "perímetro" in x.lower()) for x in s)
    assert malas <= 1, "sugirió áreas sobre puntos"
