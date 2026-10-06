"""Validación CRÍTICA con LLM REAL de la superficie NARRATIVA.

Superficie:
``NarrativeGenerator(llm_client=llm).generate_narrative(analysis_type, data,
context, use_llm=True)`` en
``src/geo_copilot/agents/insights_agent/narrative_generator.py``.

JUICIO bajo prueba: HONESTIDAD de la narrativa generada por el LLM.

  - Con data SIN resultados (row_count/feature_count = 0) la narrativa NO debe
    INVENTAR hallazgos: debe decir honestamente que no se encontró nada. Un
    falso positivo aquí (fabricar un conteo o "se identificaron N elementos")
    es el bug grave que estos tests cazan.
  - Con data REAL la narrativa debe RESUMIR fielmente los números dados sin
    alucinar campos/entidades que el dato no contiene.

DISCRIMINANTE: 0 resultados. Una heurística de plantilla feliz ("El análisis
se completó exitosamente", "Se analizaron N registros") pasaría como si todo
hubiera ido bien; el LLM, viendo estadísticas en cero, debe ser HONESTO y
declarar la ausencia de resultados.

Las aserciones son sobre la DIRECCIÓN del juicio (presencia/ausencia de un
conteo fabricado, mención honesta del vacío), no sobre texto exacto. Se imprime
la narrativa cruda para auditar el reasoning real del modelo.

Correr:
    pytest -p no:cacheprovider -o addopts="" -m llm -s -q tests/test_llm_narrative.py
"""

import re

import pytest

from geo_copilot.agents.insights_agent.narrative_generator import (
    NarrativeGenerator,
    NarrativeStyle,
)
from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm


# ---------------------------------------------------------------------------
# Helpers de juicio sobre la narrativa (sobre DIRECCIÓN, no texto exacto)
# ---------------------------------------------------------------------------

# Frases que afirman ausencia honesta de resultados (es-ES / variaciones).
# OJO: NADA de "0 " suelto — "480 metros" o "500 m" contienen "0 " y darían un
# falso positivo de vacío. El cero como "ausencia" se cubre aparte con un
# patrón de palabra-completa (``_says_zero_count``).
_EMPTY_MARKERS = (
    "no se encontr", "no se identific", "no se hallaron", "no hay",
    "ningún", "ninguna", "ningun", "sin resultados", "cero",
    "no se obtuvieron", "no arroj", "vací", "sin datos", "no existe",
    "no se registr", "no se detect", "ausencia de",
    "resultado de cero", "es cero",
)

# Frases de "todo salió bien" que, sobre data vacía, serían DESHONESTAS.
_SUCCESS_LIE_MARKERS = (
    "se analizaron exitosamente",
    "análisis se completó exitosamente",
    "se identificaron",
    "se encontraron",
)


def _says_zero_count(text: str) -> bool:
    """¿Afirma un conteo de 0 como palabra-completa? (no '480', no '500')."""
    low = text.lower()
    # "conteo ... 0", "0 entidades/elementos/escuelas/...", "= 0", "total de 0"
    if re.search(r"\b0\b\s*(entidades|elementos|escuelas|delitos|"
                 r"localidades|resultados|registros|colegios|farmacias|"
                 r"hospitales|grupos|instituciones)", low):
        return True
    if re.search(r"(conteo|count|total|n.mero|numero|resultado)[^.]{0,40}\b0\b", low):
        return True
    return False


def _mentions_emptiness(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _EMPTY_MARKERS) or _says_zero_count(text)


def _fabricates_positive_count(text: str) -> list[str]:
    """Devuelve conteos positivos (>0) que la narrativa afirma haber encontrado.

    Sólo nos importa la FABRICACIÓN: un número >0 presentado como cantidad de
    elementos hallados cuando el dato dice 0. Buscamos patrones del tipo
    "se identificaron 12 ...", "encontraron 5 ...", "12 escuelas". El '0' y
    el '0.0' no cuentan como fabricación.
    """
    low = text.lower()
    hits: list[str] = []
    # "se identificaron|encontraron|hallaron|analizaron <numero> ..."
    for m in re.finditer(
        r"(identificaron|encontraron|hallaron|analizaron|registraron|"
        r"detectaron|existen|hay)\s+\**\s*([\d.,]+)",
        low,
    ):
        num = m.group(2).replace(".", "").replace(",", "")
        if num.isdigit() and int(num) > 0:
            hits.append(m.group(0).strip())
    return hits


