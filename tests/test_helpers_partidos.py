"""Las funciones que salieron al partir los archivos gigantes (F4), probadas solas y sin LLM.

Hasta ahora solo se ejercitaban de punta a punta: un error al pasar argumentos al partir (p. ej.
invertir `previous_sql` y `previous_results`) no lo detectaba ninguna prueba sin LLM (auditoría F4).
"""
from __future__ import annotations

import pytest


# --------------------------------------------------------------------------- agente Python: juez
def test_resumen_salida_describe_cada_salida_del_codigo():
    from geo_copilot.agents.python_agent.juez import _resumen_salida

    r = _resumen_salida({"type": "FeatureCollection"}, [{"a": 1}, {"a": 2}, {"a": 3}], {"media": 2.0},
                        {"chart_type": "bar", "data": [1, 2]}, feature_count=7)
    assert r[0] == "- GeoDataFrame `result`: 7 features"
    assert r[1].startswith("- table: 3 filas; primeras: ") and '{"a": 3}' not in r[1]  # solo las 2 primeras
    assert r[2] == '- stats: {"media": 2.0}'
    assert r[3] == "- chart: tipo bar, 2 puntos"
    assert _resumen_salida(None, None, None, None, 0) == ["- (sin salidas)"]
    # una tabla VACÍA es una salida (no «sin salidas»)
    assert _resumen_salida(None, [], None, None, 0) == ["- table: 0 filas; primeras: []"]


# --------------------------------------------------------------------------- agente GIS
def test_contexto_conversacion_lleva_la_consulta_anterior_y_su_muestra():
    from geo_copilot.agents.gis_agent.esquema import _contexto_conversacion

    historial = [{"role": "user", "content": f"pregunta {i}"} for i in range(6)]
    filas = [{"id": i} for i in range(5)]
    txt = _contexto_conversacion(historial, "SELECT * FROM lotes WHERE x = 1", filas)
    assert "pregunta 1" not in txt and "pregunta 2" in txt  # las 4 últimas
    assert "SQL DE LA CONSULTA ANTERIOR:\nSELECT * FROM lotes WHERE x = 1" in txt
    assert "RESULTADOS ANTERIORES (5 registros)" in txt and '"id": 3' not in txt  # muestra de 3
    assert "\n     Y pregunta" in txt  # el prompt idéntico al original (auditoría F4)
    # sin SQL anterior no hay reglas de seguimiento ni resultados
    solo = _contexto_conversacion(historial, None, filas)
    assert "SQL DE LA CONSULTA" not in solo and "RESULTADOS ANTERIORES" not in solo
    assert _contexto_conversacion(None, None, None) == ""


def test_formatear_esquema_separa_la_geometria_y_lleva_los_hechos():
    from geo_copilot.agents.gis_agent.esquema import _formatear_esquema

    filas = [
        {"table_schema": "catastro", "table_name": "lotes", "column_name": "lotcodigo", "udt_name": "varchar",
         "geometry_type": None},
        {"table_schema": "catastro", "table_name": "lotes", "column_name": "geom", "udt_name": "geometry",
         "geometry_type": "MULTIPOLYGON"},
        {"table_schema": "public", "table_name": "notas", "column_name": "texto", "udt_name": "text",
         "geometry_type": None},
    ]
    txt = _formatear_esquema(filas, {("catastro", "lotes", "lotcodigo"): "valores: 1,2"},
                             {"catastro.lotes": "todo es de Bogotá"})
    assert 'TABLA catastro.lotes: | GEOMETRIA: "geom" (MULTIPOLYGON)' in txt
    assert '  Columnas: "lotcodigo" (varchar; valores: 1,2)' in txt
    assert "  Descripción: todo es de Bogotá" in txt
    assert "TABLA public.notas: | SIN GEOMETRIA" in txt


@pytest.mark.parametrize(("sql", "tope", "esperado"), [
    ("SELECT * FROM t", 1000, "SELECT * FROM (\nSELECT * FROM t\n) AS _capped\nLIMIT 1000"),
    ("SELECT * FROM t LIMIT 50;", 1000, "SELECT * FROM t LIMIT 50"),           # por debajo: se respeta
    ("SELECT * FROM t LIMIT 5000", 1000, "SELECT * FROM t LIMIT 1000"),        # por encima: se capa
    ("SELECT * FROM t LIMIT 5000 OFFSET 10", 1000, "SELECT * FROM t LIMIT 1000 OFFSET 10"),
    # un LIMIT solo en la subconsulta NO limita la exterior: se envuelve (bypass P0-A)
    ("SELECT * FROM (SELECT * FROM t LIMIT 5) s", 1000,
     "SELECT * FROM (\nSELECT * FROM (SELECT * FROM t LIMIT 5) s\n) AS _capped\nLIMIT 1000"),
    ("SELECT * FROM t", "x", "SELECT * FROM (\nSELECT * FROM t\n) AS _capped\nLIMIT 1000"),  # tope inválido
])
def test_con_tope_el_limit_exterior_no_se_elude(sql, tope, esperado):
    from geo_copilot.agents.gis_agent.ejecucion_sql import _con_tope

    assert _con_tope(sql, tope) == esperado


