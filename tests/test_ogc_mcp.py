"""ogc-mcp: catálogo curado, capacidades WMS/WFS/OGC API, elementos con filtro en el servicio y teselas WMS.

Servicios simulados con la forma de las respuestas reales medidas el 2026-10-15 (PNN GeoServer, IGAC y SGC
ArcGIS Server). Las cifras de verdad (RUNAP 169 en el bbox de Bogotá, 151 manzanas en Luruaco, 33
departamentos DIVIPOLA) están en tests/test_ogc_mcp_vivo.py (marca `integration`).
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "ogc_mcp"))
from ogc_mcp import catalogo, elementos, ows, teselas
from ogc_mcp import server as srv

WMS_CAPS = b"""<?xml version="1.0"?>
<WMS_Capabilities version="1.3.0" xmlns="http://www.opengis.net/wms">
 <Service><Title>Amenaza sismica</Title>
  <ContactInformation><ContactPersonPrimary><ContactOrganization>SGC</ContactOrganization>
  </ContactPersonPrimary></ContactInformation></Service>
 <Capability>
  <Request><GetMap><Format>image/png</Format><Format>image/jpeg</Format></GetMap></Request>
  <Layer><Title>raiz</Title><CRS>EPSG:4326</CRS><CRS>EPSG:3857</CRS>
   <EX_GeographicBoundingBox><westBoundLongitude>-79</westBoundLongitude><eastBoundLongitude>-66</eastBoundLongitude>
    <southBoundLatitude>-4</southBoundLatitude><northBoundLatitude>13</northBoundLatitude></EX_GeographicBoundingBox>
   <Layer queryable="1"><Name>5</Name><Title>PGA75</Title><Abstract>Aceleracion</Abstract>
    <Style><Name>default</Name></Style></Layer>
   <Layer><Name>runap</Name><Title>RUNAP</Title><CRS>EPSG:4686</CRS>
    <EX_GeographicBoundingBox><westBoundLongitude>-84.8</westBoundLongitude>
     <eastBoundLongitude>-65</eastBoundLongitude><southBoundLatitude>-3.6</southBoundLatitude>
     <northBoundLatitude>16.1</northBoundLatitude></EX_GeographicBoundingBox></Layer>
  </Layer>
 </Capability>
</WMS_Capabilities>"""

WMS_SIN_3857 = WMS_CAPS.replace(b"<CRS>EPSG:3857</CRS>", b"")

WFS_CAPS = b"""<?xml version="1.0"?>
<wfs:WFS_Capabilities version="2.0.0" xmlns:wfs="http://www.opengis.net/wfs/2.0"
  xmlns:ows="http://www.opengis.net/ows/1.1">
 <ows:ServiceIdentification><ows:Title>GeoServer WFS</ows:Title></ows:ServiceIdentification>
 <ows:ServiceProvider><ows:ProviderName>Parques Nacionales Naturales de Colombia</ows:ProviderName>
 </ows:ServiceProvider>
 <ows:OperationsMetadata><ows:Operation name="GetFeature"><ows:Parameter name="outputFormat"><ows:AllowedValues>
  <ows:Value>text/xml; subtype=gml/3.2</ows:Value><ows:Value>application/json</ows:Value>
 </ows:AllowedValues></ows:Parameter></ows:Operation></ows:OperationsMetadata>
 <wfs:FeatureTypeList><wfs:FeatureType><wfs:Name>pnn:runap</wfs:Name><wfs:Title>RUNAP</wfs:Title>
  <wfs:DefaultCRS>urn:ogc:def:crs:EPSG::4326</wfs:DefaultCRS>
  <ows:WGS84BoundingBox><ows:LowerCorner>-84.8 -3.6</ows:LowerCorner><ows:UpperCorner>-65 16.1</ows:UpperCorner>
  </ows:WGS84BoundingBox></wfs:FeatureType>
 <wfs:FeatureType><wfs:Name>pnn:predio</wfs:Name><wfs:Title>Predios</wfs:Title></wfs:FeatureType>
 </wfs:FeatureTypeList>
