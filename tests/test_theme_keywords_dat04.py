"""DAT-04: los términos temáticos vienen del LLM y el bonus de ranking acredita
acrónimos institucionales cortos (SGC, PNN, río) que la heurística previa
(len>=4) descartaba — sin casar fragmentos ("río" dentro de "prioridad").

El juicio de CUÁL es el tema lo hace el LLM (discovery._llm_build_hub_plan
devuelve theme_keywords en el plan). Aquí probamos el efecto observable en el
ranking: _theme_match_bonus premia esos acrónimos cortos con precisión.
"""

from __future__ import annotations

import pytest

from geo_copilot.agents.data_agent.hub_items import (
    HubItem,
    _theme_match_bonus,
)


def _item(title: str, description: str = "", tags: list[str] | None = None) -> HubItem:
    return HubItem(
        id="x",
        source="hub",
        org="Org",
        title=title,
        description=description,
        service_type="FeatureServer",
        service_url="https://example.com/FeatureServer/0",
        tags=tags or [],
    )


def test_acronimo_corto_en_titulo_recibe_bonus():
    """'SGC' (3 chars) en el título AHORA suma (antes: descartado por len<4)."""
    item = _item("Amenaza Sísmica SGC")
    assert _theme_match_bonus(item, ["SGC"]) > 0


def test_rio_tres_chars_casa_palabra_completa():
    """'río' (3 chars) casa como palabra en 'Río Magdalena'."""
    item = _item("Estaciones del Río Magdalena")
    assert _theme_match_bonus(item, ["río"]) > 0


def test_termino_corto_no_casa_fragmento():
    """'río' NO debe casar dentro de 'prioridades' (fragmento) — precisión."""
    item = _item("Plan de Prioridades Municipales")
    assert _theme_match_bonus(item, ["río"]) == 0.0


def test_termino_largo_sigue_con_match_flexible_plural():
    """No-regresión: términos largos mantienen el match plural↔singular."""
    item = _item("Directorio de Hospitales de Bogotá")
    assert _theme_match_bonus(item, ["hospital"]) > 0


def test_sin_keywords_no_da_bonus():
    item = _item("Cualquier cosa")
    assert _theme_match_bonus(item, []) == 0.0
    assert _theme_match_bonus(item, None) == 0.0


def test_dos_chars_se_ignoran_como_ruido():
    """Términos de 1-2 chars siguen ignorados (ruido); el umbral bajó a 3, no a 1."""
    item = _item("De la Ciudad")
    assert _theme_match_bonus(item, ["de"]) == 0.0
