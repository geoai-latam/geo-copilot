"""F5 (T5.2) — servidor MCP de ArcGIS (services/arcgis_mcp), sin red.

Lo que antes eran conectores del núcleo, ahora del lado del servidor: la guarda SSRF, el paso
de esriJSON a GeoJSON (con multipolígonos de verdad), la consulta con el filtro EMPUJADO al
servicio y paginación que respeta su `maxRecordCount`, los hechos honestos (muestra vs total),
la descripción con la extensión en EPSG:4326 y la búsqueda en el Hub.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "arcgis_mcp"))
from arcgis_mcp import hub, red, rest
from arcgis_mcp import server as srv

# ---------------------------------------------------------------------------
# Guarda SSRF
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url, motivo", [
    ("http://127.0.0.1/arcgis/rest/services/x/FeatureServer/0", "IP interna"),
    ("http://169.254.169.254/latest/meta-data", "IP interna"),
    ("http://10.0.0.5/arcgis/rest/services", "IP interna"),
    ("https://services.arcgis.com:22/x", "puerto"),
    ("ftp://services.arcgis.com/x", "esquema"),
    ("https:///sin-host", "host"),
])
def test_la_guarda_ssrf_bloquea_lo_interno_antes_de_pedir(url, motivo):
    with pytest.raises(red.UrlNoPermitida, match=motivo):
        red.ip_validada(url)


def test_la_lista_de_dominios_restringe_si_se_configura():
    with pytest.raises(red.UrlNoPermitida, match="dominio"):
        red.ip_validada("http://127.0.0.1/x", ["arcgis.com"])


def test_la_tool_devuelve_el_rechazo_como_error_legible(monkeypatch):
    out = srv.arcgis_query_features("http://169.254.169.254/arcgis/rest/services/x/FeatureServer/0")
    assert out.isError and set(out.structuredContent) == {"error"} and "IP interna" in out.content[0].text


# ---------------------------------------------------------------------------
# esriJSON → GeoJSON
# ---------------------------------------------------------------------------

EXT_CW = [[0, 0], [0, 10], [10, 10], [10, 0], [0, 0]]          # exterior ArcGIS: horario
HUECO_CCW = [[2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]            # hueco ArcGIS: antihorario
ISLA_CW = [[20, 20], [20, 25], [25, 25], [25, 20], [20, 20]]    # otro exterior


def test_geometrias_simples():
    assert rest.geometria_geojson({"x": 1, "y": 2}) == {"type": "Point", "coordinates": [1, 2]}
    assert rest.geometria_geojson({"x": None, "y": None}) is None
    assert rest.geometria_geojson({"paths": [[[0, 0], [1, 1]]]})["type"] == "LineString"
    assert rest.geometria_geojson({"paths": [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]})["type"] == "MultiLineString"
    assert rest.geometria_geojson({"points": [[0, 0], [1, 1]]})["type"] == "MultiPoint"
    assert rest.geometria_geojson({}) is None


def test_un_poligono_con_hueco_queda_con_hueco_y_orientacion_rfc7946():
    g = rest.geometria_geojson({"rings": [EXT_CW, HUECO_CCW]})
    assert g["type"] == "Polygon" and len(g["coordinates"]) == 2
    assert rest._area_firmada(g["coordinates"][0]) > 0   # exterior antihorario
    assert rest._area_firmada(g["coordinates"][1]) < 0   # hueco horario


def test_varios_exteriores_son_un_multipoligono_no_huecos():
    """Antes una isla se dibujaba como AGUJERO del primer polígono."""
    g = rest.geometria_geojson({"rings": [EXT_CW, ISLA_CW, HUECO_CCW]})
    assert g["type"] == "MultiPolygon" and len(g["coordinates"]) == 2
    principal = next(p for p in g["coordinates"] if [0, 0] in p[0])
    assert len(principal) == 2  # el hueco quedó en el polígono que lo contiene, no en la isla


# ---------------------------------------------------------------------------
# Consultar: pushdown, paginación y hechos
# ---------------------------------------------------------------------------

CAPA = "https://services.example.org/arcgis/rest/services/Sedes/FeatureServer/0"


def _servicio(total: int, max_record: int, *, geometria: str | None = "esriGeometryPoint", registro: list | None = None):
    registro = registro if registro is not None else []
    feats = [{"attributes": {"OBJECTID": i + 1, "NOMBRE": f"sede {i + 1}"}, "geometry": {"x": -74 + i / 100, "y": 4.6}}
             for i in range(total)]

    def handler(req: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(urlparse(str(req.url)).query).items()}
        registro.append((req.url.path, q))
        if req.url.path.endswith("/query") and q.get("returnCountOnly") == "true":
            return httpx.Response(200, json={"count": total})
        if req.url.path.endswith("/query"):
            ini, n = int(q.get("resultOffset", 0)), int(q.get("resultRecordCount", max_record))
            n = min(n, max_record)
            pagina = feats[ini:ini + n]
            return httpx.Response(200, json={"features": pagina, "exceededTransferLimit": ini + n < total})
        return httpx.Response(200, json={"name": "Sedes", "geometryType": geometria, "objectIdField": "OBJECTID",
                                         "maxRecordCount": max_record, "capabilities": "Query",
                                         "fields": [{"name": "OBJECTID", "type": "esriFieldTypeOID"},
                                                    {"name": "NOMBRE", "type": "esriFieldTypeString"}],
                                         "extent": {"xmin": -74.1, "ymin": 4.5, "xmax": -74.0, "ymax": 4.7,
                                                    "spatialReference": {"wkid": 4326}}})

    return handler


@pytest.fixture
def mock_http(monkeypatch):
    def instalar(handler):
        monkeypatch.setattr(rest, "cliente", lambda url, **kw: httpx.Client(transport=httpx.MockTransport(handler)))
        monkeypatch.setattr(srv, "_url_permitida", lambda url: url)
    return instalar


def test_trae_todo_paginando_con_el_tope_del_servicio_y_orden_estable(mock_http):
    """maxRecordCount 2 < página 1000: antes la primera página «corta» se tomaba como la última."""
    reg: list = []
    mock_http(_servicio(5, 2, registro=reg))
    fc, hechos = rest.consultar(CAPA, where="SECTOR = 'OFICIAL'", bbox=[-75, 4, -73, 5], out_fields=["NOMBRE"])
    assert len(fc["features"]) == 5 and hechos["completo"] is True and hechos["total_en_servicio"] == 5
    assert "aviso" not in hechos
    paginas = [q for p, q in reg if p.endswith("/query") and "resultOffset" in q]
    assert sorted(q["resultOffset"] for q in paginas) == ["0", "2", "4"]  # en paralelo: el orden de llegada varía
    q = paginas[0]  # el filtro va AL SERVICIO
    assert q["where"] == "SECTOR = 'OFICIAL'" and q["outFields"] == "NOMBRE" and q["outSR"] == "4326"
    assert q["geometry"] == "-75,4,-73,5" and q["inSR"] == "4326" and q["orderByFields"] == "OBJECTID"
    assert q["geometryPrecision"] == "7"  # ~1 cm: ArcGIS manda 15 decimales si no se pide


def test_una_muestra_se_declara_muestra(mock_http):
    mock_http(_servicio(50, 1000))
    fc, hechos = rest.consultar(CAPA, max_features=10)
    assert len(fc["features"]) == 10 and hechos["completo"] is False
    assert "MUESTRA" in hechos["aviso"] and "10 de 50" in hechos["aviso"]


def test_errores_que_se_pueden_corregir(mock_http):
    mock_http(_servicio(3, 10, geometria=None))
    with pytest.raises(rest.ErrorArcGIS, match="no tiene geometría"):
        rest.consultar(CAPA)
    with pytest.raises(rest.ErrorArcGIS, match="es imagen"):
        rest.consultar("https://x.org/arcgis/rest/services/Orto/ImageServer")
    with pytest.raises(rest.ErrorArcGIS, match="bbox"):
        rest.consultar(CAPA, bbox=[-75, 4, -73])

    def error(req):
        return httpx.Response(200, json={"error": {"code": 400, "message": "Invalid where", "details": ["campo X"]}})
    mock_http(error)
    with pytest.raises(rest.ErrorArcGIS, match="Invalid where.*campo X"):
        rest.consultar(CAPA)


def test_la_tool_devuelve_una_capa_en_4326_con_sus_hechos(mock_http):
    mock_http(_servicio(3, 1000))
    res = srv.arcgis_query_features(CAPA, where="1=1")
    out = res.structuredContent
    assert out["geo_result"] == "1" and not res.isError
    (art,) = out["artifacts"]
    assert art["kind"] == "feature_collection" and art["crs"] == "EPSG:4326" and art["name"] == "Sedes"
    assert len(art["data"]["features"]) == 3 and out["facts"]["completo"] is True
    # el texto es un RESUMEN (hechos + cuántos elementos), no la geometría otra vez
    resumen = json.loads(res.content[0].text)
    assert resumen["artifacts"] == [{"kind": "feature_collection", "name": "Sedes", "crs": "EPSG:4326", "features": 3}]
    assert "coordinates" not in res.content[0].text


def test_una_capa_grande_viaja_entera_como_archivo_que_sirve_el_servidor(mock_http, monkeypatch, tmp_path):
    """E2.1 (V3 F5): 41.033 puntos llegaban cortados a 10.000; ahora la capa entera va por referencia."""
    import asyncio

    monkeypatch.setattr(srv, "MAX_EN_LINEA", 4)
    monkeypatch.setattr(srv, "RESULTADOS", str(tmp_path))
    mock_http(_servicio(9, 1000))
    out = srv.arcgis_query_features(CAPA, max_features=200_000).structuredContent
    (art,) = out["artifacts"]
    assert art["kind"] == "feature_ref" and art["format"] == "geojson" and art["crs"] == "EPSG:4326"
    assert art["feature_count"] == 9 and out["facts"]["completo"] is True
    assert art["uri"].startswith("/resultados/") and art["uri"].endswith(".geojson")
    # la ruta del propio servidor lo sirve (y solo con el nombre que dio)
    ident = srv._RESULTADO_RE.match(art["uri"]).group(1)
    enviado: list = []

    async def send(msg):
        enviado.append(msg)
    asyncio.run(srv._servir_resultado({}, send, ident, None))
    assert enviado[0]["status"] == 200
    assert len(json.loads(enviado[1]["body"])["features"]) == 9
    assert srv._RESULTADO_RE.match("/resultados/../../etc/passwd") is None


def test_un_servicio_sin_indice_usa_su_capa_0(mock_http):
    reg: list = []
    mock_http(_servicio(1, 10, registro=reg))
    rest.consultar(CAPA.rsplit("/", 1)[0])
    assert all(p.startswith("/arcgis/rest/services/Sedes/FeatureServer/0") for p, _ in reg)


# ---------------------------------------------------------------------------
# Describir
# ---------------------------------------------------------------------------


def test_describir_una_capa(mock_http):
    mock_http(_servicio(7, 1000))
    d = rest.describir(CAPA)
    assert d["es_capa"] and d["nombre"] == "Sedes" and d["tipo_geometria"] == "Point" and d["elementos"] == 7
    assert d["campos"][1] == {"name": "NOMBRE", "alias": None, "type": "String"}
    assert d["extent_4326"] == [-74.1, 4.5, -74.0, 4.7] and d["consultable"] is True


def test_describir_un_imageserver_reproyecta_la_extension_y_da_el_descriptor(mock_http):
    def handler(req):
        return httpx.Response(200, json={"name": "Orto", "bandCount": 3, "pixelType": "U8",
                                         "extent": {"xmin": -8238310.0, "ymin": 504757.0, "xmax": -8226310.0,
                                                    "ymax": 516757.0, "spatialReference": {"wkid": 102100,
                                                                                           "latestWkid": 3857}}})
    mock_http(handler)
    d = rest.describir("https://x.org/arcgis/rest/services/Orto/ImageServer/")
    x0, y0, x1, y1 = d["extent_4326"]
    assert -74.1 < x0 < x1 < -73.7 and 4.5 < y0 < y1 < 4.7  # Bogotá, no metros
    assert d["imagen"]["export_url"].endswith("/ImageServer/exportImage")
    assert d["imagen"]["extent"]["xmin"] == x0


def test_mapserver_sin_extension_en_la_raiz_usa_la_de_sus_capas(mock_http):
    def handler(req):
        if req.url.path.endswith("/MapServer"):
            return httpx.Response(200, json={"mapName": "Vias", "layers": [{"id": 0, "name": "a"}, {"id": 1, "name": "b"}]})
        i = int(req.url.path.rsplit("/", 1)[-1])
        return httpx.Response(200, json={"extent": {"xmin": -74 - i, "ymin": 4, "xmax": -73, "ymax": 5 + i,
                                                    "spatialReference": {"wkid": 4326}}})
    mock_http(handler)
    d = rest.describir("https://x.org/arcgis/rest/services/Vias/MapServer")
    assert d["extent_4326"] == [-75, 4, -73, 6] and d["imagen"]["export_url"] is None
    assert [c["nombre"] for c in d["capas"]] == ["a", "b"]


def test_sin_referencia_espacial_no_se_adivina():
    assert rest.extent_4326({"xmin": 500000, "ymin": 500000, "xmax": 510000, "ymax": 510000}) is None
    assert rest.extent_4326({"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4, "spatialReference": {"wkid": 4326}}) == [1, 2, 3, 4]


# ---------------------------------------------------------------------------
# Hub
# ---------------------------------------------------------------------------


def _item(nombre, url, *, tipo="Feature Service", extent=None, capa=None):
    return {"id": nombre, "attributes": {"name": nombre, "url": url, "type": tipo, "owner": "igac",
                                         "tags": ["educación"], "extent": {"coordinates": extent} if extent else None, "layerId": capa,
                                         "itemId": f"id-{nombre}", "thumbnail": "t.png"}}


def test_la_busqueda_en_el_hub_filtra_normaliza_y_deduplica(monkeypatch):
    enviados: list = []
    data = [
        _item("Sedes Soacha", "https://s.arcgis.com/x/FeatureServer", extent=[[-74.3, 4.5], [-74.1, 4.6]], capa=0),
        _item("Sedes Soacha (copia)", "https://s.arcgis.com/x/FeatureServer", extent=[[-74.3, 4.5], [-74.1, 4.6]], capa=0),
        _item("Mapa web", "https://www.arcgis.com/apps/x", tipo="Web Map", extent=[[-74.3, 4.5], [-74.1, 4.6]]),
        _item("Hospitales Texas", "https://t.arcgis.com/y/FeatureServer/0", extent=[[-106, 25], [-93, 36]]),
        _item("Sin extensión", "https://s.arcgis.com/z/MapServer"),
    ]

    def handler(req):
        if req.url.host != "opendata.arcgis.com":
            return httpx.Response(200, json={"results": []})  # ArcGIS Online: sin resultados en este test
        enviados.append(parse_qs(urlparse(str(req.url)).query))
        return httpx.Response(200, json={"data": data})
    monkeypatch.setattr(hub, "cliente", lambda url, **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    items, avisos = hub.buscar(text_query="sedes educativas", tags_any=["educación"], service_types=["Feature Service"],
                               bbox=[-74.5, 4.4, -74.0, 4.8])
    assert [i["title"] for i in items] == ["Sedes Soacha"] and avisos == []
    it = items[0]
    assert it["service_type"] == "FeatureServer" and it["layer_id"] == 0 and it["extent"] == [-74.3, 4.5, -74.1, 4.6]
    assert it["hub_url"].endswith("id=id-Sedes Soacha") and it["thumbnail_url"].endswith("/info/t.png")
    q = enviados[0]
    assert q["q"] == ["sedes educativas"] and q["filter[tags]"] == ["any(educación)"]
    assert q["filter[type]"] == ["Feature Service"] and "sort" not in q  # con texto: relevancia


def test_una_pagina_caida_se_dice(monkeypatch):
    monkeypatch.setattr(hub, "cliente", lambda url, **kw: httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(503))))
    items, avisos = hub.buscar(tags_any=["vías"])
    assert items == []
    assert any("ArcGIS Online falló" in a for a in avisos) and any("página 1 del Hub" in a for a in avisos)


def _online(titulo, url, *, owner="SecretariaMovilidad", creditos="Secretaría Distrital de Movilidad", vistas=60008,
            extent=((-74.39, 3.82), (-73.99, 4.84)), claves=("Feature Service", "Singlelayer"), tipo="Feature Service"):
    return {"id": f"it-{titulo}", "title": titulo, "url": url, "type": tipo, "owner": owner,
            "accessInformation": creditos, "numViews": vistas, "scoreCompleteness": 98,
            "typeKeywords": list(claves), "extent": [list(extent[0]), list(extent[1])],
            "snippet": "Conjunto de líneas que definen los ejes viales", "modified": 1721741073000, "tags": ["vías"]}


def _dos_buscadores(monkeypatch, *, online, hub_v3, online_status=200):
    pedidos: dict[str, list] = {"online": [], "hub": []}

    def handler(req):
        q = parse_qs(urlparse(str(req.url)).query)
        if req.url.host == "www.arcgis.com":
            pedidos["online"].append(q)
            return httpx.Response(online_status, json={"results": online, "nextStart": -1})
        pedidos["hub"].append(q)
        return httpx.Response(200, json={"data": hub_v3})
    monkeypatch.setattr(hub, "cliente", lambda url, **kw: httpx.Client(transport=httpx.MockTransport(handler)))
    return pedidos


BOGOTA = [-74.3, 4.45, -73.95, 4.85]


def test_arcgis_online_filtra_la_zona_en_el_servidor_y_trae_los_hechos_para_juzgar_la_fuente(monkeypatch):
    """Rama arcgis-busqueda: «vías de Bogotá» daba 0 en el Hub v3 (aplica el bbox sobre los primeros 50
    resultados mundiales); ArcGIS Online devuelve la Malla Vial Integral de la Secretaría de Movilidad."""
    pedidos = _dos_buscadores(monkeypatch, hub_v3=[], online=[
        _online("Malla Vial Integral Bogota D_C", "https://services2.arcgis.com/NEw/arcgis/rest/services/MVI/FeatureServer"),
        _online("Rutas del mundo", "https://x.arcgis.com/w/FeatureServer", extent=((-180, -90), (180, 90))),
        _online("Un mapa web", "https://www.arcgis.com/apps/x", tipo="Web Map"),
    ])
    items, avisos = hub.buscar(text_query="malla vial", bbox=BOGOTA)
    assert avisos == [] and [i["title"] for i in items] == ["Malla Vial Integral Bogota D_C"]
    it = items[0]
    assert it["sources"] == ["arcgis_online"] and it["source"] == "arcgis_online"
    assert (it["credits"], it["owner"], it["views"], it["completeness"], it["single_layer"]) == (
        "Secretaría Distrital de Movilidad", "SecretariaMovilidad", 60008, 98, True)
    assert it["org"] == "Secretaría Distrital de Movilidad" and it["modified"] == "2024-07-23"
    assert it["service_type"] == "FeatureServer" and it["layer_id"] is None
    q = pedidos["online"][0]
    assert q["bbox"] == ["-74.3,4.45,-73.95,4.85"]  # la zona la filtra el servidor
    assert q["q"][0].startswith("malla vial ") and 'type:"Feature Service"' in q["q"][0]


def test_los_dos_buscadores_se_fusionan_sin_duplicados_y_con_los_hechos_de_ambos(monkeypatch):
    url = "https://services2.arcgis.com/NEw/arcgis/rest/services/MVI/FeatureServer"
    _dos_buscadores(monkeypatch, online=[
        _online("Malla Vial Integral", url),
        _online("Ciclorrutas", "https://s.arcgis.com/c/FeatureServer"),
    ], hub_v3=[
        _item("Malla Vial Integral (Hub)", url + "/", extent=[[-74.39, 3.82], [-73.99, 4.84]]),
        _item("Colegios SED", "https://s.arcgis.com/col/FeatureServer", extent=[[-74.2, 4.5], [-74.0, 4.8]]),
    ])
    items, _ = hub.buscar(text_query="vías", bbox=BOGOTA)
    assert [i["title"] for i in items] == ["Malla Vial Integral", "Ciclorrutas", "Colegios SED"]
    assert items[0]["sources"] == ["arcgis_online", "hub"]  # el mismo servicio, una vez
    assert items[2]["sources"] == ["hub"]


def test_si_arcgis_online_cae_quedan_los_resultados_del_hub_y_se_dice(monkeypatch):
    _dos_buscadores(monkeypatch, online=[], online_status=500, hub_v3=[
        _item("Colegios SED", "https://s.arcgis.com/col/FeatureServer", extent=[[-74.2, 4.5], [-74.0, 4.8]])])
    items, avisos = hub.buscar(text_query="colegios", bbox=BOGOTA)
    assert [i["title"] for i in items] == ["Colegios SED"]
    assert avisos and "ArcGIS Online falló" in avisos[0]


def test_la_consulta_de_arcgis_online_traduce_los_filtros():
    q = hub.consulta_online(text_query="resguardos", tags_all=None, tags_any=["indígena", "ANT"],
                            owner_any=["ANT_Colombia"], service_types=["Feature Layer", "Map Service"],
                            modified_after="2024-01-01", only_loadable=True)
    assert q.startswith('resguardos (tags:"indígena" OR tags:"ANT") (owner:"ANT_Colombia")')
    assert '(type:"Feature Service" OR type:"Map Service")' in q and "modified:[" in q
    # solo `source_any` (concepto del Hub): ArcGIS Online no se consulta con el catálogo mundial
    assert hub.consulta_online(text_query=None, tags_all=None, tags_any=None, owner_any=None,
                               service_types=None, modified_after=None, only_loadable=True) is None


def test_arcgis_online_busca_con_y_sin_tildes():
    """Medido: ArcGIS Online distingue tildes. «Malla Vial Bogotá» no encontraba la Malla Vial de la
    Secretaría de Movilidad (titulada «Bogota D_C», 60 mil vistas); sin tilde sale primera."""
    q = hub.consulta_online(text_query="Malla Vial Bogotá", tags_all=None, tags_any=None, owner_any=None,
                            service_types=None, modified_after=None, only_loadable=False)
    assert q == "((Malla Vial Bogotá) OR (Malla Vial Bogota))"
    assert hub.consulta_online(text_query="malla vial", tags_all=None, tags_any=None, owner_any=None,
                               service_types=None, modified_after=None, only_loadable=False) == "malla vial"
    assert hub.sin_tildes("Bogotá, Nariño, Útica") == "Bogota, Narino, Utica"


def test_busquedas_invalidas():
    with pytest.raises(hub.BusquedaInvalida, match="al menos un filtro"):
        hub.buscar()
    with pytest.raises(hub.BusquedaInvalida, match="tipos de servicio"):
        hub.buscar(text_query="x", service_types=["Shapefile"])


def test_la_tool_de_busqueda_devuelve_items_como_hechos(monkeypatch):
    monkeypatch.setattr(hub, "buscar", lambda **kw: ([{"title": "a"}], ["la página 2 del Hub falló"]))
    out = srv.arcgis_search_items(text_query="x").structuredContent
    assert out["facts"] == {"items": [{"title": "a"}], "encontrados": 1, "avisos": ["la página 2 del Hub falló"]}
    assert json.dumps(out)  # serializable tal cual


def test_sin_claves_no_arranca(monkeypatch):
    monkeypatch.delenv("ARCGIS_MCP_KEYS", raising=False)
    with pytest.raises(RuntimeError, match="sin claves"):
        srv.build_app()


def test_la_descripcion_del_hub_llega_en_texto_plano():
    """V5 (auditoría pre-producción): las tarjetas mostraban «<p><span style="font-family:&quot;Avenir…»
    y al LLM le llegaba CSS en lugar de la descripción."""
    from arcgis_mcp.hub import normalizar, texto_plano

    html = ('<p><span style="font-family:&quot;Avenir Next W01&quot;">Coberturas <b>2017</b> del parque'
            '</span></p><style>.x{color:red}</style><script>alert(1)</script>&nbsp;&amp; más')
    assert texto_plano(html) == "Coberturas 2017 del parque & más"
    item = {"attributes": {"url": "https://s.example/arcgis/rest/services/X/FeatureServer/0", "description": html}}
    assert normalizar(item)["description"] == "Coberturas 2017 del parque & más"


def test_la_capa_del_hub_y_el_servicio_de_arcgis_online_son_el_mismo_dataset(monkeypatch):
    url = "https://services2.arcgis.com/NEw/arcgis/rest/services/MVI/FeatureServer"
    _dos_buscadores(monkeypatch, online=[_online("Malla Vial Integral", url)], hub_v3=[
        _item("Malla Vial Integral (capa)", url + "/0", extent=[[-74.39, 3.82], [-73.99, 4.84]], capa=0),
        _item("Otra capa del mismo servicio", url + "/3", extent=[[-74.39, 3.82], [-73.99, 4.84]], capa=3)])
    items, _ = hub.buscar(text_query="malla vial", bbox=BOGOTA)
    assert [(i["title"], i["layer_id"], i["sources"]) for i in items] == [
        ("Malla Vial Integral", 0, ["arcgis_online", "hub"]),     # una vez, con la capa que dio el Hub
        ("Otra capa del mismo servicio", 3, ["hub"])]             # otra capa: otro dataset


def test_una_pagina_que_no_llega_a_tiempo_se_pide_a_la_mitad(mock_http, monkeypatch):
    """V5 (rama arcgis-busqueda): los polígonos del RUNAP no cabían en el plazo con páginas de 1000 y la
    carga fallaba entera con «ReadTimeout». Ahora la misma página se pide más pequeña."""
    reg: list = []
    base = _servicio(300, 1000, registro=reg)

    def lento(req):
        q = {k: v[0] for k, v in parse_qs(urlparse(str(req.url)).query).items()}
        if req.url.path.endswith("/query") and int(q.get("resultRecordCount", 0) or 0) > 250:
            raise httpx.ReadTimeout("lento", request=req)
        return base(req)

    mock_http(lento)
    monkeypatch.setattr(rest, "PAGINAS_EN_PARALELO", 1)  # una sola página de 300
    fc, hechos = rest.consultar(CAPA)
    assert len(fc["features"]) == 300 and hechos["completo"] is True
    # la página de 300 no llegó a tiempo: se partió (150 + 150) y cada mitad sí llegó
    traidas = sorted((int(q["resultOffset"]), int(q["resultRecordCount"])) for p, q in reg
                     if p.endswith("/query") and "resultOffset" in q and int(q["resultRecordCount"]) <= 250)
    assert traidas == [(0, 150), (150, 150)]


def test_si_ni_la_pagina_minima_llega_se_dice_que_no_respondio_a_tiempo(mock_http):
    def siempre_lento(req):
        if req.url.path.endswith("/query") and "resultOffset" in str(req.url):
            raise httpx.ReadTimeout("lento", request=req)
        return _servicio(300, 1000)(req)

    mock_http(siempre_lento)
    with pytest.raises(rest.TiempoAgotado, match="no respondió a tiempo"):
        rest.consultar(CAPA)


def test_pocos_elementos_pero_pesados_viajan_como_archivo(mock_http, monkeypatch, tmp_path):
    """V5 (rama arcgis-busqueda): 1882 polígonos del RUNAP = 110 MB en la respuesta MCP; el cliente se
    cansaba de esperar. El peso también decide."""
    monkeypatch.setattr(srv, "MAX_BYTES_EN_LINEA", 200)  # las 9 sedes pesan más que esto
    monkeypatch.setattr(srv, "RESULTADOS", str(tmp_path))
    mock_http(_servicio(9, 1000))
    (art,) = srv.arcgis_query_features(CAPA, max_features=200_000).structuredContent["artifacts"]
    assert art["kind"] == "feature_ref" and art["feature_count"] == 9



def test_las_paginas_se_piden_a_la_vez_y_el_resultado_es_el_mismo_y_en_orden(mock_http, monkeypatch):
    """Rama arcgis-busqueda: la malla vial de Bogotá (137 mil líneas) tardaba 91 s en 137 páginas una tras
    otra. Con el total conocido y orden por OBJECTID se piden a la vez; el resultado, idéntico y ordenado."""
    import threading
    import time as _time

    activas = {"ahora": 0, "max": 0}
    candado = threading.Lock()
    base = _servicio(10_000, 1000)

    def lento(req):
        if req.url.path.endswith("/query") and "resultOffset" in str(req.url):
            with candado:
                activas["ahora"] += 1
                activas["max"] = max(activas["max"], activas["ahora"])
            _time.sleep(0.05)
            with candado:
                activas["ahora"] -= 1
        return base(req)

    mock_http(lento)
    fc, hechos = rest.consultar(CAPA, max_features=200_000)
    ids = [f["properties"]["OBJECTID"] for f in fc["features"]]
    assert ids == list(range(1, 10_001)) and hechos["completo"] is True
    assert activas["max"] > 1  # de verdad a la vez


def test_si_el_servicio_devuelve_menos_de_lo_pedido_se_completa_el_tramo(mock_http):
    """Geometrías pesadas: el servicio corta antes (exceededTransferLimit) aunque se pidan 1000."""
    base = _servicio(2500, 1000)  # anuncia maxRecordCount 1000…

    def corto(req):
        r = base(req)
        if req.url.path.endswith("/query") and "resultOffset" in str(req.url):
            d = r.json()
            d["features"] = d["features"][:400]  # …pero corta en 400 (límite de transferencia)
            d["exceededTransferLimit"] = True
            return httpx.Response(200, json=d)
        return r

    mock_http(corto)
    fc, hechos = rest.consultar(CAPA, max_features=200_000)
    assert [f["properties"]["OBJECTID"] for f in fc["features"]] == list(range(1, 2501))


def test_lo_que_cabe_en_una_pagina_se_reparte_entre_las_que_van_a_la_vez(mock_http):
    """V5 (rama arcgis-busqueda): los 1882 polígonos del RUNAP cabían en UNA página de 2000; nada iba en
    paralelo y esa página de 110 MB no llegaba a tiempo (139 s). Se reparten en tantas como van a la vez."""
    reg: list = []
    mock_http(_servicio(300, 1000, registro=reg))
    fc, _ = rest.consultar(CAPA)
    assert len(fc["features"]) == 300
    paginas = sorted(int(q["resultOffset"]) for p, q in reg if p.endswith("/query") and "resultOffset" in q)
    assert len(paginas) == rest.PAGINAS_EN_PARALELO and paginas[0] == 0
