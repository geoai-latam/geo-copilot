"""El texto de respaldo del responder cuando no hay nada que narrar.

F2.3: una consulta que dio 0 filas (y el juez de vacíos lo anotó) se reporta honesta
(«no encontré…»), no con el genérico «Consulta procesada.» que ocultaba que la respuesta
real era «no hay». El genérico queda solo cuando no hubo tal veredicto.
"""

from geo_copilot.orchestrator.nodes.responder import _vacio_honesto


def test_sin_veredicto_vacio_cae_al_generico():
    assert _vacio_honesto({}) == "Consulta procesada."
    assert _vacio_honesto({"empty_result_verdict": None}) == "Consulta procesada."


def test_cero_filas_plausiblemente_real_se_dice_honesto():
    estado = {"empty_result_verdict": {"verdict": "plausibly_real", "confidence": 0.9, "reason": "x"}}
    assert _vacio_honesto(estado) == "No encontré resultados para esa consulta."


def test_cero_filas_posible_bug_lo_dice_sin_inventar():
    estado = {"empty_result_verdict": {"verdict": "likely_bug", "confidence": 0.8, "reason": "x"}}
    msg = _vacio_honesto(estado)
    assert "no devolvió resultados" in msg and "problema" in msg
    assert msg != "Consulta procesada."
