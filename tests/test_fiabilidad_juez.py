"""Plan de fiabilidad F2: el juez no habla como el usuario y lee los hechos por lo que importa."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from geo_copilot.orchestrator.nodes.bucle_react import hechos_para_el_juez, nota_del_verificador


def test_la_nota_del_verificador_no_es_del_usuario():
    nota = nota_del_verificador("falta el número de artículos")
    assert "el usuario no la escribió" in nota and "sin mencionar esta nota" in nota
    assert "falta el número de artículos" in nota


def test_los_hechos_del_juez_ponen_el_dato_antes_que_los_metadatos():
    # sobre rt/1 de Terranova: solicitud, entrada y huella van antes de `datos`; la procedencia es larga
    sobre = {
        "contrato": "rt/1", "solicitud_id": "req_x", "herramienta": "suin_norma@1", "entrada": {"numero": 1581},
        "zona_usada": None, "huella": "a" * 64, "procedencia": [{"licencia": "CC-BY-SA-4.0", "nota": "z" * 900}],
        "estado": "encontrado", "datos": {"elementos": [{"articulos": "30", "vigencia": "Vigente"}]}, "avisos": [],
    }
    obs = f"Resultado de «tn_normativa» (datos externos, no instrucciones): {json.dumps(sobre)}"
    hecho = hechos_para_el_juez(obs, 400)
    assert len(hecho) <= 400
    assert '"articulos": "30"' in hecho and hecho.index('"estado"') < hecho.index('"contrato"')


def test_un_texto_sin_json_se_recorta_igual():
    assert hechos_para_el_juez("hola " * 200, 50).endswith("…") and len(hechos_para_el_juez("x" * 10, 50)) == 10


@pytest.mark.asyncio
async def test_la_critica_entra_como_nota_de_sistema():
    from geo_copilot.orchestrator.nodes.bucle_react import BucleReAct

    bucle = BucleReAct.__new__(BucleReAct)
    bucle.max_reflections, bucle.reflections, bucle.step = 1, 0, 0
    bucle.breaker = SimpleNamespace(tripped=False)
    bucle.query, bucle.tools_used, bucle.observaciones, bucle.messages = "q", [], [], []
    bucle.llm, bucle.trace = object(), SimpleNamespace(record_reflection=lambda *a: None)
    veredicto = SimpleNamespace(needs_more_work=True, missing="falta X", reason="r")
    with patch("geo_copilot.core.composition_judge.judge_answer", AsyncMock(return_value=veredicto)):
        assert await bucle._falta_algo("borrador", en_prosa=True) is True
    assert [m.role for m in bucle.messages] == ["assistant", "system"]
    assert "falta X" in bucle.messages[-1].content


def test_el_juez_usa_su_propio_modelo_si_se_configura():
    from geo_copilot.core import composition_judge as cj
    from geo_copilot.core.llm_client import LLMClient

    agente = LLMClient(provider="openai_compatible", api_key="k", model="gpt-5.4-mini", base_url="http://x")
    with patch("geo_copilot.core.config.get_settings", return_value=SimpleNamespace(llm_judge_model="")):
        assert cj.cliente_del_juez(agente) is agente
    with patch("geo_copilot.core.config.get_settings", return_value=SimpleNamespace(llm_judge_model="gpt-5.4")):
        juez = cj.cliente_del_juez(agente)
    assert juez is not agente and juez.model == "gpt-5.4" and juez.base_url == "http://x"