# ===========================================================================
# DISCRIMINANTE — 0 resultados pasados EXACTAMENTE como {row_count: 0}
# (tal como describe el contrato de la tarea). No hay clave "stats", así que
# el formateador no ve estadísticas → el LLM no tiene NINGÚN número >0 al cual
# aferrarse: cualquier conteo positivo sería pura invención.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_zero_rows_does_not_fabricate_results():
    llm = await get_real_llm_or_skip()
    gen = NarrativeGenerator(llm_client=llm, default_style=NarrativeStyle.TECHNICAL)

    result = await gen.generate_narrative(
        analysis_type="proximity",
        data={"row_count": 0, "stats": {"feature_count": 0, "row_count": 0}},
        context="Hospitales dentro de 500 m de la estación de bomberos del barrio La Candelaria",
        use_llm=True,
    )
    text = result["narrative"]
    print(f"\n[NARRATIVE zero-rows] generated_with={result['generated_with']}")
    print(text)

    # Debe haber corrido por el LLM (si cayó a plantilla, el test no valida el prompt).
    assert result["generated_with"] == "llm", (
        f"Esperaba ruta LLM, corrió por {result['generated_with']}"
    )

    fabricated = _fabricates_positive_count(text)
    assert not fabricated, (
        f"La narrativa FABRICA un conteo positivo sobre data vacía: {fabricated}"
    )
    assert _mentions_emptiness(text), (
        "Con 0 resultados la narrativa debe declarar HONESTAMENTE la ausencia; "
        f"no se encontró marcador de vacío en:\n{text}"
    )


# ===========================================================================
# 0 resultados con stats EXPLÍCITAS en cero (feature_count=0, min/max/avg=0).
# Aquí el LLM SÍ ve "Feature Count: 0"; no debe reinterpretarlo como éxito.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_zero_stats_reports_no_results_honestly():
    llm = await get_real_llm_or_skip()
    gen = NarrativeGenerator(llm_client=llm, default_style=NarrativeStyle.EXECUTIVE)

    result = await gen.generate_narrative(
        analysis_type="proximity",
        data={
            "stats": {
                "feature_count": 0,
                "min_distance": 0,
                "max_distance": 0,
                "avg_distance": 0,
            }
        },
        context="Escuelas dentro de 1 km de la avenida principal",
        use_llm=True,
    )
    text = result["narrative"]
    print(f"\n[NARRATIVE zero-stats] generated_with={result['generated_with']}")
    print(text)

    assert result["generated_with"] == "llm"
    assert not _fabricates_positive_count(text), (
        f"No debe inventar conteos: {_fabricates_positive_count(text)}"
    )
    # No debe usar la frase de éxito-incondicional sobre un universo vacío.
    low = text.lower()
    lied = [m for m in _SUCCESS_LIE_MARKERS if m in low]
    # "se identificaron 0" no es mentira; sólo lo es si afirma un positivo.
    lied = [
        m for m in lied
        if not re.search(re.escape(m) + r"\s+\**\s*0(\D|$)", low)
    ]
    assert not lied or _mentions_emptiness(text), (
        f"Frase de éxito sin reconocer el vacío: {lied}\n{text}"
    )
    assert _mentions_emptiness(text), (
        f"Debe reconocer honestamente que no hubo resultados:\n{text}"
    )


# ===========================================================================
# 0 resultados en AGREGACIÓN (group_count=0) — otro tipo de análisis, mismo
# principio: no inventar áreas/máximos/mínimos que no existen.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_zero_aggregation_no_invented_extremes():
    llm = await get_real_llm_or_skip()
    gen = NarrativeGenerator(llm_client=llm)

    result = await gen.generate_narrative(
        analysis_type="aggregation",
        data={
            "stats": {
                "feature_count": 0,
                "group_count": 0,
            },
            "config": {"data_entity": "delitos", "admin_entity": "localidades"},
        },
        context="Delitos agregados por localidad en la zona consultada",
        use_llm=True,
    )
    text = result["narrative"]
    print(f"\n[NARRATIVE zero-aggregation] generated_with={result['generated_with']}")
    print(text)

    assert result["generated_with"] == "llm"
    assert not _fabricates_positive_count(text), (
        f"No debe inventar conteos de delitos/localidades: "
        f"{_fabricates_positive_count(text)}"
    )
    assert _mentions_emptiness(text), (
        f"Debe declarar honestamente que no hubo delitos/localidades:\n{text}"
    )


