"""F5 (T5.1) — servidor MCP de SQL multi-BD (services/postgis_mcp).

Sin BD: la configuración de fuentes y que el validador del núcleo (geo_sql_guard) rechace del
lado del SERVIDOR antes de conectarse. Con la PostGIS de tests (`-m postgis`, rol de solo
lectura gc_reader): describir, consultar con y sin geometría, el tope honesto y que una tabla
fuera de los esquemas publicados «no existe».
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "postgis_mcp"))
from postgis_mcp import server as srv

LECTOR_DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://gc_reader:gc_reader_pw@localhost:5434/geocopilot_test")


def test_las_fuentes_salen_de_la_configuracion_y_el_dsn_del_entorno():
    raw = ('[{"id":"a","descripcion":"A","dsn_env":"DSN_A","esquemas":["Catastro"]},'
           '{"id":"b","dsn_env":"DSN_B","esquemas":["x"]}]')
    fuentes = srv.cargar_fuentes(raw, {"DSN_A": "postgresql://u@h/d"})
    assert list(fuentes) == ["a"]  # sin DSN, la fuente se omite (con aviso)
    assert fuentes["a"].esquemas == ["catastro"] and fuentes["a"].descripcion == "A"
    with pytest.raises(ValueError, match="al menos un esquema"):
        srv.cargar_fuentes('[{"id":"c","dsn_env":"DSN_C","esquemas":[]}]', {"DSN_C": "x"})


@pytest.fixture
def fuente_sin_bd(monkeypatch):
    """Una fuente con su catálogo ya conocido: el rechazo ocurre ANTES de conectarse."""
    f = srv.Fuente("cat", "Catastro", "postgresql://nadie@127.0.0.1:1/x", ["catastro"])
    f._tablas = {"catastro.lotes", "catastro.construcciones"}
    monkeypatch.setattr(srv, "FUENTES", {"cat": f})
    return f


@pytest.mark.parametrize(("sql", "motivo"), [
    ("DELETE FROM catastro.lotes", "no es de lectura"),
    ("SELECT rolpassword FROM pg_authid", "catálogo del sistema"),
    ("SELECT * FROM ws_meta.datasets", "no disponible en el catálogo"),
    ("SELECT 1; SELECT 2", "se esperaba 1 statement"),
    ("SELECT pg_sleep(30)", "fuera de la allowlist"),
])
def test_el_servidor_rechaza_lo_que_no_es_una_lectura_de_lo_publicado(fuente_sin_bd, sql, motivo):
    with pytest.raises(srv.ErrorDeConsulta, match=motivo):
        srv._consultar("cat", sql, 10)


def test_una_fuente_que_no_existe_dice_cuales_hay(fuente_sin_bd):
    with pytest.raises(srv.ErrorDeConsulta, match="las que hay: cat"):
        srv._consultar("otra", "SELECT 1", 10)


@pytest.fixture
def fuente_real(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    try:
        psycopg.connect(LECTOR_DSN, connect_timeout=2).close()
    except psycopg.Error as e:
        if os.environ.get("REQUIRE_TEST_DB") == "1":
            pytest.fail(f"REQUIRE_TEST_DB=1 y la PostGIS de tests no responde ({e})")
        pytest.skip(f"PostGIS de tests no disponible ({e})")
    f = srv.Fuente("cat", "Catastro de pruebas", LECTOR_DSN, ["catastro"])
    monkeypatch.setattr(srv, "FUENTES", {"cat": f})
    return f


@pytest.mark.postgis
def test_describe_las_tablas_publicadas_y_una_tabla(fuente_real):
    todas = srv._describir("cat", None)["facts"]["tablas"]
    nombres = {t["tabla"] for t in todas}
    assert {"catastro.lotes", "catastro.construcciones"} <= nombres
    assert not any(n.startswith("tiger.") for n in nombres)  # solo lo publicado
    una = srv._describir("cat", "catastro.lotes")["facts"]
    assert any(g["srid"] == 4326 for g in una["geometria"]) and una["columnas"] and len(una["muestra"]) <= 3


@pytest.mark.postgis
def test_con_geometria_devuelve_una_capa_4326_y_sin_ella_una_tabla(fuente_real):
    gr = srv._consultar("cat", "SELECT * FROM catastro.lotes", 5000)
    capa = gr["artifacts"][0]
    assert capa["kind"] == "feature_collection" and capa["crs"] == "EPSG:4326"
    assert len(capa["data"]["features"]) == gr["facts"]["filas"] > 0 and gr["facts"]["completo"] is True
    assert capa["data"]["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    t = srv._consultar("cat", "SELECT count(*) AS n FROM catastro.lotes", 5000)
    assert t["artifacts"][0]["kind"] == "table" and t["artifacts"][0]["rows"][0]["n"] == gr["facts"]["filas"]
    # V3 F6 (E0.2): el resultado dice qué tablas se leyeron (no se narra una cifra de otra tabla)
    assert t["facts"]["tablas_consultadas"] == ["catastro.lotes"]


@pytest.mark.postgis
def test_el_tope_se_dice_y_lo_no_publicado_no_existe(fuente_real):
    gr = srv._consultar("cat", "SELECT * FROM catastro.lotes", 2)
    assert gr["facts"]["filas"] == 2 and gr["facts"]["completo"] is False and "NO es el total" in gr["facts"]["aviso"]
    with pytest.raises(srv.ErrorDeConsulta, match="no disponible en el catálogo: tiger.state"):
        srv._consultar("cat", "SELECT count(*) FROM tiger.state", 10)


@pytest.mark.postgis
def test_una_columna_que_no_existe_dice_cuales_hay(fuente_real):
    """V3 F5: el LLM probó 5 nombres de columna seguidos hasta agotar el turno; el error de
    PostgreSQL solo decía que no existía. Ahora trae las columnas reales de la tabla usada."""
    res = srv.sql_query("cat", "SELECT * FROM catastro.lotes WHERE municipio = 'X'")
    out = res.structuredContent
    assert res.isError and set(out) == {"error"}
    assert 'column "municipio" does not exist' in out["error"]
    assert "columnas de catastro.lotes:" in out["error"] and "lotcodigo" in out["error"]


@pytest.mark.postgis
def test_describir_una_tabla_trae_los_valores_de_las_columnas_con_pocos(fuente_real):
    """V3 F5: `sector = 'Oficial'` donde el valor es 'OFICIAL' → «ninguna oficial» con 11."""
    hechos = srv.sql_describe("cat", "catastro.lotes")["facts"]
    con_valores = {c["columna"]: c["valores"] for c in hechos["columnas"] if "valores" in c}
    assert con_valores, hechos["columnas"]
    assert all(v and len(v) <= 300 for v in con_valores.values())


@pytest.mark.postgis
def test_las_fuentes_pequenas_traen_sus_tablas_columnas_y_valores(fuente_real):
    """V3 F5: el LLM iba de sql_sources directo a escribir SQL con columnas y valores supuestos."""
    assert "SOLO las fuentes de ESTE servicio" in srv.sql_sources()["facts"]["alcance"]  # F3: «no hay lotes»
    (fuente,) = srv.sql_sources()["facts"]["fuentes"]
    tablas = {t["tabla"]: t["columnas"] for t in fuente["tablas"]}
    assert "catastro.lotes" in tablas and "lotcodigo" in tablas["catastro.lotes"]
    assert any("[valores:" in c for c in tablas["catastro.lotes"])


@pytest.mark.postgis
def test_cero_filas_y_resultado_sin_geometria_traen_su_hecho(fuente_real):
    """V3 F5: `WHERE nombre_mun = 'Soacha'` (el dato es 'SOACHA') → «no hay sedes»; y
    `ST_AsGeoJSON(geom)` → una tabla que el LLM intentó cargar como URL `data:`."""
    vacio = srv.sql_query("cat", "SELECT lotcodigo FROM catastro.lotes WHERE lotcodigo = 'no-existe'").structuredContent["facts"]
    assert vacio["filas"] == 0 and "UPPER()/ILIKE" in vacio["aviso"]
    tabla = srv.sql_query("cat", "SELECT lotcodigo, ST_AsGeoJSON(shape) AS g FROM catastro.lotes LIMIT 2").structuredContent["facts"]
    assert "TABLA" in tabla["nota"] and "tal cual" in tabla["nota"]
    capa = srv.sql_query("cat", "SELECT lotcodigo, shape FROM catastro.lotes LIMIT 2").structuredContent["facts"]
    assert "nota" not in capa


@pytest.mark.postgis
def test_una_capa_grande_viaja_como_archivo_completa(fuente_real, monkeypatch, tmp_path):
    """El usuario: «debe traer todo». Antes el tope era 5000 filas en la respuesta MCP; ahora una capa
    con más de MAX_EN_LINEA va como GeoJSON servido por el servidor (el núcleo la baja al workspace)."""
    import json

    monkeypatch.setattr(srv, "MAX_EN_LINEA", 1)
    monkeypatch.setattr(srv, "RESULTADOS", str(tmp_path))
    gr = srv._consultar("cat", "SELECT lotcodigo, shape FROM catastro.lotes", srv.POR_DEFECTO)
    (art,) = gr["artifacts"]
    assert art["kind"] == "feature_ref" and art["format"] == "geojson" and art["crs"] == "EPSG:4326"
    assert gr["facts"]["completo"] is True and art["feature_count"] == gr["facts"]["filas"] > 1
    ident = srv._RESULTADO_RE.match(art["uri"]).group(1)
    fc = json.loads((tmp_path / f"{ident}.geojson").read_text(encoding="utf-8"))
    assert len(fc["features"]) == art["feature_count"]
    # una tabla (sin geometría) no va al mapa: conserva el tope de filas
    monkeypatch.setattr(srv, "MAX_FILAS", 1)
    t = srv._consultar("cat", "SELECT lotcodigo FROM catastro.lotes", srv.POR_DEFECTO)
    assert t["artifacts"][0]["kind"] == "table" and t["facts"]["completo"] is False


@pytest.mark.postgis
def test_un_area_filtra_en_el_origen_y_una_tabla_inexistente_dice_cuales_hay(fuente_real):
    """V5 (sql): «los lotes de la localidad de Teusaquillo» — la tabla no tiene localidad; el LLM adivinó
    `lotes`, `localidades`… hasta agotar el turno. Ahora: el rechazo dice qué tablas hay, y un área
    (el límite que trae el geocodificador) filtra en la base."""
    todos = srv._consultar("cat", "SELECT lotcodigo, shape FROM catastro.lotes", srv.POR_DEFECTO)
    feats = todos["artifacts"][0]["data"]["features"]
    # el área = la caja del primer lote: deja ese lote (y quizá vecinos), no todos
    xs = [c[0] for anillo in feats[0]["geometry"]["coordinates"][:1] for c in anillo] if feats[0]["geometry"]["type"] == "Polygon" \
        else [c[0] for pol in feats[0]["geometry"]["coordinates"] for c in pol[0]]
    ys = [c[1] for anillo in feats[0]["geometry"]["coordinates"][:1] for c in anillo] if feats[0]["geometry"]["type"] == "Polygon" \
        else [c[1] for pol in feats[0]["geometry"]["coordinates"] for c in pol[0]]
    caja = {"type": "Polygon", "coordinates": [[[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)],
                                                [min(xs), max(ys)], [min(xs), min(ys)]]]}
    area = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": caja, "properties": {}}]}
    dentro = srv._consultar("cat", "SELECT lotcodigo, shape FROM catastro.lotes", srv.POR_DEFECTO, area)
    assert 1 <= dentro["facts"]["filas"] < todos["facts"]["filas"] and "filtro_espacial" in dentro["facts"]
    # F3 (verdades ×5): un RESUMEN también se filtra por el área (antes: «el resultado no tiene
    # geometría», y el agente escribía el polígono a mano: 55 sedes en Soacha, eran 41)
    conteo = srv._consultar("cat", "SELECT count(*) AS n FROM catastro.lotes", 10, area)
    assert conteo["artifacts"][0]["rows"][0]["n"] == dentro["facts"]["filas"]
    con_alias = srv._consultar("cat", "SELECT count(l.lotcodigo) AS n FROM catastro.lotes AS l", 10, area)
    assert con_alias["artifacts"][0]["rows"][0]["n"] == dentro["facts"]["filas"]
    with pytest.raises(srv.ErrorDeConsulta, match="varias tablas"):
        srv._consultar("cat", "SELECT count(*) FROM catastro.lotes l JOIN catastro.construcciones c "
                              "ON c.lotecodigo = l.lotcodigo", 10, area)
    with pytest.raises(srv.ErrorDeConsulta, match=r"Tablas publicadas en «cat»: .*catastro\.lotes"):
        srv._consultar("cat", "SELECT * FROM lotes", 10)


@pytest.mark.postgis
def test_una_geometria_escrita_en_el_sql_se_dice(fuente_real):
    """V5 (sql): un rectángulo escrito a mano trajo 34.738 lotes «de Teusaquillo» que no eran la localidad."""
    gr = srv._consultar("cat", "SELECT lotcodigo, shape FROM catastro.lotes WHERE ST_Intersects(shape, "
                               "ST_MakeEnvelope(-75, 4, -73, 5, 4326))", 10)
    assert "NO está contada dentro del límite" in gr["facts"]["aviso_area"]
    assert "aviso_area" not in srv._consultar("cat", "SELECT lotcodigo, shape FROM catastro.lotes", 10)["facts"]


@pytest.mark.postgis
def test_un_cero_con_un_filtro_de_texto_exacto_trae_los_valores_parecidos(fuente_real):
    """F3 (verdades ×5): `count(*) … WHERE nombre_mun = 'Soacha'` da UNA fila con 0 (el aviso de «sin
    filas» no salta) y el agente respondía «no hay sedes en Soacha»; hay 41, escritas 'SOACHA'.
    Aquí, con un código real cortado: el = exacto no lo encuentra y la tabla sí tiene parecidos."""
    real = srv.sql_query("cat", "SELECT lotcodigo FROM catastro.lotes WHERE lotcodigo IS NOT NULL LIMIT 1")
    codigo = real.structuredContent["artifacts"][0]["rows"][0]["lotcodigo"]
    parte = codigo[:8]
    hechos = srv.sql_query("cat", f"SELECT count(*) AS n FROM catastro.lotes WHERE lotcodigo = '{parte}'"
                           ).structuredContent["facts"]
    assert hechos["filas"] == 1  # una fila con n = 0: no es «sin filas»
    parecidos = hechos["valores_parecidos"][f"lotcodigo = '{parte}'"]
    assert parecidos and all(parte in v for v in parecidos)
    assert "NO significa que no haya" in hechos["aviso_texto"]
    # con resultado, ni se busca
    con = srv.sql_query("cat", f"SELECT count(*) AS n FROM catastro.lotes WHERE lotcodigo = '{codigo}'"
                        ).structuredContent["facts"]
    assert "valores_parecidos" not in con


@pytest.mark.postgis
def test_un_sql_sin_filtro_dice_que_cubre_toda_la_tabla(fuente_real):
    """F3 (verdades ×5): `SELECT count(*) FROM sedes` → «en Soacha hay 2000» (todo Cundinamarca)."""
    todo = srv._consultar("cat", "SELECT count(*) AS n FROM catastro.lotes", 10)["facts"]
    assert "TODA la tabla" in todo["sin_filtro"]
    filtrado = srv._consultar("cat", "SELECT count(*) AS n FROM catastro.lotes WHERE lotcodigo LIKE '0%'", 10)["facts"]
    assert "sin_filtro" not in filtrado


@pytest.mark.postgis
def test_los_avisos_van_primero_en_los_hechos(fuente_real):
    """El juez recibe cada resultado recortado a 400 caracteres: con muchas columnas, un aviso al final
    («geometría escrita a mano», «sin filtro») no le llegaba."""
    hechos = srv._consultar("cat", "SELECT * FROM catastro.lotes WHERE ST_Intersects(shape, "
                                   "ST_MakeEnvelope(-74.1, 4.6, -74.0, 4.7, 4326))", 10)["facts"]
    assert next(iter(hechos)) == "aviso_area"
