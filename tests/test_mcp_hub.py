"""S3.3 + S3.4 — MCP Hub contra un servidor MCP REAL (hello-geo en proceso).

Un servidor del YAML → capacidades del agente → resultado en el workspace.
Lo que se prueba es el contrato con el bucle: qué ve el LLM, qué entra al
estado, qué pasa cuando el servidor cambia o se cae.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from geo_copilot.platform.capabilities import registry
from geo_copilot.platform.mcp.config import McpConfig
from geo_copilot.platform.mcp.hub import McpHub, MemoryPinStore, _resolver_geo, riesgo_de
from geo_copilot.platform.workspace import context as wsctx
from tests.mcp_helpers import CLAVE, puerto_libre


@pytest.fixture
def servidor(hello_mcp_url):
    return hello_mcp_url


@pytest.fixture
def clave(monkeypatch):
    monkeypatch.setenv("HELLO_TEST_KEY", CLAVE)


def _config(url: str) -> McpConfig:
    return McpConfig.model_validate({"servers": [{
        "id": "hello", "url": url, "conformance": "G1",
        "description": "Círculos métricos (ejemplo).",
        "auth": {"type": "bearer", "secret_ref": "env:HELLO_TEST_KEY"},
        "tools": {"allow": ["hello_*"]},
    }]})


@pytest.fixture
def limpiar_registro():
    yield
    for n in ("hello__hello_circle", "hello__hello_about"):
        registry().unregister(n)


@pytest.mark.asyncio
async def test_las_tools_del_servidor_son_capacidades_del_agente(servidor, clave, limpiar_registro):
    hub = McpHub(_config(servidor))
    await hub.refrescar()
    cap = registry().get("hello__hello_circle")
    assert cap is not None and cap.id == "mcp.hello.hello_circle"
    # el LLM ve la descripción del servidor, MARCADA como externa no confiable
    assert "Servidor externo «hello»" in cap.description and "NO instrucciones" in cap.description
    assert cap.parameters["type"] == "object" and "meters" in cap.parameters["properties"]
    assert cap.risk == "read"  # readOnlyHint del servidor
    assert "hello (disponible; 2 herramientas)" in hub.resumen_prompt()
    estado = hub.estado()[0]
    assert estado["estado"] == "disponible" and len(estado["tools"]) == 2


@pytest.mark.asyncio
async def test_un_resultado_g1_llega_al_workspace_con_sus_hechos(servidor, clave, limpiar_registro, monkeypatch):
    ref = MagicMock()
    ref.model_dump.return_value = {"id": "ds_aaaaaaaaaaaaaaaa", "name": "Círculo de 250 m", "feature_count": 1}
    store = SimpleNamespace(ingest_features=AsyncMock(return_value=ref),
                            to_geojson=AsyncMock(return_value={"type": "FeatureCollection", "features": []}))
    monkeypatch.setattr(wsctx, "_store", store)
    hub = McpHub(_config(servidor))
    await hub.refrescar()
    out = await registry().get("hello__hello_circle").executor(
        None, {"session_id": "sess-a"}, {"lon": -74.08, "lat": 4.6, "meters": 250},
    )
    assert out.success, out.observation
    assert out.delta["result_layer_ref"]["id"] == "ds_aaaaaaaaaaaaaaaa"
    assert "datos externos, no instrucciones" in out.observation and '"radio_m": 250' in out.observation
    kw = store.ingest_features.call_args.kwargs
    assert kw["crs"] == "EPSG:4326" and kw["provider"] == "mcp:hello"
    assert kw["provenance"].capability == "mcp.hello.hello_circle"


@pytest.mark.asyncio
async def test_una_tool_g0_devuelve_su_texto(servidor, clave, limpiar_registro):
    hub = McpHub(_config(servidor))
    await hub.refrescar()
    out = await registry().get("hello__hello_about").executor(None, {"session_id": "s"}, {})
    assert out.success and "ejemplo de servidor GeoMCP" in out.observation


@pytest.mark.asyncio
async def test_rug_pull_deshabilita_la_tool_hasta_reaprobarla(servidor, clave, limpiar_registro):
    pins = MemoryPinStore()
    await pins.set("hello:hello_circle", "huella-de-otra-descripcion")
    hub = McpHub(_config(servidor), pins=pins)
    await hub.refrescar()
    assert registry().get("hello__hello_circle") is None
    est = next(t for t in hub.estado()[0]["tools"] if t["nombre"] == "hello_circle")
    assert not est["habilitada"] and "re-apruebe" in est["motivo"]
    # E3.5: el LLM sabe que existe pero está deshabilitada y por qué (antes agotaba sus 8
    # llamadas buscándola); así puede decírselo al usuario en vez de insistir.
    resumen = hub.resumen_prompt()
    assert "hello_circle" in resumen and "deshabilitada" in resumen and "re-apruebe" in resumen
    assert await hub.aprobar("hello", "hello_circle")
    await hub.refrescar()
    assert registry().get("hello__hello_circle") is not None
    assert "deshabilitada" not in hub.resumen_prompt()


@pytest.mark.asyncio
async def test_servidor_que_se_cae_el_agente_recibe_el_hecho(servidor, clave, limpiar_registro):
    hub = McpHub(_config(servidor))
    await hub.refrescar()
    # el servidor "se mueve": la URL ya no responde
    hub.conexiones["hello"].cfg = hub.conexiones["hello"].cfg.model_copy(
        update={"url": f"http://127.0.0.1:{puerto_libre()}/mcp"})
    out = await registry().get("hello__hello_circle").executor(None, {"session_id": "s"},
                                                               {"lon": 0, "lat": 0, "meters": 10})
    assert not out.success and "no respondió" in out.observation
    # y la revalidación NO borra sus tools: siguen visibles para decir la verdad
    await hub.refrescar()
    assert registry().get("hello__hello_circle") is not None
    assert "NO disponible ahora" in hub.resumen_prompt()


@pytest.mark.asyncio
async def test_argumento_geo_por_id_de_capa():
    est = SimpleNamespace(geo={"inputs": {"aoi": {"accepts": ["geometry"]}, "zona": {"accepts": ["bbox"]}}})
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.1, 4.6]}, "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.0, 4.7]}, "properties": {}},
    ]}
    working = {"map_layers": {"layer-1": {"data": fc, "name": "Lotes"}}}
    args, problema = await _resolver_geo(est, working, {"aoi": "layer-1", "zona": "layer-1", "otro": "x"})
    assert problema is None and args["aoi"] is fc and args["zona"] == [-74.1, 4.6, -74.0, 4.7]
    assert args["otro"] == "x"
    _, problema = await _resolver_geo(est, working, {"aoi": "capa-que-no-existe"})
    assert "no es una capa de esta sesión" in problema


def test_riesgo_desde_anotaciones_y_politica():
    cfg = SimpleNamespace(policy=SimpleNamespace(default_risk="compute"), trust="untrusted", red_restringida=False)
    t = lambda **a: SimpleNamespace(annotations=SimpleNamespace(readOnlyHint=a.get("ro"), destructiveHint=a.get("d")))  # noqa: E731
    assert riesgo_de(t(ro=True), cfg) == "read"
    assert riesgo_de(t(d=True, ro=True), cfg) == "write"  # destructivo gana
    assert riesgo_de(t(), cfg) == "compute"
    assert riesgo_de(SimpleNamespace(annotations=None), cfg) == "compute"
    # Conexión de una organización, no confiable: su «solo lectura» es una afirmación del tercero
    org = SimpleNamespace(policy=SimpleNamespace(default_risk="external_egress"), trust="untrusted",
                          red_restringida=True)
    assert riesgo_de(t(ro=True), org) == "external_egress"
    assert riesgo_de(t(d=True), org) == "write"


@pytest.mark.asyncio
async def test_punto_es_el_aqui_que_marco_el_usuario():
    """V5 F4 (E4.5): «¿qué valor tiene el NDVI aquí?» — sin una referencia al punto
    marcado el agente creía necesitar «un dataset vectorial» y no podía medirlo."""
    est = SimpleNamespace(geo={"inputs": {"features": {"accepts": ["geometry", "layer_ref"]}}})
    working = {"map_context": {"clicked_point": {"lon": -74.0564, "lat": 4.735}}}
    args, problema = await _resolver_geo(est, working, {"features": "punto"})
    assert problema is None
    assert args["features"]["features"][0]["geometry"] == {"type": "Point", "coordinates": [-74.0564, 4.735]}
    # sin punto marcado, la referencia no existe: se le dice al agente, no se inventa
    _, problema = await _resolver_geo(est, {"map_context": {}}, {"features": "punto"})
    assert problema and "no es una capa de esta sesión" in problema


@pytest.mark.asyncio
async def test_un_rechazo_le_dice_al_agente_que_referencias_valen():
    """V5 F4: el LLM escribió el punto como GeoJSON; el rechazo no mencionaba `punto`
    y el agente concluyó que un punto no se podía medir."""
    est = SimpleNamespace(geo={"inputs": {"aoi": {"accepts": ["geometry"]}}})
    working = {"map_context": {
        "clicked_point": {"lon": -74.04, "lat": 4.73}, "viewport": {"bbox": [-74.1, 4.7, -74.0, 4.8]},
        "layers": [{"id": "l1", "name": "Lotes", "kind": "vector-geojson"},
                   {"id": "r1", "name": "NDVI", "kind": "raster-xyz"}]}}
    _, problema = await _resolver_geo(est, working, {"aoi": '{"type":"Point","coordinates":[-74.04,4.73]}'})
    assert "`punto` (marcado en lon -74.04000, lat 4.73000)" in problema
    assert "`viewport`" in problema and "`l1` (Lotes)" in problema
    assert "`r1`" not in problema          # un raster no es una geometría que pasar
    assert "No escribas geometría a mano" in problema


@pytest.mark.asyncio
async def test_viewport_es_la_zona_visible():
    est = SimpleNamespace(geo={"inputs": {"aoi": {"accepts": ["geometry"]}}})
    working = {"map_context": {"viewport": {"bbox": [-74.2, 4.5, -74.0, 4.7]}}}
    args, problema = await _resolver_geo(est, working, {"aoi": "viewport"})
    assert problema is None
    anillo = args["aoi"]["features"][0]["geometry"]["coordinates"][0]
    assert anillo[0] == [-74.2, 4.5] and anillo[2] == [-74.0, 4.7]


@pytest.mark.asyncio
async def test_teselas_y_estadisticas_de_un_g2_van_al_mapa_y_a_la_tabla(monkeypatch):
    from geo_copilot.platform.mcp.hub import _materializar

    cfg = SimpleNamespace(id="imagery", tiles=SimpleNamespace(prefixes=["/tiles/"]))
    gr = {"geo_result": "1", "facts": {"scene": {"id": "S2"}}, "artifacts": [
        {"kind": "raster_tiles", "name": "NDVI 2026-01-10", "tiles": "/tiles/S2/{z}/{x}/{y}.png?rescale=0,1",
         "bounds": [-74.2, 4.5, -74.0, 4.7]},
        {"kind": "stats", "items": [{"label": "mean", "value": 0.42}]},
        {"kind": "raster_tiles", "name": "colado", "tiles": "/admin/{z}/{x}/{y}.png"},
    ]}
    out = await _materializar(cfg, "imagery_ndvi", gr, {"session_id": "s"},
                              {"date_from": "2026-01-01", "aoi": {"type": "FeatureCollection"}})
    im = out.delta["external_imagery"]
    # S4.4: la capa sabe de dónde salió (tool + argumentos escalares; la geometría no)
    assert im["provenance"]["capability"] == "mcp.imagery.imagery_ndvi"
    assert im["provenance"]["arguments"] == {"date_from": "2026-01-01"}
    assert im["service_url"] == "/api/v1/proxy/mcp/imagery/tiles/S2/{z}/{x}/{y}.png?rescale=0,1"
    assert im["extent"] == {"xmin": -74.2, "ymin": 4.5, "xmax": -74.0, "ymax": 4.7}
    assert out.delta["data"] == {"results": [{"métrica": "mean", "valor": 0.42}]}
    assert "fuera de los prefijos declarados" in out.observation  # la ruta no declarada no pasa
    assert '"scene"' in out.observation


def test_los_argumentos_geo_se_piden_como_referencia_no_como_geometria():
    """V5 F3: con el esquema del servidor (`aoi: object`) el LLM escribió a mano dos
    cuadrados en otro barrio en vez de pasar la capa de 30 lotes que acababa de traer.
    El hub pide una REFERENCIA (y pone la geometría real); el servidor no cambia."""
    from geo_copilot.platform.mcp.hub import EstadoTool

    cfg = _config("http://x/mcp").servers[0]
    tool = SimpleNamespace(name="zonal", description="NDVI por feature.", annotations=None, meta=None,
                           inputSchema={"type": "object", "required": ["features", "fecha"], "properties": {
                               "features": {"type": "object", "title": "Features"},
                               "fecha": {"type": "string"}}})
    est = EstadoTool("hello", "zonal", "hello__zonal", True, None, "read", "h",
                     {"inputs": {"features": {"accepts": ["geometry", "layer_ref"]}}})
    cap = McpHub(McpConfig.model_validate({"servers": []}))._capacidad(cfg, tool, est)
    geo = cap.parameters["properties"]["features"]
    assert geo["type"] == "string" and "activa" in geo["description"] and "viewport" in geo["description"]
    assert cap.parameters["properties"]["fecha"] == {"type": "string"}
    assert cap.parameters["required"] == ["features", "fecha"]
    assert tool.inputSchema["properties"]["features"] == {"type": "object", "title": "Features"}  # intacto


@pytest.mark.asyncio
async def test_activa_grande_se_lee_del_workspace(monkeypatch):
    """Una capa del turno de >5000 elementos no viaja inline: `activa` va a su dataset."""
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}}]}
    store = SimpleNamespace(to_geojson=AsyncMock(return_value=fc))
    monkeypatch.setattr(wsctx, "_store", store)
    est = SimpleNamespace(geo={"inputs": {"aoi": {"accepts": ["geometry"]}}})
    working = {"session_id": "s", "result_layer_ref": {"id": "ds_0123456789abcdef"}}
    args, problema = await _resolver_geo(est, working, {"aoi": "activa"})
    assert problema is None and args["aoi"] is fc
    # se pide uno más del tope: así se SABE si la capa lo supera (antes se cortaba a 5000 en silencio)
    store.to_geojson.assert_awaited_with("s", "ds_0123456789abcdef", limit=5001)


@pytest.mark.asyncio
async def test_una_capa_mayor_que_el_tope_no_se_envia_truncada(monkeypatch):
    """Auditoría pre-producción: un NDVI por lote de 20.000 lotes se calculaba sobre los primeros
    5000 y se narraba como el total. Ahora no se ejecuta y se dice por qué; para un bbox sí vale."""
    from geo_copilot.platform.mcp import hub as hubmod
    from geo_copilot.platform.mcp import referencias  # F4.2: la resolución geo salió de hub.py

    monkeypatch.setattr(referencias, "_max_geo", lambda: 3)
    punto = {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [0, 0]}}
    fc = {"type": "FeatureCollection", "features": [punto] * 4}
    ref = SimpleNamespace(bbox=(-74.2, 4.5, -74.0, 4.7))
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(to_geojson=AsyncMock(return_value=fc),
                                                         get=AsyncMock(return_value=ref)))
    working = {"session_id": "s", "result_layer_ref": {"id": "ds_0123456789abcdef"}}
    geo = SimpleNamespace(geo={"inputs": {"aoi": {"accepts": ["geometry"]}}})
    _, problema = await _resolver_geo(geo, working, {"aoi": "activa"})
    assert problema and "más de 3 elementos" in problema and "No se ejecutó" in problema
    # para un bbox basta la extensión, y es la de TODO el dataset (no la de los primeros N)
    solo_bbox = SimpleNamespace(geo={"inputs": {"aoi": {"accepts": ["bbox"]}}})
    args, problema = await _resolver_geo(solo_bbox, working, {"aoi": "activa"})
    assert problema is None and args["aoi"] == [-74.2, 4.5, -74.0, 4.7]


# ---------------------------------------------------------------------------
# T3.10 (S3.8): HITL por riesgo
# ---------------------------------------------------------------------------


def _grafo_con_hitl(status):
    from geo_copilot.security.hitl import HITLResponse

    mgr = SimpleNamespace(request_approval=AsyncMock(return_value=HITLResponse(request_id="r", status=status)))
    return SimpleNamespace(hitl_manager=mgr)


@pytest.fixture
def hub_hello(servidor, clave, limpiar_registro):
    async def crear(**politica):
        cfg = _config(servidor)
        if politica:
            s = cfg.servers[0]
            cfg = McpConfig.model_validate({"servers": [s.model_copy(update={
                "policy": s.policy.model_copy(update={"hitl": s.policy.hitl.model_copy(update=politica)})
            }).model_dump()]})
        hub = McpHub(cfg)
        await hub.refrescar()
        return hub
    return crear


@pytest.mark.asyncio
async def test_lectura_con_politica_auto_no_pide_aprobacion(hub_hello, monkeypatch):
    from geo_copilot.security.hitl import HITLStatus

    await hub_hello()
    grafo = _grafo_con_hitl(HITLStatus.REJECTED)
    out = await registry().get("hello__hello_about").executor(grafo, {"session_id": "s"}, {})
    assert out.success and grafo.hitl_manager.request_approval.await_count == 0


@pytest.mark.asyncio
async def test_si_la_politica_exige_aprobar_y_el_usuario_rechaza_no_se_llama_al_servidor(hub_hello):
    from geo_copilot.security.hitl import HITLStatus

    hub = await hub_hello(read="approve")
    llamadas = []
    original = hub.conexiones["hello"].call_tool

    async def espiar(*a, **k):
        llamadas.append(a)
        return await original(*a, **k)

    hub.conexiones["hello"].call_tool = espiar
    grafo = _grafo_con_hitl(HITLStatus.REJECTED)
    out = await registry().get("hello__hello_circle").executor(
        grafo, {"session_id": "s"}, {"lon": -74.08, "lat": 4.6, "meters": 250})
    assert not out.success and "rechazó" in out.observation and llamadas == []
    kw = grafo.hitl_manager.request_approval.call_args.kwargs
    assert kw["session_id"] == "s" and '"meters": 250' in kw["details"]["argumentos"]


@pytest.mark.asyncio
async def test_aprobada_se_ejecuta(hub_hello, monkeypatch):
    from geo_copilot.security.hitl import HITLStatus

    ref = MagicMock()
    ref.model_dump.return_value = {"id": "ds_aaaaaaaaaaaaaaaa", "name": "c", "feature_count": 1}
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(ingest_features=AsyncMock(return_value=ref),
                                                         to_geojson=AsyncMock(return_value=None)))
    await hub_hello(read="approve")
    out = await registry().get("hello__hello_circle").executor(
        _grafo_con_hitl(HITLStatus.APPROVED), {"session_id": "s"}, {"lon": 0, "lat": 0, "meters": 10})
    assert out.success, out.observation


@pytest.mark.asyncio
async def test_sin_hitl_una_tool_que_exige_aprobacion_no_se_ejecuta(hub_hello, monkeypatch):
    await hub_hello(read="approve")
    monkeypatch.setattr("geo_copilot.core.config.get_settings",
                        lambda: SimpleNamespace(hitl_enabled=False, hitl_mode="blocking"))
    out = await registry().get("hello__hello_circle").executor(
        SimpleNamespace(hitl_manager=None), {"session_id": "s"}, {"lon": 0, "lat": 0, "meters": 10})
    assert not out.success and "exige aprobación" in out.observation


@pytest.mark.asyncio
async def test_desde_el_panel_el_clic_es_la_aprobacion(hub_hello, monkeypatch):
    ref = MagicMock()
    ref.model_dump.return_value = {"id": "ds_aaaaaaaaaaaaaaaa", "name": "c", "feature_count": 1}
    monkeypatch.setattr(wsctx, "_store", SimpleNamespace(ingest_features=AsyncMock(return_value=ref),
                                                         to_geojson=AsyncMock(return_value=None)))
    await hub_hello(read="approve")
    out = await registry().get("hello__hello_circle").executor(None, {"session_id": "s"},
                                                               {"lon": 0, "lat": 0, "meters": 10})
    assert out.success, out.observation


def test_un_argumento_numerico_no_se_convierte_en_referencia():
    """Una tool que marque lon/lat como geo no debe perder su tipo numérico."""
    from geo_copilot.platform.mcp.hub import _esquema_con_referencias

    esquema = {"type": "object", "properties": {"lon": {"type": "number"}, "aoi": {"type": "object"},
                                                "zona": {"anyOf": [{"type": "array"}, {"type": "null"}]}}}
    geo = {"lon": {"accepts": ["geometry"]}, "aoi": {"accepts": ["geometry"]}, "zona": {"accepts": ["bbox"]}}
    out = _esquema_con_referencias(esquema, geo)["properties"]
    assert out["lon"] == {"type": "number"}
    assert out["aoi"]["type"] == "string" and out["zona"]["type"] == "string"


# ---------------------------------------------------------------------------
# T5.2 — servidores que usa el propio núcleo (llamada directa) y que el agente no ve
# ---------------------------------------------------------------------------


def _config_solo_nucleo(url: str) -> McpConfig:
    cfg = _config(url).model_dump()
    cfg["servers"][0]["tools"]["agent"] = []
    return McpConfig.model_validate(cfg)


@pytest.mark.asyncio
async def test_un_servidor_solo_del_nucleo_no_es_herramienta_del_agente(servidor, clave, limpiar_registro):
    hub = McpHub(_config_solo_nucleo(servidor))
    await hub.refrescar()
    assert registry().get("hello__hello_circle") is None and registry().get("hello__hello_about") is None
    assert hub.herramientas() == [] and "hello" not in hub.resumen_prompt()
    # pero el núcleo sí lo llama, con el resultado estructurado del servidor
    sc = await hub.llamar_directo("hello", "hello_circle", {"lon": -74.08, "lat": 4.6, "meters": 250})
    assert sc["geo_result"] == "1" and sc["artifacts"][0]["kind"] == "feature_collection"
    assert len(hub.estado()[0]["tools"]) == 2  # el panel de conexiones las sigue mostrando


@pytest.mark.asyncio
async def test_la_llamada_directa_respeta_allowlist_y_pinning(servidor, clave, limpiar_registro):
    from geo_copilot.platform.mcp.connection import McpError

    pins = MemoryPinStore()
    await pins.set("hello:hello_circle", "huella-de-otra-descripcion")
    hub = McpHub(_config(servidor), pins=pins)
    with pytest.raises(McpError, match="deshabilitada"):
        await hub.llamar_directo("hello", "hello_circle", {"lon": 0, "lat": 0, "meters": 10})
    with pytest.raises(McpError, match="no ofrece"):
        await hub.llamar_directo("hello", "otra_tool", {})
    with pytest.raises(McpError, match="no está configurado"):
        await hub.llamar_directo("arcgis", "arcgis_search_items", {})


@pytest.mark.asyncio
async def test_un_argumento_que_la_tool_no_declara_se_rechaza_en_vez_de_ignorarse(servidor, clave, limpiar_registro):
    """V5 T5.5: el SDK de MCP ignora en silencio lo que la tool no declara; se pidió el NDVI por lote
    «con Landsat» a una tool sin `collection`, salió Sentinel-2 y la respuesta dijo Landsat."""
    hub = McpHub(_config(servidor))
    await hub.refrescar()
    out = await registry().get("hello__hello_circle").executor(
        None, {"session_id": "s"}, {"lon": -74.08, "lat": 4.6, "meters": 250, "collection": "landsat-c2-l2"})
    assert not out.success and "no acepta ['collection']" in out.observation
    assert "meters" in out.observation  # dice cuáles sí acepta
    ok = await registry().get("hello__hello_circle").executor(None, {"session_id": "s"},
                                                              {"lon": -74.08, "lat": 4.6, "meters": 250})
    assert ok.success


def test_el_llm_ve_los_valores_de_la_capa_que_devolvio_el_servicio():
    """V5 (imagery): «¿cuál de los dos lotes tiene más vegetación?» — el NDVI por lote quedó en la capa
    pero la observación solo decía {id, nombre, elementos}: respondió «uno de ellos» e inventó colores."""
    from geo_copilot.platform.mcp.hub import MAX_ELEMENTOS_EN_OBSERVACION, _contenido_de_capa

    lotes = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {"nombre": "Lote Norte", "ndvi_mean": 0.68, "g": {"x": 1}}},
        {"type": "Feature", "geometry": None, "properties": {"nombre": "Lote Sur", "ndvi_mean": 0.38}}]}
    assert _contenido_de_capa(lotes) == {"elementos": [{"nombre": "Lote Norte", "ndvi_mean": 0.68},
                                                       {"nombre": "Lote Sur", "ndvi_mean": 0.38}]}
    muchos = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {"v": i, "tipo": "ab"[i % 2]}}
        for i in range(MAX_ELEMENTOS_EN_OBSERVACION + 5)]}
    resumen = _contenido_de_capa(muchos)
    assert resumen["campos"]["v"] == {"min": 0, "max": MAX_ELEMENTOS_EN_OBSERVACION + 4,
                                      "media": (MAX_ELEMENTOS_EN_OBSERVACION + 4) / 2}
    assert resumen["campos"]["tipo"] == {"distintos": 2, "ejemplos": ["a", "b"]}
    assert _contenido_de_capa(None) is None and _contenido_de_capa({"features": []}) is None


def test_una_capa_que_el_servidor_marca_como_contorno_nace_sin_relleno():
    """V5 (imagery): el límite de Chía se pintaba relleno al 60 % ENCIMA del NDVI y lo tapaba."""
    from geo_copilot.platform.mcp.hub import _estilo_inicial

    e = _estilo_inicial({"solo_contorno": True}, {"name": "Chía", "geometry_type": "MultiPolygon"})
    assert e["fill"]["opacity"] == 0 and e["stroke"]["width"] > 0 and e["symbology_type"] == "single_symbol"
    assert e["geometry_type"] == "MultiPolygon" and e["layer_title"] == "Chía"
    # otra sugerencia (un gusto) sigue siendo solo texto para el LLM
    assert _estilo_inicial({"color": "#ff0000"}, {"name": "x"}) is None and _estilo_inicial(None, {}) is None
    assert _estilo_inicial({"solo_contorno": True, "color": "red;}"}, {"name": "x"})["stroke"]["color"] == "#1f2937"


def test_las_tools_que_producen_capas_aceptan_un_titulo_del_nucleo():
    """V5 (sql): «las sedes rurales de Zipaquirá» quedaban como «equipamientos · consulta» en el mapa."""
    from geo_copilot.platform.mcp.hub import TITULO_CAPA, EstadoTool

    cfg = _config("http://x/mcp").servers[0]
    esquema = {"type": "object", "properties": {"sql": {"type": "string"}}, "additionalProperties": False}
    tool = SimpleNamespace(name="q", description="consulta", annotations=None, meta=None, inputSchema=esquema)
    capa = EstadoTool("sql", "q", "sql__q", True, None, "read", "h", {"outputs": ["feature_collection", "table"]})
    texto = EstadoTool("sql", "q", "sql__q", True, None, "read", "h", {"outputs": []})
    hub = McpHub(McpConfig.model_validate({"servers": []}))
    assert TITULO_CAPA in hub._capacidad(cfg, tool, capa).parameters["properties"]
    assert TITULO_CAPA not in hub._capacidad(cfg, tool, texto).parameters["properties"]
    assert TITULO_CAPA not in esquema["properties"]  # el esquema del servidor (y su huella) no cambia


@pytest.mark.asyncio
async def test_el_titulo_nombra_la_capa_y_no_llega_al_servidor(monkeypatch):
    from geo_copilot.platform.mcp import hub as h

    nombres = []

    class Store:
        async def ingest_features(self, sesion, nombre, fc, **_):
            nombres.append(nombre)
            return SimpleNamespace(model_dump=lambda mode=None: {"id": "ds_aaaaaaaaaaaaaaaa", "name": nombre,
                                                                 "feature_count": 1})

        async def to_geojson(self, *a, **k):
            return {"type": "FeatureCollection", "features": []}

    monkeypatch.setattr("geo_copilot.platform.workspace.context.store_actual", lambda: Store())
    enviados = []

    class Con:
        async def call_tool(self, tool, args):
            enviados.append(dict(args))
            return SimpleNamespace(content=[], isError=False, structuredContent={
                "geo_result": "1", "facts": {}, "artifacts": [
                    {"kind": "feature_collection", "name": "equipamientos · consulta", "crs": "EPSG:4326",
                     "data": {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None,
                                                                          "properties": {}}]}}]})

    cfg = _config("http://x/mcp").servers[0]
    hub = McpHub(McpConfig.model_validate({"servers": []}))
    hub.conexiones = {cfg.id: Con()}
    est = h.EstadoTool(cfg.id, "q", f"{cfg.id}__q", True, None, "read", "h", {"outputs": ["feature_collection"]},
                       esquema={"type": "object", "properties": {"sql": {"type": "string"}}})
    out = await h.ejecutar_tool(hub, cfg, "q", est, {"session_id": "s"},
                                {"sql": "SELECT 1", "titulo_capa": "  Sedes rurales de   Zipaquirá "})
    assert out.success and nombres == ["Sedes rurales de Zipaquirá"]
    assert enviados == [{"sql": "SELECT 1"}]


@pytest.mark.parametrize("archivo", ["config/mcp_servers.yaml", "config/mcp_servers.local.yaml"])
def test_los_archivos_de_configuracion_de_mcp_son_validos(archivo):
    """Una descripción con «: » sin comillas rompió el YAML y la app no arrancó (V5 de sql)."""
    from pathlib import Path

    import yaml

    ruta = Path(__file__).resolve().parents[1] / archivo
    if not ruta.exists():
        pytest.skip(f"{archivo} no existe aquí (el .local es de cada máquina)")
    datos = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    cfg = McpConfig.model_validate(datos)
    assert len({s.id for s in cfg.servers}) == len(cfg.servers) and cfg.servers


def test_una_capa_incompleta_dice_que_es_una_muestra():
    """V5 (archivos): 10 de 22.387 lotes analizados y su promedio dado como el de todos."""
    from geo_copilot.platform.mcp.hub import _marca_de_muestra

    assert _marca_de_muestra({"completo": False, "total_que_cumplen": 22387, "traidos": 10}) == "(muestra: 10 de 22.387)"
    assert _marca_de_muestra({"completo": False, "filas": 5000}) == "(muestra)"
    assert _marca_de_muestra({"completo": True, "traidos": 10}) is None and _marca_de_muestra({}) is None
