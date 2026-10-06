"""Hallazgos del V3 de F2 (stack real), convertidos en test antes de cerrarse.

H12 — "¿cuántas construcciones caen dentro del buffer?" respondió 1000 donde
había 8442. El bucle vio "1000 elemento(s)" de una consulta cortada por su
LIMIT y "1 elemento(s)" del COUNT(*): el número real nunca llegó al LLM.

H13 — el router devolvió como capa objetivo el id del dataset (`ds_…`) que ve
junto a la capa, y se descartaba como inexistente.
"""

from __future__ import annotations

import pytest

from geo_copilot.agents.gis_agent import sql_ast_validator as ast_v
from geo_copilot.orchestrator.layer_resolution import id_de_capa
from geo_copilot.orchestrator.react_tools import _observe


def _fc(n: int) -> dict:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}}
    ] * n}


def test_h12_el_conteo_llega_con_su_valor():
    obs, ok = _observe("query_database", {
        "raw_data": [{"cantidad_construcciones": 8442}],
        "sql": "SELECT COUNT(*) AS cantidad_construcciones FROM c JOIN b ON ST_Intersects(c.shape, b.geom) LIMIT 1000;",
    })
    assert ok and "8442" in obs
    # 1 fila < 1000: completo y exacto (tras un aviso de tope en un paso previo,
    # el LLM narró este COUNT como "al menos… aproximado")
    assert "COMPLETO" in obs and "no es el total" not in obs


def test_h12_una_consulta_cortada_por_su_limit_lo_dice():
    filas = [{"objectid": i, "geom_geojson": "{}"} for i in range(1000)]
    obs, _ = _observe("query_database", {
        "raw_data": filas, "geojson": _fc(1000),
        "sql": "SELECT objectid, ST_AsGeoJSON(shape) AS geom_geojson FROM c LIMIT 1000;",
    })
    assert "1000 elemento(s)" in obs and "LIMIT 1000" in obs and "no es el total" in obs
    assert "Datos" not in obs  # una capa no vuelca filas: la cifra es el conteo


def test_h12_las_filas_tabulares_van_sin_geometria_y_acotadas():
    filas = [{"barrio": f"B{i}", "n": i, "geom": "0101…"} for i in range(20)]
    obs, _ = _observe("query_database", {"raw_data": filas, "sql": "SELECT barrio, n, geom FROM t"})
    assert "B0" in obs and '"geom"' not in obs
    assert "(de 20)" in obs


def test_h13_el_id_del_dataset_resuelve_a_su_capa():
    state = {
        "map_layers": {"layer-1": {}, "layer-2": {}},
        "map_context": {"layers": [
            {"id": "layer-1", "dataset_id": "ds_aaaaaaaaaaaaaaaa"},
            {"id": "layer-2", "dataset_id": "ds_bbbbbbbbbbbbbbbb"},
        ]},
    }
    assert id_de_capa("ds_bbbbbbbbbbbbbbbb", state) == "layer-2"
    assert id_de_capa("layer-1", state) == "layer-1"
    assert id_de_capa("ds_cccccccccccccccc", state) is None
    assert id_de_capa(None, state) is None


# H14 — "¿cuántas construcciones caen dentro de ese buffer?" → "No hay": el SQL
# comparaba `shape` (4326) contra un buffer en 32618. PostGIS no da error: el
# prefiltro por bbox compara grados con metros, nunca solapa, COUNT = 0.

SQL_H14 = (
    'SELECT COUNT(*) FROM catastro.construcciones c WHERE ST_Intersects(c."shape", '
    '(SELECT ST_Union(ST_Buffer(ST_Transform(l."shape", 32618), 500)::geometry) '
    "FROM catastro.lotes l WHERE l.\"manzcodigo\" = '004503009')) LIMIT 1000"
)


@pytest.fixture
def srid_4326():
    ast_v.fijar_srid_de_columnas(4326)
    yield
    ast_v.fijar_srid_de_columnas(None)


def test_h14_el_sql_real_con_srid_mezclado_se_rechaza(srid_4326):
    r = ast_v.analizar(SQL_H14)
    assert not r.aceptada
    assert any("SRID mezclado en st_intersects: 4326 contra 32618" in m for m in r["motivos"])


