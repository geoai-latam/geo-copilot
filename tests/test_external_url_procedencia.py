"""R0.9 (auditoría 2026-07-26, AUD-16) — procedencia de la URL externa.

`external_url` lo pone el LLM del router desde su structured output, SIN
validar, y llegaba a un GET server-side sin allowlist de dominio ni HITL. Eso
cerraba un ciclo de inyección indirecta: un Feature Service público con
instrucciones en su `title` entra al system prompt vía `found_services`, el LLM
emite la URL del atacante, y la respuesta vuelve al prompt del turno siguiente.
"""

from __future__ import annotations

import pytest

from geo_copilot.orchestrator.nodes.data_agent import _external_url_con_procedencia

ATACANTE = "https://cdn-geodata-mirror.example/predios/FeatureServer/0"
LEGITIMA = "https://services1.arcgis.com/abc/arcgis/rest/services/Lotes/FeatureServer/0"


class TestProcedenciaDeUrlExterna:
    def test_url_inventada_por_el_llm_se_descarta(self):
        """El escenario exacto de AUD-16: el modelo propone un host que el
        usuario nunca vio ni escribió."""
        state = {"external_url": ATACANTE, "found_services": []}
        assert _external_url_con_procedencia(state, "busca predios de Zipaquirá") is None

    def test_url_escrita_por_el_usuario_se_acepta(self):
        state = {}
        assert _external_url_con_procedencia(state, f"carga {LEGITIMA}") == LEGITIMA

    def test_url_de_un_servicio_mostrado_se_acepta(self):
        state = {
            "external_url": LEGITIMA,
            "found_services": [{"name": "Lotes", "url": LEGITIMA}],
        }
        assert _external_url_con_procedencia(state, "carga el 2") == LEGITIMA

    @pytest.mark.parametrize("variante", [LEGITIMA + "/", LEGITIMA.upper()])
    def test_la_comparacion_es_canonica(self, variante):
        state = {
            "external_url": variante,
            "found_services": [{"name": "Lotes", "url": LEGITIMA}],
        }
        assert _external_url_con_procedencia(state, "carga el 2") == variante

    def test_el_usuario_manda_sobre_la_propuesta_del_modelo(self):
        """Si el usuario escribió una URL, se usa ESA, no la del modelo."""
        state = {"external_url": ATACANTE, "found_services": []}
        assert _external_url_con_procedencia(state, f"carga {LEGITIMA}") == LEGITIMA

    def test_sin_url_no_inventa_nada(self):
        assert _external_url_con_procedencia({}, "cuántos lotes hay") is None