</wfs:WFS_Capabilities>"""


XSD_GEOSERVER = b"""<?xml version="1.0"?><xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"
  xmlns:gml="http://www.opengis.net/gml/3.2"><xsd:complexType name="runapType"><xsd:complexContent>
  <xsd:extension base="gml:AbstractFeatureType"><xsd:sequence>
   <xsd:element name="ap_id" type="xsd:int"/><xsd:element name="ap_nombre" type="xsd:string"/>
   <xsd:element name="geom" type="gml:MultiSurfacePropertyType"/>
  </xsd:sequence></xsd:extension></xsd:complexContent></xsd:complexType>
  <xsd:element name="runap" substitutionGroup="gml:AbstractFeature" type="pnn:runapType"/></xsd:schema>"""

XSD_ARCGIS = b"""<?xml version="1.0" encoding="UTF-8"?><xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema">
 <xsd:element name="Departamento" type="x:DepartamentoFeatureType" substitutionGroup="gml:AbstractFeature"/>
 <xsd:complexType name="DepartamentoFeatureType"><xsd:complexContent><xsd:extension base="gml:AbstractFeatureType">
  <xsd:sequence><xsd:element name="OBJECTID" type="xsd:int"/>
   <xsd:element name="Nombre_departamento"><xsd:simpleType><xsd:restriction base="xsd:string">
    <xsd:maxLength value="250"/></xsd:restriction></xsd:simpleType></xsd:element>
   <xsd:element name="SHAPE" type="gml:MultiSurfacePropertyType"/>
  </xsd:sequence></xsd:extension></xsd:complexContent></xsd:complexType></xsd:schema>"""


def _resp(contenido: bytes | str | dict, tipo: str = "text/xml", status: int = 200) -> httpx.Response:
    req = httpx.Request("GET", "https://x.test/")
    if isinstance(contenido, dict):
        return httpx.Response(status, json=contenido, request=req)
    cuerpo = contenido.encode() if isinstance(contenido, str) else contenido
    return httpx.Response(status, content=cuerpo, headers={"content-type": tipo}, request=req)


def _feature(i: int) -> dict:
    return {"type": "Feature", "properties": {"ap_nombre": f"Area {i}", "ap_id": i},
            "geometry": {"type": "Point", "coordinates": [-74.0 + i * 1e-4, 4.5]}}


@pytest.fixture
def servicio(monkeypatch):
    """`ows.pedir` simulado: responde según el `request` OGC; registra cada petición."""
    pedidos: list[tuple[str, dict]] = []
    estado = {"total": 2500, "wms": WMS_CAPS, "png": b"\x89PNG\r\n\x1a\nfalso"}

    def pedir(url, params=None):
        p = dict(params or {})
        pedidos.append((url, p))
        req = p.get("request")
        if req == "GetCapabilities":
            return _resp(estado["wms"] if p.get("service") == "WMS" else WFS_CAPS)
        if req == "GetFeature" and p.get("resultType") == "hits":
            return _resp(f'<wfs:FeatureCollection numberMatched="{estado["total"]}" numberReturned="0"/>')
        if req == "GetFeature":
            inicio, n = int(p["startIndex"]), int(p["count"])
            fin = min(inicio + n, estado["total"])
            return _resp({"type": "FeatureCollection", "features": [_feature(i) for i in range(inicio, fin)]})
        if req == "DescribeFeatureType":
            return _resp(XSD_GEOSERVER if "runap" in p["typeNames"] else XSD_ARCGIS)
        if req == "GetMap":
            es_png = estado["png"].startswith(b"\x89PNG")
            return _resp(estado["png"], tipo="image/png" if es_png else "text/xml")
        raise AssertionError(f"petición inesperada {p}")

    monkeypatch.setattr(ows, "pedir", pedir)
    # los hosts *.test no resuelven: la validación SSRF real se prueba aparte (sin este fixture)
    monkeypatch.setattr(srv, "ip_validada", lambda url, dominios=None: ("93.184.216.34", "x.test"))
    monkeypatch.setenv("OGC_MCP_KEYS", '[{"name":"t","key":"k","scopes":["ogc:read"]}]')
    ows._cache.clear()
    teselas._cache.clear()
    return pedidos, estado


# --- catálogo --------------------------------------------------------------------------------------

def test_el_catalogo_real_es_valido_y_cada_entrada_tiene_url_https():
    catalogo.cargar.cache_clear()
    entradas = catalogo.cargar()
    assert len(entradas) >= 30
    assert len({e["id"] for e in entradas}) == len(entradas)
    for e in entradas:
        urls = [e[k] for k in ("wms", "wfs", "ogcapi") if e.get(k)]
        assert urls and all(u.startswith("https://") for u in urls), e["id"]
        assert e.get("verificado"), e["id"]  # cada entrada dice cuándo se comprobó en vivo


def test_catalogo_ordena_por_palabras_que_coinciden_sin_tildes_y_dice_cuando_no_hay():
    hechos = catalogo.buscar("amenaza sísmica")
    primeros = hechos["servicios"][:2]
    assert {s["id"] for s in primeros} == {"sgc_amenaza_sismica", "sgc_zonas_sismicas_nsr10"}
    assert all(s["palabras_que_coinciden"] == 2 for s in primeros)
    assert any(s["id"] == "sgc_amenaza_volcanica" for s in hechos["servicios"][2:])  # «amenaza» sola, detrás
    # V5: «departamentos Colombia» del DANE daba 0 exigiendo TODAS las palabras
    dane = catalogo.buscar("departamentos Colombia", entidad="DANE")["servicios"]
    assert {s["id"] for s in dane} == {"dane_mgn_2025", "dane_divipola_2025"}
    assert catalogo.buscar(entidad="parques")["encontrados"] == 1
    vacio = catalogo.buscar("unicornios")
    assert vacio["encontrados"] == 0 and "ogc_describir" in vacio["nota"]
    assert isinstance(catalogo.buscar()["servicios"][0]["verificado"], str)  # JSON, no fecha


# --- capacidades -----------------------------------------------------------------------------------

def test_wms_capas_con_crs_y_extension_heredados_de_la_madre(servicio):
    caps = ows.capacidades_wms("https://x.test/wms?service=WMS&request=GetCapabilities")
    assert caps["url"] == "https://x.test/wms"  # sin los parámetros OGC
    assert caps["entidad"] == "SGC" and caps["titulo"] == "Amenaza sismica"
    pga, runap = caps["capas"]
    assert pga["nombre"] == "5" and pga["titulo"] == "PGA75" and pga["estilos"] == ["default"]
    assert pga["bbox_4326"] == [-79.0, -4.0, -66.0, 13.0]  # heredada de la raíz
    assert {"EPSG:3857", "EPSG:4326"} <= pga["_crs"]
    assert runap["bbox_4326"] == [-84.8, -3.6, -65.0, 16.1] and "EPSG:4686" in runap["_crs"]


def test_wfs_detecta_geojson_y_extension(servicio):
    caps = ows.capacidades_wfs("https://x.test/wfs")
    assert caps["formato_geojson"] == "application/json"
    assert caps["entidad"] == "Parques Nacionales Naturales de Colombia"
    assert caps["capas"][0] == {"nombre": "pnn:runap", "titulo": "RUNAP", "resumen": None,
                                "bbox_4326": [-84.8, -3.6, -65.0, 16.1],
                                "crs_defecto": "urn:ogc:def:crs:EPSG::4326"}


def test_excepcion_ogc_y_xml_con_entidades_son_errores_honestos(servicio, monkeypatch):
    monkeypatch.setattr(ows, "pedir", lambda u, p=None: _resp(
        b'<ServiceExceptionReport><ServiceException code="X">capa mala</ServiceException></ServiceExceptionReport>'))
    with pytest.raises(ows.ErrorOgc, match="capa mala"):
        ows.capacidades_wms("https://otro.test/wms")
    bomba = (b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
             b"<WMS_Capabilities>&e;</WMS_Capabilities>")
    monkeypatch.setattr(ows, "pedir", lambda u, p=None: _resp(bomba))
    with pytest.raises(ows.ErrorOgc, match="no es un XML"):
        ows.capacidades_wms("https://tercero.test/wms")


def test_describir_prueba_protocolos_y_resumir_acota(servicio):
    hechos = ows.resumir(ows.describir("https://x.test/geoserver/wfs"), filtro="runap")
    assert hechos["tipo"] == "WFS" and hechos["total_capas"] == 2 and hechos["coinciden_con_filtro"] == 1
    assert all(not k.startswith("_") for c in hechos["capas"] for k in c)


# --- elementos -------------------------------------------------------------------------------------

def test_wfs_bbox_en_orden_lat_lon_y_paginado_con_conteo(servicio):
    pedidos, _ = servicio
    fc, hechos = elementos.traer("https://x.test/wfs", "runap", [-74.5, 4.0, -73.5, 5.0], 2200, "wfs")
    getf = [p for _, p in pedidos if p.get("request") == "GetFeature" and "count" in p]
    # medido en PNN e IGAC: con urn EPSG:4326 el bbox va lat/lon (169 contra 0 al revés)
    assert getf[0]["bbox"] == "4.0,-74.5,5.0,-73.5,urn:ogc:def:crs:EPSG::4326"
    assert getf[0]["typeNames"] == "pnn:runap"  # el nombre corto se resuelve al del servicio
    assert getf[0]["srsName"] == "EPSG:4326" and getf[0]["outputFormat"] == "application/json"
    assert [p["startIndex"] for p in getf] == [0, 1000, 2000] and len(fc["features"]) == 2200
    assert hechos["elementos"] == 2200 and hechos["en_el_servicio_dentro_del_filtro"] == 2500
    assert hechos["completa_dentro_del_filtro"] is False and "completa" not in hechos  # «completa» es del recorte
    assert hechos["en_el_servicio_sin_filtro"] == 2500  # el total de la capa entera, aparte
    assert "no es la capa completa" in hechos["aviso"]
    assert "campos" not in hechos  # van en las filas; en los hechos solo ocupaban la observación


def test_wfs_completo_no_avisa_y_capa_inexistente_lista_las_que_hay(servicio):
    _, estado = servicio
    estado["total"] = 40
    _, hechos = elementos.traer("https://x.test/wfs", "pnn:runap", None, 5000, "wfs")
    assert hechos["elementos"] == 40 and hechos["completa"] is True and "aviso" not in hechos
    with pytest.raises(ows.ErrorOgc, match="pnn:runap, pnn:predio"):
        elementos.traer("https://x.test/wfs", "veredas", None, 10, "wfs")


@pytest.mark.parametrize("bbox", [[1, 2, 3], [-74, 5, -75, 6], [-200, 0, 0, 1]])
def test_bbox_invalido_se_rechaza(servicio, bbox):
    with pytest.raises(ows.ErrorOgc, match="bbox"):
        elementos.traer("https://x.test/wfs", "runap", bbox, 10, "wfs")


def test_un_wms_no_trae_elementos():
    with pytest.raises(ows.ErrorOgc, match="ogc_ver_mapa"):
        elementos.traer("https://x.test/wms", "5", None, 10, "wms")


def test_ogcapi_pagina_por_next_solo_del_mismo_host(monkeypatch):
    llamadas: list[str] = []
    paginas = {
        "https://api.test/collections": {"collections": [{"id": "rios", "title": "Ríos",
                                                           "extent": {"spatial": {"bbox": [[-80, -5, -66, 13]]}}}]},
        "https://api.test/collections/rios/items": {
            "numberMatched": 3, "features": [_feature(0), _feature(1)],
            "links": [{"rel": "next", "href": "https://api.test/collections/rios/items?offset=2"}]},
        "https://api.test/collections/rios/items?offset=2": {
            "features": [_feature(2)], "links": [{"rel": "next", "href": "https://malo.test/robar"}]},
    }

    def pedir(url, params=None):
        llamadas.append(url)
        return _resp(paginas[url])

    monkeypatch.setattr(ows, "pedir", pedir)
    ows._cache.clear()
    fc, hechos = elementos.traer("https://api.test/collections/rios", "rios", [-75, 4, -74, 5], 100, "ogcapi")
    assert len(fc["features"]) == 3 and hechos["en_el_servicio_dentro_del_filtro"] == 3
    assert hechos["completa_dentro_del_filtro"] is True
    assert hechos["protocolo"] == "OGC API - Features"
    assert "https://malo.test/robar" not in llamadas  # un `next` a otro host no se sigue


# --- teselas WMS -----------------------------------------------------------------------------------

def test_bbox_3857_de_teselas_conocidas():
    assert teselas.bbox_3857(0, 0, 0) == pytest.approx((-20037508.34, -20037508.34, 20037508.34, 20037508.34))
    minx, miny, maxx, maxy = teselas.bbox_3857(1, 1, 0)
    assert (minx, miny) == pytest.approx((0, 0)) and maxx == pytest.approx(20037508.34)
    assert teselas.tesela_xyz_de(-74.08, 4.6, 6) == (18, 31)


def test_la_ficha_firmada_no_se_puede_manipular(servicio):
    f = teselas.ficha("https://x.test/wms", "5", "", "1.3.0", "EPSG:3857")
    spec, z, x, y = teselas.parse_ruta(f"/wms/{f}/6/18/31.png")
    assert spec == {"u": "https://x.test/wms", "l": "5", "s": "", "v": "1.3.0", "c": "EPSG:3857"} and z == 6
    datos, firma = f.split(".")
    otra = base64.urlsafe_b64encode(json.dumps({"u": "https://evil.test/", "l": "5", "s": "", "v": "1.3.0",
                                                "c": "EPSG:3857"}).encode()).decode().rstrip("=")
    assert teselas.parse_ruta(f"/wms/{otra}.{firma}/6/18/31.png") is None
    assert teselas.parse_ruta(f"/wms/{f}/3/9/0.png") is None  # x fuera de rango en z=3


def test_ver_mapa_teselas_por_el_servidor_y_estilo_validado(servicio):
    pedidos, _ = servicio
    sc = srv.ogc_ver_mapa("https://x.test/wms", "5").structuredContent
    (art,) = sc["artifacts"]
    assert art["kind"] == "raster_tiles" and art["tiles"].startswith("/wms/") and art["tiles"].endswith(
        "/{z}/{x}/{y}.png")
    assert sc["facts"]["capa"] == "PGA75" and sc["facts"]["entidad"] == "SGC" and "crs_teselas" not in sc["facts"]
    spec = teselas.parse_ruta(art["tiles"].format(z=6, x=18, y=31))
    assert teselas.getmap(*spec).startswith(b"\x89PNG")
    getmap = [p for _, p in pedidos if p.get("request") == "GetMap"][-1]
    assert getmap["crs"] == "EPSG:3857" and getmap["width"] == 256 and getmap["layers"] == "5"
    assert "error" in srv.ogc_ver_mapa("https://x.test/wms", "5", estilo="inventado").structuredContent


def test_capa_sin_3857_declarado_se_comprueba_con_una_tesela(servicio):
    _, estado = servicio
    estado["wms"] = WMS_SIN_3857
    sc = srv.ogc_ver_mapa("https://x.test/wms", "5").structuredContent
    assert "lo atiende" in sc["facts"]["crs_teselas"]  # hecho comprobado, no supuesto
    ows._cache.clear()
    estado["png"] = b"<ServiceExceptionReport/>"
    assert "web-mercator" in srv.ogc_ver_mapa("https://y.test/wms", "5").structuredContent["error"]


# --- servidor --------------------------------------------------------------------------------------

def test_elementos_grandes_viajan_por_referencia(servicio, monkeypatch, tmp_path):
    _, estado = servicio
    estado["total"] = 6000
    monkeypatch.setattr(srv, "RESULTADOS", str(tmp_path))
    sc = srv.ogc_traer_elementos("https://x.test/wfs", "runap", tipo="wfs", max_elementos=6000).structuredContent
    (art,) = sc["artifacts"]
    assert art["kind"] == "feature_ref" and art["feature_count"] == 6000
    assert art["uri"].startswith("/resultados/") and (tmp_path / art["uri"].split("/")[-1]).exists()


def test_url_interna_rechazada_y_todas_las_tools_con_scope():
    assert "puerto no permitido" in srv.ogc_describir("http://127.0.0.1:9800/x").structuredContent["error"]
    assert "IP interna" in srv.ogc_describir("http://localhost/wms").structuredContent["error"]
    registradas = {t.name for t in srv.mcp._tool_manager.list_tools()}
    assert registradas == set(srv.SCOPES)  # toda tool nueva necesita su scope o la app responde 403


def test_area_deja_solo_los_que_la_intersecan_no_su_rectangulo(servicio):
    """V5: «áreas protegidas en Bogotá» filtró por el rectángulo del límite y contó Tibaitatá (Mosquera)."""
    pedidos, estado = servicio
    estado["total"] = 40  # puntos en x = -74.0 + i·1e-4, y = 4.5
    triangulo = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[-74.0, 4.4], [-73.99, 4.4], [-74.0, 4.6], [-74.0, 4.4]]]}}]}
    fc, hechos = elementos.traer("https://x.test/wfs", "runap", None, 5000, "wfs", area=triangulo)
    getf = [p for _, p in pedidos if p.get("request") == "GetFeature" and "count" in p][-1]
    assert getf["bbox"].startswith("4.4,-74.0,4.6,-73.99")  # el servicio filtra por el rectángulo del área
    # en y=4.5 el triángulo va de x=-74.0 a x=-73.995: entran i = 0..50 → los 40 puntos caben… salvo que
    # el triángulo se estrecha: comprobado con shapely, no a ojo
    from shapely.geometry import Point, shape
    esperados = [i for i in range(40) if shape(triangulo["features"][0]["geometry"]).intersects(
        Point(-74.0 + i * 1e-4, 4.5))]
    assert len(fc["features"]) == len(esperados) == hechos["elementos"]
    assert hechos["filtro_area"]["en_su_rectangulo"] == 40
    assert hechos["filtro_area"]["intersecan_el_area"] == len(esperados)
    with pytest.raises(ows.ErrorOgc, match="area"):
        elementos.traer("https://x.test/wfs", "runap", None, 10, "wfs", area={"features": []})


def test_ver_mapa_resuelve_por_titulo_exacto_y_unico(servicio):
    """V5: en el WMS de ArcGIS del DANE la capa es «9» y su título «Departamento»; pedir el título fallaba."""
    sc = srv.ogc_ver_mapa("https://x.test/wms", "PGA75").structuredContent
    assert sc["facts"]["nombre_en_servicio"] == "5" and "es el título" in sc["facts"]["capa_resuelta"]
    sc = srv.ogc_ver_mapa("https://x.test/wms", "Serv:pga75").structuredContent  # nombre del WFS: prefijo fuera
    assert sc["facts"]["nombre_en_servicio"] == "5"
    assert "nombre (título)" in srv.ogc_ver_mapa("https://x.test/wms", "PGA").structuredContent["error"]


def test_pagina_demasiado_pesada_se_pide_en_mitades_y_el_peso_total_corta_con_aviso(servicio, monkeypatch):
    """V5: 1000 polígonos del RUNAP pasaban de 40 MB en UNA página y la herramienta fallaba entera."""
    pedidos, estado = servicio
    estado["total"] = 900
    original = ows.pedir

    def pesada(url, params=None):
        if (params or {}).get("count") and int(params["count"]) > 250:
            raise ows.RespuestaGrande("la respuesta del servicio pasa de 40 MB")
        return original(url, params)

    monkeypatch.setattr(ows, "pedir", pesada)
    fc, hechos = elementos.traer("https://x.test/wfs", "runap", None, 900, "wfs")
    assert len(fc["features"]) == 900 and hechos["completa"] is True
    assert max(int(p["count"]) for _, p in pedidos if "count" in p) == 250  # 1000 → 500 → 250
    monkeypatch.setattr(elementos, "MAX_BYTES_TOTAL", 1)  # el tope de memoria del servidor
    fc, hechos = elementos.traer("https://x.test/wfs", "runap", None, 900, "wfs")
    assert len(fc["features"]) == 250 and hechos["completa"] is False and "corte_por_peso" in hechos
    monkeypatch.setattr(elementos, "PAGINA_MINIMA", 500)
    with pytest.raises(ows.RespuestaGrande, match="Acota con `area`"):
        elementos.traer("https://x.test/wfs", "runap", None, 900, "wfs")


def test_campos_deja_solo_los_atributos_pedidos_y_rechaza_los_que_no_hay(servicio):
    """V5: 23 atributos por área del RUNAP → en la observación cabían 2 de 13 filas; el agente elige cuáles."""
    _, estado = servicio
    estado["total"] = 5
    fc, hechos = elementos.traer("https://x.test/wfs", "runap", None, 10, "wfs", campos=["AP_NOMBRE"])
    assert [f["properties"] for f in fc["features"]][:2] == [{"ap_nombre": "Area 0"}, {"ap_nombre": "Area 1"}]
    assert hechos["campos_traidos"] == ["ap_nombre"]
    with pytest.raises(ows.ErrorOgc, match="los suyos: ap_id, ap_nombre"):
        elementos.traer("https://x.test/wfs", "runap", None, 10, "wfs", campos=["nombre"])


def test_describir_acotado_a_pocas_capas_wfs_trae_sus_atributos(servicio):
    """V5: sin los nombres reales, el agente pidió `campos` «nombre, categoría» (son ap_nombre…)."""
    sc = srv.ogc_describir("https://x.test/wfs", "wfs", "runap").structuredContent
    (capa,) = sc["facts"]["capas"]
    assert capa["campos"] == ["ap_id (int)", "ap_nombre (string)", "geom (geometría)"]
    assert ows.campos_wfs("https://x.test/wfs", "x:Departamento") == [
        "OBJECTID (int)", "Nombre_departamento (string)", "SHAPE (geometría)"]  # forma ArcGIS (restriction)
    sin_filtro = srv.ogc_describir("https://x.test/wfs", "wfs").structuredContent  # 2 capas: también
    assert all("campos" in c for c in sin_filtro["facts"]["capas"])