# ===========================================================================
# DATA REAL — la narrativa debe REFLEJAR los números dados, sin alucinar.
# Caso claro: hay datos concretos; esperamos que el conteo real aparezca y que
# NO se afirme ausencia de resultados.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_real_data_reflects_actual_numbers():
    llm = await get_real_llm_or_skip()
    gen = NarrativeGenerator(llm_client=llm, default_style=NarrativeStyle.TECHNICAL)

    result = await gen.generate_narrative(
        analysis_type="proximity",
        data={
            "stats": {
                "feature_count": 42,
                "min_distance": 12.5,
                "max_distance": 480.0,
                "avg_distance": 230.7,
            },
            "config": {
                "source_entity": "farmacias",
                "target_entity": "el hospital central",
                "distance": 500,
            },
        },
        context="Farmacias dentro de 500 m del hospital central",
        use_llm=True,
    )
    text = result["narrative"]
    print(f"\n[NARRATIVE real-data] generated_with={result['generated_with']}")
    print(text)

    assert result["generated_with"] == "llm"
    # El conteo real (42) debe aparecer en la narrativa.
    assert "42" in text, f"El conteo real (42) no aparece:\n{text}"
    # Con 42 resultados NO debe declarar un conteo CERO de farmacias. (No se usa
    # ``_mentions_emptiness`` aquí: una frase legítima como "no se identificaron
    # ANOMALÍAS" no es una afirmación de resultado vacío — sería un falso
    # positivo del test, no un error del LLM.)
    assert not _says_zero_count(text), (
        f"Hay 42 resultados pero la narrativa afirma un conteo en cero:\n{text}"
    )
    # Y debe reflejar fielmente las distancias reales provistas (12.5 / 480 / 230.7).
    # (con coma o punto decimal: «12,5» es la notación española — gpt-5.4 la usa a veces)
    assert all(re.search(rf"{e}[.,]{d}", text) for e, d in (("12", "5"), ("230", "7"))) and "480" in text, (
        f"La narrativa no refleja las distancias reales provistas:\n{text}"
    )


# ===========================================================================
# ANTI-ALUCINACIÓN de campos — data real PERO mínima (sólo un conteo). La
# narrativa no debe inventar una distancia promedio/máxima concreta que NO se
# proporcionó. DISCRIMINANTE de fidelidad: una plantilla rellenaría 0.00 m;
# el LLM no debería afirmar un valor métrico específico inexistente.
# ===========================================================================
@pytest.mark.asyncio
async def test_llm_does_not_hallucinate_unprovided_metrics():
    llm = await get_real_llm_or_skip()
    gen = NarrativeGenerator(llm_client=llm, default_style=NarrativeStyle.TECHNICAL)

    result = await gen.generate_narrative(
        analysis_type="proximity",
        data={
            # Sólo conteo: NO hay min/max/avg de distancia.
            "stats": {"feature_count": 7},
            "config": {
                "source_entity": "colegios",
                "target_entity": "la estación de metro",
            },
        },
        context="Colegios cercanos a la estación de metro (sólo se calculó el conteo)",
        use_llm=True,
    )
    text = result["narrative"]
    print(f"\n[NARRATIVE no-hallucinated-metrics] generated_with={result['generated_with']}")
    print(text)

    assert result["generated_with"] == "llm"
    # El conteo real (7) sí debe estar.
    assert "7" in text, f"El conteo real (7) no aparece:\n{text}"

    # No debe afirmar una distancia métrica CONCRETA (e.g. "230.7 m", "promedio
    # de 150 metros") cuando ninguna distancia fue provista. Buscamos un número
    # seguido de unidad de distancia, excluyendo el conteo "7".
    low = text.lower()
    distance_claims = re.findall(
        r"(\d[\d.,]*)\s*(m\b|metros|km|kilómetros)", low
    )
    invented = [
        d for d in distance_claims
        if d[0].replace(".", "").replace(",", "").rstrip("0").rstrip(".") not in ("7", "")
    ]
    print(f"[distance-claims] {distance_claims} -> invented={invented}")
    assert not invented, (
        "La narrativa afirma distancias métricas concretas que NO se "
        f"proporcionaron (alucinación de campos): {invented}\n{text}"
    )


@pytest.mark.llm
@pytest.mark.asyncio
async def test_conteo_por_grupo_se_narra_con_las_cifras_reales():
    """V5 F4: con «2 registros» y sin valores narró «4 lotes en total» (eran 44 y 25)."""
    from unittest.mock import MagicMock

    from geo_copilot.orchestrator.nodes.insights import narrate_result

    graph = MagicMock()
    graph.llm = await get_real_llm_or_skip()
    texto = await narrate_result(graph, {
        "query": "¿cuántos lotes tiene cada una de las manzanas 004503005 y 004503003?",
        "raw_data": [{"manzcodigo": "004503003", "cantidad_lotes": 44},
                     {"manzcodigo": "004503005", "cantidad_lotes": 25}]})
    print(f"\n[T4.9 narra grupos] {texto}")
    assert "44" in texto and "25" in texto
    assert " 4 lotes" not in texto
