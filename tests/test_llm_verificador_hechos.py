"""Plan de fiabilidad F1: el verificador de hechos con el LLM real.

La respuesta directa del router se revisa: charla, conceptos y negativas por reglas NO afirman hechos
(siguen directas); dar, negar o esquivar un dato pedido SÍ (van al bucle a consultar). Medido 21/21
(×3) con gpt-5.4-mini el 2026-10-24.
"""

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

CASOS = [
    ("Hola, ¿qué puedes hacer?", "Puedo consultar datos geográficos, cargar capas y analizarlas.", False),
    ("¿Qué es un buffer en análisis espacial?", "Un buffer es el área a cierta distancia de una geometría.", False),
    ("Gracias", "¡Con gusto!", False),
    ("Borra la tabla de lotes", "No puedo: la plataforma accede a la base en solo lectura.", False),
    ("¿Puedo usar Open-Meteo comercialmente?", "Sí, en general permite uso comercial.", True),
    ("¿Cuánto llovió en Bogotá en enero de 2023?", "No tengo una fuente climática conectada.", True),
    ("¿Cuántos habitantes tiene Chía?", "Chía tiene unos 150 000 habitantes.", True),
    ("¿Cuánto llovió en Bogotá en enero de 2023?", "Puedo ayudarte con análisis geoespaciales y capas.", True),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("pregunta", "respuesta", "esperado"), CASOS)
async def test_verificador_de_hechos(pregunta, respuesta, esperado):
    from geo_copilot.core.verificador_hechos import afirma_hechos

    llm = await get_real_llm_or_skip()
    assert await afirma_hechos(llm, pregunta, respuesta) is esperado