@pytest.mark.parametrize("sql", [
    # el cruce correcto contra el workspace (mismo SRID)
    'SELECT COUNT(*) FROM catastro.construcciones c JOIN ws_x.d_y b ON ST_Intersects(c."shape", b.geom)',
    # los dos lados transformados al mismo SRID métrico
    "SELECT ST_Area(ST_Intersection(ST_Transform(a.geom, 9377), ST_Transform(b.geom, 9377))) FROM t a, t b",
    # geography::geometry vuelve a 4326
    "SELECT 1 FROM t a WHERE ST_Intersects(a.geom, ST_Buffer(a.geom::geography, 500)::geometry)",
    # envolvente en 4326 contra columna en 4326
    "SELECT 1 FROM t a WHERE ST_Intersects(a.geom, ST_MakeEnvelope(-74.1, 4.5, -74.0, 4.6, 4326))",
])
def test_h14_srid_consistente_se_acepta(srid_4326, sql):
    r = ast_v.analizar(sql)
    assert not any("SRID mezclado" in m for m in r["motivos"]), r["motivos"]


def test_h14_explicito_contra_explicito_se_detecta_sin_saber_el_de_las_columnas():
    ast_v.fijar_srid_de_columnas(None)
    r = ast_v.analizar(
        "SELECT 1 FROM t a WHERE ST_Intersects(ST_Transform(a.geom, 9377), ST_Transform(a.geom, 3116))"
    )
    assert any("9377 contra 3116" in m for m in r["motivos"])
    # columna (desconocido) contra explícito: no se afirma lo que no se sabe
    r2 = ast_v.analizar("SELECT 1 FROM t a WHERE ST_Intersects(a.geom, ST_Transform(a.geom, 32618))")
    assert not any("SRID mezclado" in m for m in r2["motivos"])


# H15 (V5 de F2) — "construcciones a menos de 300 m de la manzana": el validador
# rechazó 6 veces un ST_DWithin correcto porque su segundo argumento era una
# SUBCONSULTA con ::geography y la regla de unidades no miraba dentro. El bucle
# agotó sus 8 llamadas sin traer nada.

SQL_H15 = """SELECT c."objectid" FROM catastro.construcciones c
WHERE ST_DWithin(
    ST_Transform(c."shape", 4326)::geography,
    (SELECT ST_Transform(l."shape", 4326)::geography FROM catastro.lotes l
     WHERE l."manzcodigo" = '004503009' LIMIT 1),
    300)
LIMIT 1000"""


def test_h15_subconsulta_con_geography_no_es_error_de_unidades():
    r = ast_v.analizar(SQL_H15)
    assert not any("unidades" in m for m in r["motivos"]), r["motivos"]


def test_h15_subconsulta_en_grados_sigue_rechazada():
    sql = SQL_H15.replace('ST_Transform(l."shape", 4326)::geography', 'l."shape"')
    r = ast_v.analizar(sql)
    assert any("unidades: st_dwithin" in m for m in r["motivos"]), r["motivos"]


# H16 (V5 de F2) — con el validador arreglado (H15), la misma consulta hizo
# timeout 3 veces: ST_DWithin(col::geography, …) no usa el índice GiST y recorre
# 2,4 M construcciones. El patrón indexable (`col && ST_Expand(ref, d)` + la
# medida geography) tarda 0,7 s y el validador lo rechazaba por la allowlist.

SQL_H16 = """SELECT c."objectid" FROM catastro.construcciones c
WHERE c."shape" && ST_Expand((SELECT ST_Union(l."shape") FROM catastro.lotes l
                               WHERE l."manzcodigo" = '004503009'), 0.0045)
  AND ST_DWithin(c."shape"::geography,
                 (SELECT ST_Union(l."shape") FROM catastro.lotes l
                  WHERE l."manzcodigo" = '004503009')::geography, 300)
LIMIT 1000"""


def test_h16_el_prefiltro_indexable_de_proximidad_se_acepta(srid_4326):
    r = ast_v.analizar(SQL_H16)
    assert r.aceptada, r["motivos"]


def test_h16_el_prompt_sql_ensena_el_prefiltro_indexable():
    # F1: el prompt del generador SQL vive en prompts/sql_generacion.md (antes, en el agente)
    from geo_copilot.prompts import cargar_prompt

    texto = cargar_prompt("sql_generacion")
    assert "PREFILTRO INDEXABLE" in texto and "ST_Expand" in texto


# H17 (V5 de F2) — un timeout llegaba al corrector como "" ("Unknown error"); tras
# tres intentos lentos el LLM bajó el LIMIT a 50 y truncó el resultado en silencio.

def test_h17_un_timeout_llega_al_corrector_con_causa_y_remedio():
    import asyncio

    from geo_copilot.orchestrator.nodes.gis_agent import _describir_fallo

    for exc in (TimeoutError(), TimeoutError()):
        msg = _describir_fallo(exc)
        assert "timeout" in msg and "ST_Expand" in msg and "NO bajes el LIMIT" in msg
    assert _describir_fallo(ValueError("columna x no existe")) == "columna x no existe"
    assert _describir_fallo(RuntimeError()) == "RuntimeError"


