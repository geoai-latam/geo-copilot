"""F1.3: honestidad de CRS/unidades en área/distancia.

Revisión del 8-sep-2026: esta suite probaba 4326, 32618, 32718 y `None`. Ni un
CRS colombiano — que es exactamente cómo sobrevivió que `is_metric_crs` dijera
que **9377 y 3116 NO son métricos**, siendo los dos CRS proyectados oficiales de
Colombia y el caso de uso principal del sistema. Lo que no se prueba, no existe.
"""

import pytest

from geo_copilot.core.formatters import (
    check_measurable_crs,
    crs_units_note,
    is_metric_crs,
)


def test_4326_is_degrees_not_metric():
    note = crs_units_note(4326)
    assert "GRADOS" in note and "NO métrico" in note
    assert is_metric_crs(4326) is False


def test_utm_is_metric():
    assert "METROS" in crs_units_note(32618)   # UTM 18N (Colombia)
    assert "METROS" in crs_units_note(32718)   # UTM 18S
    assert is_metric_crs(32618) is True
    assert is_metric_crs(32718) is True


def test_unknown_srid():
    assert "indeterminadas" in crs_units_note(0)
    assert "indeterminadas" in crs_units_note(None)
    assert is_metric_crs(None) is False


# ---------------------------------------------------------------------------
# Los CRS de Colombia, que es de lo que va este sistema
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("srid", "nombre"),
    [
        (9377, "MAGNA-SIRGAS 2018 / Origen-Nacional"),
        (3116, "MAGNA-SIRGAS / Colombia Bogotá zone"),
        (3115, "Colombia West zone"),
        (3117, "Colombia East Central zone"),
        (3118, "Colombia East zone"),
        (21896, "Bogotá 1975 / Colombia West zone"),
    ],
)
def test_los_proyectados_de_colombia_si_son_metricos(srid, nombre):
    """El fallo que la suite vieja no podía ver."""
    assert is_metric_crs(srid) is True, nombre
    assert "METROS" in crs_units_note(srid), crs_units_note(srid)
    apto, motivo = check_measurable_crs(srid)
    assert apto, motivo


def test_4686_magna_sirgas_geografico_es_grados():
    """4686 es el datum oficial de Colombia y son GRADOS, igual que 4326.

    Confundirlo con 9377 es el error de bulto del dominio: el nombre
    "MAGNA-SIRGAS" está en los dos.
    """
    assert is_metric_crs(4686) is False
    nota = crs_units_note(4686)
    assert "GRADOS" in nota and "NO métrico" in nota, nota
    assert check_measurable_crs(4686)[0] is False


def test_la_nota_del_prompt_nombra_el_sistema():
    """`crs_units_note` alimenta el prompt (`semantic/layer.py:440`).

    Antes, para 9377 decía "verificar unidades del CRS", que es lo mismo que
    no decir nada.
    """
    nota = crs_units_note(9377)
    assert "9377" in nota
    assert "MAGNA-SIRGAS" in nota, nota
    assert "verificar unidades" not in nota


# ---------------------------------------------------------------------------
# 3857: métrico SÍ, medible NO. La decisión, explícita.
# ---------------------------------------------------------------------------

def test_3857_esta_en_metros_pero_no_sirve_para_medir():
    """Son dos preguntas distintas y el repo tiene que contestarlas distinto.

    Sus coordenadas SON metros, así que `is_metric_crs` dice `True`. Pero
    Web Mercator es conforme, no equivalente: deforma el área con la latitud
    (~0,7 % en Bogotá, >170 % en la Patagonia). Para medir, `False`.
    """
    assert is_metric_crs(3857) is True

    apto, motivo = check_measurable_crs(3857)
    assert apto is False
    assert "deforma" in motivo.lower(), motivo

    nota = crs_units_note(3857)
    assert "METROS" in nota
    assert "NO sirve para medir" in nota, nota


def test_proyectado_pero_en_pies_no_es_metrico():
    """Proyectado ≠ métrico. EPSG:2276 mide en `US survey foot`."""
    assert is_metric_crs(2276) is False
    assert check_measurable_crs(2276)[0] is False
    assert "foot" in crs_units_note(2276).lower()


@pytest.mark.parametrize("srid", [6318, 7844, 5340])
def test_geograficos_fuera_del_rango_4000_4999(srid):
    """6318 NAD83(2011), 7844 GDA2020, 5340 POSGAR 2007 (Argentina).

    Los tres son geográficos y caen fuera del bloque 4000–4999, que es el
    atajo que se descartó.
    """
    assert is_metric_crs(srid) is False
    assert check_measurable_crs(srid)[0] is False


@pytest.mark.parametrize("srid", [4484, 4489, 4647])
def test_proyectados_metricos_dentro_del_rango_4000_4999(srid):
    """Las UTM de México (ITRF92) y ETRS89/UTM 32N: métricos de verdad."""
    assert is_metric_crs(srid) is True
    assert check_measurable_crs(srid)[0] is True


def test_srid_inexistente_falla_cerrado():
    assert is_metric_crs(999_999) is False
    apto, motivo = check_measurable_crs(999_999)
    assert apto is False
    assert "no existe" in motivo, motivo


# ---------------------------------------------------------------------------
# Una sola definición: el validador SQL no puede tener la suya
# ---------------------------------------------------------------------------

def test_el_validador_sql_usa_esta_misma_funcion():
    """El defecto real no era ninguna de las tres reglas por separado: era que
    el prompt le decía una cosa al modelo y el validador le hacía cumplir otra.
    """
    from geo_copilot.agents.gis_agent import sql_ast_validator

    assert sql_ast_validator.check_measurable_crs is check_measurable_crs

    # Y el efecto observable: 9377 pasa el validador, 3857 no.
    assert sql_ast_validator.analizar(
        "SELECT ST_Area(ST_Transform(geom, 9377)) FROM lotes"
    ).aceptada
    assert not sql_ast_validator.analizar(
        "SELECT ST_Area(ST_Transform(geom, 3857)) FROM lotes"
    ).aceptada


def test_semantic_context_includes_crs(tmp_path):
    import textwrap

    from geo_copilot.semantic.layer import SemanticLayer
    yaml_path = tmp_path / "e.yaml"
    yaml_path.write_text(textwrap.dedent("""
        version: "1.0"
        entities:
          parcela:
            description: "u"
            aliases: ["predio"]
            table: t
            schema: s
            geometry_column: geom
            geometry_type: POLYGON
            srid: 4326
            fields:
              id: {column: id, type: string}
    """), encoding="utf-8")
    layer = SemanticLayer(str(yaml_path))
    ctx = layer.get_context_for_llm()
    assert "CRS:" in ctx
    assert "GRADOS" in ctx  # el LLM ve que 4326 es grados