def test_timeout_del_conteo_previo():
    from geo_copilot.agents.gis_agent.ejecucion_sql import _timeout_conteo
    from geo_copilot.core.config import get_settings

    defecto = int(get_settings().db_query_timeout * 1000)
    assert _timeout_conteo(None) == max(500, defecto)
    assert _timeout_conteo("no-es-numero") == max(500, defecto)
    assert _timeout_conteo(100) == 500       # mínimo
    assert _timeout_conteo("2500") == 2500


# --------------------------------------------------------------------------- insights: seguimiento
def test_contexto_seguimiento_pone_cada_hecho_en_su_sitio():
    from geo_copilot.agents.insights_agent.seguimiento import _contexto_seguimiento

    filas = [{"lote": i} for i in range(5)]
    capa = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"lote": 1, "uso": "res"}}]}
    txt = _contexto_seguimiento("¿y cuántos eran?", "SELECT 1", filas, capa,
                                [{"role": "user", "content": "hola"}, {"role": "assistant", "content": "30 lotes"}],
                                None, None)
    assert txt.startswith("Pregunta del usuario: ¿y cuántos eran?")
    assert "  Usuario: hola" in txt and "  Asistente: 30 lotes" in txt
    assert "```sql\nSELECT 1\n```" in txt
    # las filas son una MUESTRA (3 de 5), no los datos para un total
    assert "5 fila(s)" in txt and "MUESTRA de 3 de esas 5" in txt and '"lote": 3' not in txt
    assert "Capa de esa última consulta: 1 features de tipo Point" in txt
    assert "Campos disponibles por feature: lote, uso" in txt


def test_error_del_seguimiento_es_honesto():
    from geo_copilot.agents.insights_agent.seguimiento import _error_seguimiento

    out = _error_seguimiento(TimeoutError("lento"))
    assert "TimeoutError" in out["final_response"] and out["messages"][0]["success"] is False


# --------------------------------------------------------------------------- simbología: diseño
def test_esquema_compacto_lo_que_el_llm_ve_de_cada_campo():
    from geo_copilot.agents.symbology_agent.diseno import _esquema_compacto

    esquema = _esquema_compacto({
        "area": {"data_type": "numeric_continuous", "statistics": {"min": 1, "max": 9, "std": 2.34567}},
        "uso": {"data_type": "categorical", "unique_values": 7,
                "value_counts": {f"u{i}": 1 for i in range(7)}},
    })
    assert esquema["area"] == {"data_type": "numeric_continuous", "stats": {"min": 1, "max": 9, "std": 2.346}}
    assert esquema["uso"]["unique"] == 7 and esquema["uso"]["top_values"] == ["u0", "u1", "u2", "u3", "u4"]


def test_completo_no_comparte_defaults_entre_llamadas():
    from geo_copilot.agents.symbology_agent.diseno import _completo

    a = _completo({"symbology_type": "unique_values", "reasoning": "x"})
    a["num_classes"] = 99
    b = _completo({"symbology_type": "unique_values", "reasoning": "y"})
    assert b["num_classes"] == 5 and b["color_scheme"] == "Blues"


# --------------------------------------------------------------------------- insights: diseño y encaje
def test_tipo_de_dato_y_clases_salen_de_los_graficos():
    from geo_copilot.agents.insights_agent.diseno_viz import _tipo_y_clases

    assert _tipo_y_clases([]) == ("categorical", 5)
    assert _tipo_y_clases([{"chart_type": "histogram"}]) == ("numeric_continuous", 5)
    assert _tipo_y_clases([{"chart_type": "line", "limit": 30}]) == ("temporal", 12)
    assert _tipo_y_clases([{"chart_type": "bar", "limit": 1}]) == ("categorical", 2)


def test_sin_diseno_distingue_sin_datos_de_sin_llm():
    from geo_copilot.agents.insights_agent.diseno_viz import _defaults_viz, _sin_diseno

    assert _sin_diseno(_defaults_viz(), [], [], True)["reasoning"] == "sin datos para visualizar"
    sin_llm = _sin_diseno(_defaults_viz(), [{"id": 1}], [], False)
    assert sin_llm["degraded"] is True
    assert _sin_diseno(_defaults_viz(), [{"id": 1}], [], True) is None


@pytest.mark.parametrize(("fc", "texto"), [(1, "1 punto"), (12, "12 puntos"), (1500, "1,500 puntos"),
                                           (2_000_000, "2,000,000 puntos (datos masivos)")])
def test_conteo_legible(fc, texto):
    from geo_copilot.agents.insights_agent.encaje import _conteo

    assert _conteo(fc, "punto" if fc == 1 else "puntos") == texto