# H18 (V5 de F2) — tras ws_spatial_autocorrelation, la simbología estilizó la capa
# de ENTRADA (el target del router seguía puesto), se degradó a un color y el LLM
# narró "HH en rojo, HL en naranja…" sobre un mapa monocromo.

@pytest.mark.asyncio
async def test_h18_la_capa_nueva_de_una_ws_pasa_a_ser_el_foco(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.platform.contracts import LayerRef, Provenance, WorkspaceTable
    from geo_copilot.platform.workspace import context as wsctx
    from geo_copilot.platform.workspace import ops

    ref = LayerRef(
        id="ds_aaaaaaaaaaaaaaaa", name="LISA", kind="vector", provider="core", crs="EPSG:4326",
        feature_count=50, storage=WorkspaceTable(schema_name="ws_0123456789abcdef", table="d_x"),
        provenance=Provenance(capability="core.autocorrelation", produced_at=datetime.now(UTC)),
    )
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(to_geojson=AsyncMock(return_value={"type": "FeatureCollection", "features": []})))
    monkeypatch.setattr(ops, "autocorrelacion", AsyncMock(return_value=ops.Resultado(ref, {})))
    out = await ce._autocorrelacion(
        None, {"session_id": "s", "target_layer_id": "layer-construcciones"},
        {"dataset": "ds_bbbbbbbbbbbbbbbb", "field": "connpisos", "method": "lisa"},
    )
    assert "target_layer_id" in out.delta and out.delta["target_layer_id"] is None


def test_h18_la_observacion_de_simbologia_dice_lo_que_se_aplico():
    obs, _ = _observe("apply_symbology", {"symbology": {"symbology_type": "single_symbol"}}, layer_fc=50)
    assert "single_symbol" in obs and "UN SOLO COLOR" in obs
    obs2, _ = _observe("apply_symbology", {"symbology": {
        "symbology_type": "unique_values", "classification_field": "lisa_clase",
        "class_breaks": [{}, {}, {}, {}],
    }}, layer_fc=50)
    assert "«lisa_clase», 4 clase(s)" in obs2 and "UN SOLO COLOR" not in obs2


# H20 (V5 de F2) — proximidad con ::geography sin prefiltro indexable: >30 s.
# T3.0 (principio agéntico): NO se veta — en tablas chicas es SQL válido y la
# decisión es del modelo. Lo que queda son hechos: el timeout con causa (H17) y
# el conocimiento del índice en el prompt (H16).

def test_h20_proximidad_sin_prefiltro_ya_no_se_veta():
    r = ast_v.analizar(SQL_H15)
    assert not any(m.startswith("rendimiento") for m in r["motivos"]), r["motivos"]


# H21 (V5 de F2) — la narración nombró colores ("rojo HH, azul LL…") que no eran
# los del mapa. La observación lleva los colores y conteos REALES por clase.

def test_h21_la_observacion_lleva_los_colores_reales_por_clase():
    obs, _ = _observe("apply_symbology", {"symbology": {
        "symbology_type": "unique_values", "classification_field": "lisa_clase",
        "class_breaks": [
            {"label": "ns", "color": "#66c2a5", "count": 2514},
            {"label": "HH", "color": "#fc8d62", "count": 284},
        ],
    }}, layer_fc=3107)
    assert "ns=#66c2a5 (2514)" in obs and "HH=#fc8d62 (284)" in obs and "SOLO estos" in obs


@pytest.mark.asyncio
async def test_h21_la_capa_lisa_sale_sin_estilo_decidido_por_codigo(monkeypatch):
    """T3.0: el estilo lo decide el LLM (simbología con `category_colors` y la
    convención como conocimiento). La capacidad solo informa el hecho."""
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from geo_copilot.orchestrator import capabilities_espaciales as ce
    from geo_copilot.platform.contracts import LayerRef, Provenance, WorkspaceTable
    from geo_copilot.platform.workspace import context as wsctx
    from geo_copilot.platform.workspace import ops

    ref = LayerRef(
        id="ds_aaaaaaaaaaaaaaaa", name="LISA", kind="vector", provider="core", crs="EPSG:4326",
        feature_count=3107, storage=WorkspaceTable(schema_name="ws_0123456789abcdef", table="d_x"),
        provenance=Provenance(capability="core.autocorrelation", produced_at=datetime.now(UTC)),
    )
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(to_geojson=AsyncMock(return_value={"type": "FeatureCollection", "features": []})))
    monkeypatch.setattr(ops, "autocorrelacion", AsyncMock(return_value=ops.Resultado(ref, {"moran_i": 0.27})))
    out = await ce._autocorrelacion(None, {"session_id": "s"}, {"dataset": "ds_b", "field": "connpisos", "method": "lisa"})
    assert out.delta.get("symbology") is None
    assert "«lisa_clase»" in out.observation and "apply_symbology" in out.observation
    assert not hasattr(ops, "simbologia_de_autocorrelacion")


