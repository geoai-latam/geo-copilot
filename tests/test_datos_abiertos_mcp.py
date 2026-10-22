"""datos-abiertos-mcp: datos.gov.co (Socrata) y CKAN (Datos Abiertos Bogotá).

Portales simulados con la forma de las respuestas reales medidas el 2026-10-15 (Envigado, instituciones
educativas g8hy-gwg8: 82 filas, 70 en [-75.62, 6.14, -75.55, 6.20]; colegios privados de Bogotá en
GeoJSON con `crs` EPSG:3857). Las verdades contra los portales reales: tests/test_datos_abiertos_mcp_vivo.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "datos_abiertos_mcp"))
from datos_abiertos_mcp import ckan, filas, socrata
from datos_abiertos_mcp import server as srv

FILAS = [{"nombre_ie": f"IE {i}", "tipo_ie": "Oficial", "lat": f"6.1{i}", "lon": f"-75.5{i}"} for i in range(5)]


def _resp(datos, status=200, tipo="application/json") -> httpx.Response:
    req = httpx.Request("GET", "https://x.test/")
    if isinstance(datos, (dict, list)):
        return httpx.Response(status, json=datos, request=req)
    return httpx.Response(status, content=datos.encode(), headers={"content-type": tipo}, request=req)


@pytest.fixture
def portal(monkeypatch, tmp_path):
    pedidos: list[tuple[str, dict]] = []

    def get(url, params=None, **kw):
        p = dict(params or {})
        pedidos.append((url, p))
        if url == socrata.CATALOGO:
            return _resp({"results": [{"resource": {
                "id": "g8hy-gwg8", "name": "Instituciones Educativas Municipio de Envigado",
                "attribution": "Alcaldía de Envigado, Antioquia", "description": "Datos básicos",
                "columns_field_name": ["nombre_ie", "coordenadas"], "columns_datatype": ["Text", "Location"],
                "data_updated_at": "2024-08-30T10:00:00Z", "page_views": {"page_views_total": 22281}},
                "classification": {"domain_category": "Educación"}}]})
        if p.get("$select") == "count(*)":
            return _resp([{"count": "70" if p.get("$where") else "82"}])
        if url.endswith(".geojson"):
            return _resp({"type": "FeatureCollection", "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-75.59, 6.17]},
                 "properties": {"nombre_ie": "IE 1"}}]})
        if url.endswith(".json"):
            return _resp(FILAS[: int(p["$limit"])])
        raise AssertionError(url)

    monkeypatch.setattr(socrata.red, "get", get)
    monkeypatch.setattr(srv.resultados, "directorio", str(tmp_path))
    return pedidos


def test_buscar_devuelve_hechos_para_elegir_y_marca_columnas_de_ubicacion(portal):
    sc = srv.datos_buscar("instituciones educativas", portal="datos.gov.co").structuredContent
    (p,) = sc["facts"]["portales"]
    (c,) = p["conjuntos"]
    assert c["id"] == "g8hy-gwg8" and c["entidad"] == "Alcaldía de Envigado, Antioquia"
    assert c["columnas_de_ubicacion"] == ["coordenadas (Location)"] and c["vistas"] == 22281
    assert portal[0][1]["domains"] == "www.datos.gov.co"


def test_consulta_con_ubicacion_y_bbox_filtra_en_el_portal(portal):
    sc = srv.datos_consultar("g8hy-gwg8", columna_geo="coordenadas", bbox=[-75.62, 6.14, -75.55, 6.20]).structuredContent
    q = [p for u, p in portal if u.endswith(".geojson")][0]
    # within_box(columna, lat norte, lon oeste, lat sur, lon este): medido, 70 en Envigado
    assert q["$where"] == "(within_box(coordenadas, 6.2, -75.62, 6.14, -75.55))"
    assert q["$order"] == ":id"  # sin orden las páginas de SODA no son estables
    assert sc["facts"]["cumplen_en_el_portal"] == 70 and sc["artifacts"][0]["kind"] == "feature_collection"


def test_lat_lon_de_texto_son_puntos_y_el_bbox_castea_a_numero(portal):
    sc = srv.datos_consultar("abcd-1234", lat="lat", lon="lon", bbox=[-76, 6, -75, 7], where="tipo_ie = 'Oficial'",
                             max_filas=5).structuredContent
    q = [p for u, p in portal if u.endswith(".json") and "$limit" in p][0]
    assert q["$where"] == "(tipo_ie = 'Oficial') AND (lat::number between 6 and 7 AND lon::number between -76 and -75)"
    feats = sc["artifacts"][0]["data"]["features"]
    assert feats[0]["geometry"]["coordinates"] == [-75.5, 6.1] and len(feats) == 5


def test_sin_ubicacion_es_tabla_y_dice_si_vino_incompleta(portal):
    sc = srv.datos_consultar("abcd-1234", where="tipo_ie = 'Oficial'", max_filas=3).structuredContent
    assert sc["artifacts"][0]["kind"] == "table" and len(sc["artifacts"][0]["rows"]) == 3
    assert sc["facts"]["cumplen_en_el_portal"] == 70 and sc["facts"]["completo"] is False
    assert "no es el conjunto completo" in sc["facts"]["aviso"]


def test_id_invalido_y_portal_desconocido_son_errores_honestos(portal):
    assert "abcd-1234" in srv.datos_consultar("drop table").structuredContent["error"]
    assert "los que hay" in srv.datos_consultar("abcd-1234", portal="otro").structuredContent["error"]
    assert "para socrata" in srv.datos_consultar("abcd-1234", portal="bogota").structuredContent["error"]


def test_puntos_no_corrige_coordenadas_fuera_de_rango_ni_inventa_las_que_faltan():
    hechos: dict = {}
    fc = filas.puntos([{"lat": "6,17", "lon": "-75.59"}, {"lat": "", "lon": "-75"}, {"lat": "-75.5", "lon": "600"}],
                      "lat", "lon", hechos)
    assert [f["geometry"]["coordinates"] for f in fc["features"]] == [[-75.59, 6.17]]  # coma decimal admitida
    assert hechos == {"elementos": 1, "filas_sin_coordenadas": 1, "filas_con_coordenadas_fuera_de_rango": 1}
    with pytest.raises(filas.ErrorColumnas, match="las que hay: lat, lon"):
        filas.puntos([{"lat": 1, "lon": 2}], "latitud", "lon", {})


def test_puntos_mira_varias_filas_porque_socrata_omite_las_nulas():
    fc = filas.puntos([{"nombre": "a"}, {"nombre": "b", "lat": "6.1", "lon": "-75.5"}], "lat", "lon", {})
    assert len(fc["features"]) == 1


# --- CKAN ------------------------------------------------------------------------------------------

def test_ckan_dice_con_que_herramienta_se_trae_cada_recurso(monkeypatch):
    monkeypatch.setattr(ckan.red, "get", lambda url, params=None, **kw: _resp({"success": True, "result": {
        "results": [{"name": "censo-colegios", "title": "Censo de colegios", "organization": {"title": "SED"},
                     "resources": [{"format": "WFS", "url": "https://s.test/MapServer/WFSServer"},
                                   {"format": "Esri REST", "url": "https://s.test/rest/0"},
                                   {"format": "GeoJSON", "url": "https://datosabiertos.bogota.gov.co/x.geojson"},
                                   {"format": "SHP", "url": "https://datosabiertos.bogota.gov.co/x.zip"}]}]}}))
    (p,) = ckan.buscar("https://datosabiertos.bogota.gov.co", "colegios")
    usos = {r["formato"]: r["se_trae_con"] for r in p["recursos"]}
    assert usos["WFS"].startswith("ogc-mcp") and "arcgis" in usos["Esri REST"]
    assert usos["GeoJSON"] == "datos_traer_recurso" and usos["SHP"].startswith("no se puede")


def test_geojson_en_3857_se_declara_tal_cual_no_como_4326(monkeypatch, tmp_path):
    """Medido: los colegios privados de Bogotá vienen en EPSG:3857 (metros); como 4326 salían del mapa."""
    gj = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
          "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-8245438.9, 512592.7]},
                        "properties": {"NOMBRE_COLEGIO": "X"}}]}
    monkeypatch.setattr(ckan.red, "get", lambda url, params=None, **kw: _resp(gj))
    monkeypatch.setattr(srv.resultados, "directorio", str(tmp_path))
    sc = srv.datos_traer_recurso("https://datosabiertos.bogota.gov.co/r/colegios.geojson").structuredContent
    assert sc["artifacts"][0]["crs"] == "EPSG:3857" and sc["facts"]["crs_declarado"] == "EPSG:3857"
    gj.pop("crs")  # sin declarar y en metros: no se adivina la proyección
    assert "no son lon/lat" in srv.datos_traer_recurso(
        "https://datosabiertos.bogota.gov.co/r/colegios.geojson").structuredContent["error"]


def test_recurso_de_otro_dominio_no_se_descarga():
    sc = srv.datos_traer_recurso("https://evil.test/x.geojson").structuredContent
    assert "no es de un portal configurado" in sc["error"]


def test_csv_con_lat_lon_es_capa(monkeypatch, tmp_path):
    texto = "nombre;latitud;longitud\nSede A;4,6;-74,1\nSede B;;\n"
    monkeypatch.setattr(ckan.red, "get", lambda url, params=None, **kw: _resp(texto, tipo="text/csv"))
    monkeypatch.setattr(srv.resultados, "directorio", str(tmp_path))
    sc = srv.datos_traer_recurso("https://datosabiertos.bogota.gov.co/r/sedes.csv", lat="latitud",
                                 lon="longitud").structuredContent
    assert sc["artifacts"][0]["data"]["features"][0]["geometry"]["coordinates"] == [-74.1, 4.6]
    assert sc["facts"]["filas_sin_coordenadas"] == 1


def test_todas_las_tools_con_scope():
    assert {t.name for t in srv.mcp._tool_manager.list_tools()} == set(srv.SCOPES)


def test_describir_da_los_valores_exactos_de_las_columnas_de_texto_con_pocos_valores(monkeypatch):
    """V5: `tipo_ie like '%oficial%'` atrapaba «No Oficial» (dijo 82 oficiales; son 23)."""
    def get(url, params=None, **kw):
        p = dict(params or {})
        if url.endswith("/api/views/g8hy-gwg8.json"):
            return _resp({"name": "IE Envigado", "columns": [{"fieldName": "tipo_ie", "dataTypeName": "text"},
                                                             {"fieldName": "rector", "dataTypeName": "text"},
                                                             {"fieldName": "total", "dataTypeName": "number"}]})
        if p.get("$select") == "count(*)":
            return _resp([{"count": "82"}])
        if p.get("$group") == "tipo_ie":
            return _resp([{"tipo_ie": "No Oficial", "n": "59"}, {"tipo_ie": "Oficial", "n": "23"}, {"n": "0"}])
        if p.get("$group") == "rector":  # muchos valores distintos: no se listan
            return _resp([{"rector": f"R{i}", "n": "1"} for i in range(13)])
        raise AssertionError(p)

    monkeypatch.setattr(socrata.red, "get", get)
    hechos = srv.datos_describir("g8hy-gwg8").structuredContent["facts"]
    assert hechos["valores_de_columnas"] == {"tipo_ie": {"No Oficial": 59, "Oficial": 23, "(vacío)": 0}}


def test_las_herramientas_dicen_los_ids_de_los_portales_y_la_busqueda_los_no_consultados(portal):
    descripciones = {t.name: t.description for t in srv.mcp._tool_manager.list_tools()}
    assert "«bogota» = Datos Abiertos Bogotá (ckan)" in descripciones["datos_buscar"]
    assert "«bogota»" in descripciones["datos_describir"]
    sc = srv.datos_buscar("colegios", portal="datos.gov.co").structuredContent
    assert sc["facts"]["portales_no_consultados"] == ["bogota"]  # V5: concluía «no hay» mirando un solo portal


PORTAL = {"id": "datos.gov.co"}


def test_un_agregado_resume_todo_y_no_es_una_muestra():
    """Terranova F1.3: `count(*)` sobre 12 149 contratos devuelve 1 fila; antes decía «vinieron 1 de
    12149: no es el conjunto completo»."""
    h = filas.hechos("jbjy-vk9h", PORTAL, [{"contratos": "12149"}], 12149, 5000, "ciudad='Chía'",
                     select="count(*) as contratos, sum(valor_del_contrato) as valor_total")
    assert h["completo"] is True and "aviso" not in h and h["filas_que_resume"] == 12149
    assert h["completitud"] == {"vistos": 1, "total": 1, "truncado": False}
    assert h["consulta"]["select"].startswith("count(*)")


def test_un_agregado_con_grupos_hasta_el_tope_puede_estar_cortado():
    h = filas.hechos("x", PORTAL, [{}] * 10, 500, 10, None, select="ciudad, count(*) group by ciudad")
    assert h["completo"] is False and h["completitud"]["truncado"] is True and h["completitud"]["total"] is None


def test_completitud_de_filas_normales():
    assert filas.hechos("x", PORTAL, [{}] * 3, 3, 10, None)["completitud"] == {
        "vistos": 3, "total": 3, "truncado": False}
    muestra = filas.hechos("x", PORTAL, [{}] * 10, 50, 10, None)["completitud"]
    assert muestra == {"vistos": 10, "total": 50, "truncado": True, "motivo": "max_filas=10"}


def test_si_el_total_cambio_entre_peticiones_no_se_usa():
    """Conteo y filas son dos peticiones: con 5 filas y un total de 4, el total no sirve."""
    c = filas.hechos("x", PORTAL, [{}] * 5, 4, 10, None)["completitud"]
    assert c == {"vistos": 5, "total": None, "truncado": False}


def test_un_agregado_no_lleva_orden_por_id(portal):
    """Terranova (canario SECOP): `$order=:id` con count/sum da HTTP 400 en SODA («not in group by»)."""
    srv.datos_consultar("g8hy-gwg8", select="count(*) as n")
    q = [p for u, p in portal if u.endswith(".json") and "$select" in p][0]
    assert "$order" not in q and q["$select"] == "count(*) as n"


def test_un_agregado_con_grupo_pasa_group_having_y_ordena_por_el_grupo(portal):
    """Terranova F5 (G1): ICFES y transacciones por grupo, sin grupos chicos (n mínimo)."""
    sc = srv.datos_consultar("g8hy-gwg8", select="departamento, count(*) AS n", group="departamento",
                             having="count(*) >= 10").structuredContent
    q = [p for u, p in portal if u.endswith(".json") and "$select" in p and p["$select"] != "count(*)"][0]
    assert q["$group"] == "departamento" and q["$having"] == "count(*) >= 10"
    assert q["$order"] == "departamento"  # páginas estables sin `:id`
    assert sc["facts"]["consulta"]["group"] == "departamento"
    assert sc["facts"]["consulta"]["having"] == "count(*) >= 10"


@pytest.mark.parametrize(("argumentos", "texto"), [
    ({"select": "departamento", "group": "departamento"}, "necesitan un `select` con agregados"),
    ({"select": "count(*) AS n", "having": "count(*) > 1"}, "hace falta `group`"),
])
def test_grupos_mal_pedidos_son_errores_de_la_peticion(portal, argumentos, texto):
    sc = srv.datos_consultar("g8hy-gwg8", **argumentos).structuredContent
    assert texto in sc["error"] and sc["codigo"] == "parametro_invalido"