# H23 (V5 de F2) — "colorea esos puntos por estado" sobre 41.033 puntos (capa
# teselada, no hidratada): la simbología no tenía datos, el router decía "no hay
# capa" y aun así se narró "coloreado por estado" sobre un mapa monocromo.

def test_h23_la_capa_grande_da_su_muestra_solo_para_estilo():
    from geo_copilot.orchestrator.layer_resolution import muestra_para_estilo, resolver_capa

    muestra = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None, "properties": {"state": "NY"}}]}
    state = {
        "map_layers": {"layer-1": {"data": None, "name": "ZIP", "muestra": muestra, "total": 41033}},
        "map_context": {"layers": [{"id": "layer-1", "is_active": True, "dataset_id": "ds_x"}]},
    }
    assert muestra_para_estilo(state) == (muestra, 41033, "layer-1")
    # …y NADIE más la ve como capa con datos: analizar sobre una muestra sería mentir
    assert resolver_capa(state) is None


@pytest.mark.asyncio
async def test_h23_la_simbologia_usa_la_muestra_y_no_publica_conteos_falsos():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from geo_copilot.orchestrator.nodes import symbology as nodo

    muestra = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None, "properties": {"state": "NY"}}] * 3}
    agente = SimpleNamespace(process=AsyncMock(return_value=SimpleNamespace(success=True, message="", data={
        "symbology_type": "unique_values", "classification_field": "state", "layer_title": "ZIP por estado",
        "class_breaks": [{"label": "NY", "color": "#ff0000", "count": 3}],
    })))
    state = {
        "query": "colorea por estado",
        "map_layers": {"layer-1": {"data": None, "name": "ZIP", "muestra": muestra, "total": 41033}},
        "map_context": {"layers": [{"id": "layer-1", "is_active": True, "dataset_id": "ds_x"}]},
    }
    out = await nodo.run(SimpleNamespace(symbology_agent=agente), state)
    assert agente.process.call_args.kwargs["context"]["geojson"] is muestra
    assert "count" not in out["symbology"]["class_breaks"][0]  # conteo de muestra ≠ de la capa
    assert "geojson" not in out  # la muestra NO sale como datos de la capa


@pytest.mark.asyncio
async def test_h23_sin_datos_la_simbologia_falla_honesta():
    from types import SimpleNamespace

    from geo_copilot.orchestrator.nodes import symbology as nodo

    out = await nodo.run(SimpleNamespace(symbology_agent=None), {"query": "colorea", "map_layers": {}})
    assert out.get("error")
    obs, ok = _observe("apply_symbology", out)
    assert not ok and "falló" in obs


def test_fh7_una_capa_pequena_lleva_sus_atributos_con_su_id_para_nombrarlos_y_citarlos():
    from geo_copilot.orchestrator.react_tools import _filas_tabulares

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "id": 7, "properties": {"lotcodigo": "L7", "area_m2": 2483.22, "geom": "0101…"},
         "geometry": None},
        {"type": "Feature", "properties": {"lotcodigo": "L8"}, "geometry": None}]}
    txt = _filas_tabulares({"geojson": fc})
    assert '"id": 7' in txt and '"lotcodigo": "L7"' in txt and "2483.22" in txt
    assert '"id": 1' in txt  # sin id de Feature: su índice (la misma identidad que el mapa)
    assert '"geom"' not in txt
    grande = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {"n": i}} for i in range(9)]}
    assert _filas_tabulares({"geojson": grande}) == ""  # una muestra se tomaría por el total


def test_un_solo_color_lleva_el_motivo_del_disenador():
    """V5 (otra temática): sin el motivo, el bucle especulaba («puede deberse a…») y repetía."""
    obs, _ = _observe("apply_symbology", {"symbology": {
        "symbology_type": "single_symbol",
        "reasoning": "El campo RuleID tiene un único valor (1) para todos los puntos."}}, layer_fc=561)
    assert "motivo del diseñador: «El campo RuleID tiene un único valor (1)" in obs


def test_una_consulta_rechazada_por_el_usuario_no_es_falta_de_acceso():
    """V5 F5: tras rechazar un SQL, el bucle dijo «no tengo acceso a los datos del catastro»."""
    obs, ok = _observe("query_database", {"error": "Consulta rechazada: Rechazado por el usuario",
                                          "requires_hitl": True, "hitl_approved": False})
    assert not ok and "el USUARIO rechazó esta consulta" in obs and "No es falta de acceso" in obs
    obs2, _ = _observe("query_database", {"error": "timeout"})
    assert obs2.startswith("query_database falló: timeout")
