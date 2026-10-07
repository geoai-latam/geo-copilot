"""Tests del servicio imagery-mcp (C3-v1) — offline salvo los marcados.

Cubre:
- Motor: bbox/área/límites, selección de escena por contención, NDVI con
  nodata, estadísticas, zonal por rasterize+bincount, PNG con submuestreo.
- Auth: verify sha256, scopes fail-closed, rate limit, extract_bearer.
- Middleware ASGI: 401/403/429/health y paso del body re-inyectado.
- Integración REAL (marker integration): búsqueda + NDVI sobre Bogotá.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "imagery_mcp"))

from datetime import UTC

from imagery_mcp import engine
from imagery_mcp.auth import (
    SCOPE_COMPUTE,
    SCOPE_READ,
    KeyRing,
    RateLimiter,
    extract_bearer,
)
from imagery_mcp.config import Limits
from imagery_mcp.providers import (
    _SRC_ASSET,
    _SRC_BASELINE,
    _SRC_COLECCION,
    _SRC_IDENTIDAD,
    Scene,
    build_provider,
)


def _scene(bbox, cloud=10.0, sid="S1") -> Scene:
    return Scene(id=sid, datetime="2026-06-11T15:00:00Z", cloud_pct=cloud,
                 bbox=bbox, red_href="mem://red", nir_href="mem://nir",
                 provider="test")


# ---------------------------------------------------------------------------
# Motor: geometría y selección
# ---------------------------------------------------------------------------
class TestGeoYSeleccion:
    def test_aoi_bbox_featurecollection(self):
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.1, 4.6]}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
                [-74.2, 4.5], [-74.0, 4.5], [-74.0, 4.7], [-74.2, 4.7], [-74.2, 4.5]]]}},
        ]}
        assert engine.aoi_bbox(fc) == (-74.2, 4.5, -74.0, 4.7)

    def test_aoi_vacio_error_honesto(self):
        with pytest.raises(engine.ImageryError, match="coordenadas"):
            engine.aoi_bbox({"type": "FeatureCollection", "features": []})

    def test_limite_de_area(self):
        # ~5°x5° ≈ >300k km² — muy por encima del límite v1.
        with pytest.raises(engine.ImageryError, match="límite"):
            engine.check_aoi((-77, 2, -72, 7), Limits())

    def test_selecciona_contenedora_con_menos_nubes(self):
        aoi = (-74.12, 4.60, -74.08, 4.64)
        contenedora = _scene([-74.5, 4.3, -73.9, 4.9], cloud=15, sid="CONT")
        parcial = _scene([-74.5, 4.3, -74.10, 4.62], cloud=2, sid="PARCIAL")
        scene, contained = engine.select_scene([parcial, contenedora], aoi)
        assert scene.id == "CONT" and contained is True

    def test_sin_contenedora_devuelve_mejor_cobertura(self):
        aoi = (-74.12, 4.60, -74.08, 4.64)
        p1 = _scene([-74.5, 4.3, -74.10, 4.62], sid="P1")
        p2 = _scene([-74.5, 4.3, -74.09, 4.63], sid="P2")  # cubre más
        scene, contained = engine.select_scene([p1, p2], aoi)
        assert scene.id == "P2" and contained is False

    def test_sin_escenas_error_honesto(self):
        with pytest.raises(engine.ImageryError, match="No hay escenas"):
            engine.select_scene([], (-74.1, 4.6, -74.0, 4.7))


# ---------------------------------------------------------------------------
# Motor: NDVI, stats, PNG
# ---------------------------------------------------------------------------
class TestNdviStats:
    def test_ndvi_valores_y_nodata(self):
        red = np.array([[100.0, 0.0], [300.0, 100.0]], dtype="float32")
        nir = np.array([[300.0, 0.0], [100.0, 100.0]], dtype="float32")
        ndvi = engine.compute_ndvi(red, nir)
        assert ndvi[0, 0] == pytest.approx(0.5)     # vegetación
        assert np.isnan(ndvi[0, 1])                 # 0/0 = nodata del recorte
        assert ndvi[1, 0] == pytest.approx(-0.5)
        assert ndvi[1, 1] == pytest.approx(0.0)

    def test_una_banda_cero_es_nodata(self):
        # Solo UNA banda 0 → NDVI espurio (±1) en agua/sombra; debe ser nodata (#13).
        red = np.array([[0.0, 300.0]], dtype="float32")
        nir = np.array([[300.0, 0.0]], dtype="float32")
        ndvi = engine.compute_ndvi(red, nir)
        assert np.isnan(ndvi[0, 0]) and np.isnan(ndvi[0, 1])

    def test_shapes_desalineadas_se_recortan(self):
        ndvi = engine.compute_ndvi(np.ones((5, 4), "float32"), np.ones((4, 5), "float32"))
        assert ndvi.shape == (4, 4)

    def test_stats_ignoran_nan(self):
        arr = np.array([[0.2, np.nan], [0.6, 0.4]], dtype="float32")
        s = engine.array_stats(arr)
        assert s["mean"] == pytest.approx(0.4, abs=1e-3)
        assert s["px_validos"] == 3 and s["px_total"] == 4

    def test_stats_todo_nan_error(self):
        with pytest.raises(engine.ImageryError, match="nodata"):
            engine.array_stats(np.full((3, 3), np.nan, dtype="float32"))


# ---------------------------------------------------------------------------
# Motor: zonal
# ---------------------------------------------------------------------------
class TestZonal:
    def _grid(self):
        # Índice 4x4 con transform identidad en un CRS "plano" (EPSG:3857 sirve
        # de proxy; las geometrías del test van en 4326 y se reproyectan).
        from rasterio.transform import from_origin
        arr = np.array([
            [0.1, 0.1, 0.8, 0.8],
            [0.1, 0.1, 0.8, 0.8],
            [0.3, 0.3, 0.5, 0.5],
            [0.3, 0.3, 0.5, 0.5],
        ], dtype="float32")
        # celda de 1 grado para mapear geometrías 4326 directamente
        return arr, from_origin(0.0, 4.0, 1.0, 1.0), "EPSG:4326"

    def test_media_por_feature(self):
        arr, transform, crs = self._grid()
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
                [0, 2], [2, 2], [2, 4], [0, 4], [0, 2]]]}},   # cuadrante 0.1
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
                [2, 2], [4, 2], [4, 4], [2, 4], [2, 2]]]}},   # cuadrante 0.8
        ]}
        rows, skipped = engine.zonal_stats(fc, arr, transform, crs)
        assert not skipped
        medias = {r["feature_index"]: r["mean"] for r in rows}
        assert medias[0] == pytest.approx(0.1, abs=0.05)
        assert medias[1] == pytest.approx(0.8, abs=0.05)

    def test_feature_fuera_del_raster_va_a_skipped(self):
        arr, transform, crs = self._grid()
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[
                [100, 100], [101, 100], [101, 101], [100, 101], [100, 100]]]}},
        ]}
        rows, skipped = engine.zonal_stats(fc, arr, transform, crs)
        assert rows == []
        assert skipped and skipped[0]["feature_index"] == 0

    def test_geometrias_menores_que_el_pixel_no_quedan_sin_valor(self):
        """V5 F3: 30 lotes de ~98 m² sobre píxeles de 10 m. Rasterizar TODAS en un
        raster de etiquetas deja cada píxel a UNA sola geometría: las vecinas que lo
        comparten quedaban «sin píxeles» y el NDVI por lote salía vacío."""
        arr, transform, crs = self._grid()
        # cuatro «lotes» de 0.5×0.5 dentro de la MISMA celda (0.1)
        lotes = [[[x, y], [x + 0.5, y], [x + 0.5, y + 0.5], [x, y + 0.5], [x, y]]
                 for x, y in ((0.0, 3.5), (0.5, 3.5), (0.0, 3.0), (0.5, 3.0))]
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [c]}} for c in lotes]}
        rows, skipped = engine.zonal_stats(fc, arr, transform, crs)
        assert not skipped, skipped
        assert [r["mean"] for r in rows] == [pytest.approx(0.1, abs=1e-4)] * 4
        compartidos = [r for r in rows if r.get("px_compartidos")]
        assert len(compartidos) >= 3  # a lo sumo uno se quedó el píxel en exclusiva

    def test_limite_de_features(self):
        arr, transform, crs = self._grid()
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 3]}},
        ] * 10}
        with pytest.raises(engine.ImageryError, match="límite"):
            engine.zonal_stats(fc, arr, transform, crs, max_features=5)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
def _ring() -> KeyRing:
    return KeyRing([
        {"name": "app", "key": "secreto-app",
         "scopes": [SCOPE_READ, SCOPE_COMPUTE], "rate_limit_per_min": 3},
        {"name": "solo-lectura", "key": "secreto-ro", "scopes": [SCOPE_READ]},
    ])


class TestAuth:
    def test_verify_y_scopes(self):
        ring = _ring()
        app = ring.verify("secreto-app")
        ro = ring.verify("secreto-ro")
        assert app and app.allows_tool("imagery_ndvi")
        assert ro and ro.allows_tool("imagery_search_scenes")
        assert not ro.allows_tool("imagery_ndvi")          # sin compute
        assert ring.verify("incorrecta") is None
        assert ring.verify(None) is None

    def test_tool_desconocida_denegada_fail_closed(self):
        app = _ring().verify("secreto-app")
        assert not app.allows_tool("tool_inexistente")

    def test_tool_scopes_cubre_todas_las_tools_registradas(self):
        # Fail-closed: una @mcp.tool ausente de TOOL_SCOPES da un 403 sorpresa
        # (le pasó a imagery_composite). El mapa debe cubrir TODAS las tools.
        import asyncio

        from imagery_mcp import server
        from imagery_mcp.auth import TOOL_SCOPES
        names = {t.name for t in asyncio.run(server.mcp.list_tools())}
        assert names <= set(TOOL_SCOPES), f"tools sin scope: {names - set(TOOL_SCOPES)}"
        assert "imagery_composite" in TOOL_SCOPES

    def test_scopes_desconocidos_rechazados_al_cargar(self):
        with pytest.raises(ValueError, match="desconocidos"):
            KeyRing([{"name": "x", "key": "k", "scopes": ["imagery:destruir"]}])

    def test_rate_limit(self):
        ring = _ring()
        key = ring.verify("secreto-app")
        limiter = RateLimiter()
        assert [limiter.allow(key) for _ in range(4)] == [True, True, True, False]

    def test_rate_limit_peso_fraccionario_teselas(self):
        # #31: las teselas pesan 0.05 → con límite 3/min caben ~60 (no 3).
        ring = _ring()
        key = ring.verify("secreto-app")   # rate_limit_per_min=3
        limiter = RateLimiter()
        # 50 teselas (50*0.05 = 2.5 < 3) pasan todas — a peso 1.0 la 4ª caería.
        assert all(limiter.allow(key, weight=0.05) for _ in range(50))

    def test_extract_bearer(self):
        assert extract_bearer("Bearer abc") == "abc"
        assert extract_bearer("bearer abc") == "abc"
        assert extract_bearer("Basic abc") is None
        assert extract_bearer(None) is None


# ---------------------------------------------------------------------------
# Middleware ASGI (contra una app eco, sin FastMCP)
# ---------------------------------------------------------------------------
class TestMiddleware:
    def _client(self):
        import httpx
        from imagery_mcp.server import AuthMiddleware

        async def echo_app(scope, receive, send):
            body = b""
            more = True
            while more:
                m = await receive()
                body += m.get("body", b"")
                more = m.get("more_body", False)
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body})

        app = AuthMiddleware(echo_app, _ring(), RateLimiter())
        # ASGITransport es async-only → AsyncClient.
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t",
        )

    @pytest.mark.asyncio
    async def test_health_abierto(self):
        async with self._client() as c:
            r = await c.get("/health")
            assert r.status_code == 200 and r.json()["status"] == "healthy"

    @pytest.mark.asyncio
    async def test_sin_clave_401(self):
        async with self._client() as c:
            assert (await c.post("/mcp", json={})).status_code == 401

    @pytest.mark.asyncio
    async def test_scope_insuficiente_403(self):
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "imagery_ndvi", "arguments": {}}}
        async with self._client() as c:
            r = await c.post("/mcp", json=call,
                             headers={"Authorization": "Bearer secreto-ro"})
            assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_scope_correcto_pasa_y_reinyecta_body(self):
        call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "imagery_search_scenes", "arguments": {}}}
        async with self._client() as c:
            r = await c.post("/mcp", json=call,
                             headers={"Authorization": "Bearer secreto-ro"})
            assert r.status_code == 200
            # la app eco recibió el MISMO body (re-inyección funciona)
            assert r.json()["params"]["name"] == "imagery_search_scenes"

    @pytest.mark.asyncio
    async def test_rate_limit_429(self):
        async with self._client() as c:
            headers = {"Authorization": "Bearer secreto-app"}
            codes = []
            for _ in range(4):
                codes.append((await c.post("/mcp", json={}, headers=headers)).status_code)
            assert codes == [200, 200, 200, 429]

    @pytest.mark.asyncio
    async def test_health_sin_metricas(self):
        # #19: /health público NO expone métricas (patrones de uso).
        async with self._client() as c:
            r = await c.get("/health")
            assert r.status_code == 200 and "metrics" not in r.json()

    @pytest.mark.asyncio
    async def test_metrics_requiere_bearer(self):
        # #19: las métricas viven tras el Bearer, en /metrics.
        async with self._client() as c:
            assert (await c.get("/metrics")).status_code == 401
            r = await c.get("/metrics", headers={"Authorization": "Bearer secreto-ro"})
            assert r.status_code == 200 and "metrics" in r.json()

    @pytest.mark.asyncio
    async def test_body_gigante_413(self):
        # #18: un cuerpo > 2 MB se aborta con 413 antes de tocar el MCP.
        big = b'{"x":"' + b"a" * (2 * 1024 * 1024 + 100) + b'"}'
        async with self._client() as c:
            r = await c.post("/mcp", content=big,
                             headers={"Authorization": "Bearer secreto-app",
                                      "Content-Type": "application/json"})
            assert r.status_code == 413


# ---------------------------------------------------------------------------
# Server: no arranca sin claves
# ---------------------------------------------------------------------------
def test_server_sin_claves_no_arranca(monkeypatch):
    import imagery_mcp.server as srv
    monkeypatch.setattr(srv, "keyring", KeyRing([]))
    with pytest.raises(RuntimeError, match="SIN claves"):
        srv.build_app()


def test_imagery_port_invalido_error_claro(monkeypatch):
    # #21: un IMAGERY_PORT no numérico/fuera de rango da un error legible.
    from imagery_mcp.config import Settings
    monkeypatch.setenv("IMAGERY_PORT", "no-es-num")
    with pytest.raises(ValueError, match="IMAGERY_PORT inválido"):
        Settings.from_env()
    monkeypatch.setenv("IMAGERY_PORT", "99999")
    with pytest.raises(ValueError, match="fuera de rango"):
        Settings.from_env()


# ---------------------------------------------------------------------------
# Integración REAL (red externa) — pytest -m integration
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_ndvi_real_sobre_bogota():
    provider = build_provider("planetary-computer")
    aoi = engine.bbox_polygon((-74.12, 4.60, -74.08, 4.64))
    # Ventana FIJA con escenas despejadas conocidas (11-jun y 10-ago de 2026): con la ventana por
    # defecto (los últimos días) el test dependía del clima del mes — el 28-sep no había ninguna
    # escena con < 20 % de nubes sobre Bogotá y el motor lo decía, correctamente, como error.
    result = engine.run_ndvi(provider, aoi, "2026-06-01", "2026-08-31", None, Limits())
    assert "error" not in result
    assert -1.0 <= result["stats"]["mean"] <= 1.0
    assert result["scene"]["cloud_pct"] < 30
    assert result["tiles"]["url_template"].startswith("/tiles/")
    # auditoría 2026-09-08, §1.3: el `-1 <= mean <= 1` de arriba es cierto POR
    # CONSTRUCCIÓN (NDVI está acotado ahí) y no detecta el sesgo del baseline.
    # Esto sí: el payload declara qué factor se aplicó, y si la escena es
    # post-04.00 sin factor publicado el servicio tiene que DECIRLO.
    refl = result["reflectance"]
    assert refl["aplicado"] in (True, False)
    b = engine.baseline_value(refl["processing_baseline"])
    if b is not None and b >= engine._BOA_OFFSET_BASELINE and not refl["aplicado"]:
        assert "aviso" in refl, "escena post-04.00 sin factor y sin declararlo"


# ---------------------------------------------------------------------------
# Teselas (spec §8)
# ---------------------------------------------------------------------------
class TestTiles:
    def test_parse_tile_path(self):
        from imagery_mcp.tiles import parse_tile_path
        assert parse_tile_path("/tiles/S2X/13/2409/3990.png") == ("S2X", 13, 2409, 3990)
        assert parse_tile_path("/tiles/S2X/13/2409/3990.jpg") is None
        assert parse_tile_path("/tiles/S2X/13/2409.png") is None
        assert parse_tile_path("/mcp") is None

    def test_pool_escena_no_registrada(self):
        from imagery_mcp.tiles import TilePool
        pool = TilePool(provider=None)
        with pytest.raises(KeyError, match="no registrada"):
            pool.render_tile("inexistente", 13, 1, 1)

    def test_run_ndvi_incluye_tiles_y_registra(self, monkeypatch):
        from imagery_mcp import engine as eng
        scene = _scene([-75.0, 4.0, -73.0, 5.0], sid="S2TILES")

        class _Prov:
            def search(self, *a, **k): return [scene]
            def sign(self, h): return h

        fake_win = eng.BandWindow(
            data=np.array([[0.5, 0.2]], dtype="float32"),
            transform=None, crs=None, bounds4326=(-74.12, 4.60, -74.08, 4.64))
        monkeypatch.setattr(eng, "_ndvi_window", lambda *a, **k: fake_win)
        registered = []
        out = eng.run_ndvi(_Prov(), {"type": "Polygon", "coordinates": [[
            [-74.12, 4.60], [-74.08, 4.60], [-74.08, 4.64], [-74.12, 4.64],
            [-74.12, 4.60]]]}, None, None, None, Limits(),
            on_scene=registered.append)
        # A partir del stretch dinámico el template lleva ?rescale=p2,p98
        assert out["tiles"]["url_template"].startswith("/tiles/S2TILES/{z}/{x}/{y}.png?rescale=")
        assert out["tiles"]["rescale"][0] < out["tiles"]["rescale"][1]
        assert registered and registered[0].id == "S2TILES"

    @pytest.mark.asyncio
    async def test_endpoint_tiles_scope_y_404(self, tmp_path, monkeypatch):
        import httpx
        import imagery_mcp.tiles as tiles_mod
        from imagery_mcp.server import AuthMiddleware
        from imagery_mcp.tiles import TilePool
        # Aislar el caché en DISCO: un PNG de otra corrida para la escena "S"
        # convertiría el 404 esperado (escena no registrada) en un 200.
        monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

        async def never(scope, receive, send):  # el mcp app no debe tocarse
            raise AssertionError("no debió llegar al app MCP")

        app = AuthMiddleware(never, _ring(), RateLimiter(), tile_pool=TilePool(None))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t",
        ) as c:
            ro = {"Authorization": "Bearer secreto-ro"}
            ok = {"Authorization": "Bearer secreto-app"}
            sid = "S2A_MSIL2A_TEST"   # formato válido → llega a resolver → 404
            assert (await c.get(f"/tiles/{sid}/13/1/1.png", headers=ro)).status_code == 403
            assert (await c.get(f"/tiles/{sid}/13/1/1.png", headers=ok)).status_code == 404
            # Formato inválido → 400 antes de tocar el pool (id de un solo segmento).
            assert (await c.get("/tiles/NOSLASH/13/1/1.png", headers=ok)).status_code == 400


class TestLazyYAlternativas:
    def test_pool_resuelve_escena_por_id(self, tmp_path, monkeypatch):
        """Tras un restart el registro está vacío: el pool resuelve por STAC."""
        from unittest.mock import MagicMock, patch

        import imagery_mcp.tiles as tiles_mod
        from imagery_mcp.tiles import TilePool
        # Aislar el caché en DISCO: un PNG de otra corrida anularía el lazy.
        monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))
        scene = _scene([-75, 4, -73, 5], sid="S2LAZY")
        provider = MagicMock()
        provider.get_scene.return_value = scene
        provider.sign.side_effect = lambda h: h
        pool = TilePool(provider)

        fake_reader = MagicMock()
        fake_reader.tile.return_value.data = __import__("numpy").ones(
            (1, 8, 8), dtype="float32")
        fake_reader.tile.return_value.mask = __import__("numpy").full(
            (8, 8), 255, dtype="uint8")
        with patch("rio_tiler.io.Reader", return_value=fake_reader):
            png = pool.render_tile("S2LAZY", 13, 1, 1)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        provider.get_scene.assert_called_once_with("S2LAZY")

    def test_pool_id_irresoluble_404(self):
        from unittest.mock import MagicMock

        from imagery_mcp.tiles import TilePool
        provider = MagicMock()
        provider.get_scene.return_value = None
        with pytest.raises(KeyError, match="ni resoluble"):
            TilePool(provider).render_tile("NO-EXISTE", 13, 1, 1)

    def test_run_ndvi_expone_alternativas(self, monkeypatch):
        from imagery_mcp import engine as eng
        aoi_bbox = (-74.12, 4.60, -74.08, 4.64)
        elegida = _scene([-75, 4, -73, 5], cloud=5, sid="MEJOR")
        alt1 = _scene([-75, 4, -73, 5], cloud=12, sid="ALT1")
        no_contiene = _scene([-75, 4, -74.10, 4.62], cloud=1, sid="PARCIAL")

        class _Prov:
            def search(self, *a, **k): return [elegida, alt1, no_contiene]
            def sign(self, h): return h

        fake_win = eng.BandWindow(
            data=__import__("numpy").array([[0.5]], dtype="float32"),
            transform=None, crs=None, bounds4326=aoi_bbox)
        monkeypatch.setattr(eng, "_ndvi_window", lambda *a, **k: fake_win)
        out = eng.run_ndvi(_Prov(), eng.bbox_polygon(aoi_bbox), None, None,
                           None, Limits())
        ids = [a["id"] for a in out["alternatives"]]
        assert ids == ["ALT1"]  # solo las que CONTIENEN el AOI, sin la elegida

    def test_scene_id_fuera_del_search_resuelve_por_id(self, monkeypatch):
        """scene_id no presente en el search → get_scene por id (no otra fecha)."""
        from imagery_mcp import engine as eng
        aoi = (-74.12, 4.60, -74.08, 4.64)
        otra = _scene([-75, 4, -73, 5], sid="OTRA-FECHA")
        pedida = _scene([-75, 4, -73, 5], sid="LA-PEDIDA")

        class _Prov:
            def search(self, *a, **k): return [otra]          # NO trae la pedida
            def get_scene(self, sid): return pedida if sid == "LA-PEDIDA" else None
            def sign(self, h): return h

        fake_win = eng.BandWindow(
            data=np.array([[0.5]], dtype="float32"),
            transform=None, crs=None, bounds4326=aoi)
        monkeypatch.setattr(eng, "_ndvi_window", lambda *a, **k: fake_win)
        out = eng.run_ndvi(_Prov(), eng.bbox_polygon(aoi), None, None,
                           "LA-PEDIDA", Limits())
        assert out["scene"]["id"] == "LA-PEDIDA"   # jamás OTRA-FECHA

    def test_scene_id_irresoluble_es_error(self, monkeypatch):
        """scene_id inexistente ni en search ni en STAC → error, no fallback mudo."""
        from imagery_mcp import engine as eng
        aoi = (-74.12, 4.60, -74.08, 4.64)
        otra = _scene([-75, 4, -73, 5], sid="OTRA")

        class _Prov:
            def search(self, *a, **k): return [otra]
            def get_scene(self, sid): return None
            def sign(self, h): return h

        with pytest.raises(eng.ImageryError, match="no está disponible"):
            eng.run_ndvi(_Prov(), eng.bbox_polygon(aoi), None, None,
                         "NO-EXISTE", Limits())


def test_prewarm_llena_el_cache_acotado():
    import time as _t
    from unittest.mock import MagicMock, patch

    from imagery_mcp.tiles import TilePool, _tiles_in
    # _tiles_in cubre el bbox
    tiles = _tiles_in([-74.12, 4.60, -74.08, 4.64], 13)
    assert len(tiles) >= 1 and all(len(t) == 2 for t in tiles)

    pool = TilePool(provider=MagicMock())
    rendered = []
    with patch.object(pool, "render_tile",
                      side_effect=lambda *a, **k: rendered.append(a)):
        pool.prewarm("S2X", [-74.12, 4.60, -74.08, 4.64], zooms=(13,), max_tiles=4)
        for _ in range(50):
            if len(rendered) >= min(4, len(tiles)):
                break
            _t.sleep(0.05)
    assert 1 <= len(rendered) <= 4  # acotado por max_tiles


def test_tesela_fuera_del_footprint_es_transparente(tmp_path, monkeypatch):
    """TileOutsideBounds → PNG transparente cacheado, no 502 ni retries."""
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    from imagery_mcp.tiles import _TRANSPARENT_PNG, TilePool
    from rio_tiler.errors import TileOutsideBounds

    pool = TilePool(provider=MagicMock())
    pool.register_scene(_scene([-75, 4, -74, 5], sid="S2EDGE"))
    bad_reader = MagicMock()
    bad_reader.tile.side_effect = TileOutsideBounds("fuera")
    with patch("rio_tiler.io.Reader", return_value=bad_reader):
        png = pool.render_tile("S2EDGE", 14, 4824, 7981)
    assert png == _TRANSPARENT_PNG
    # Cacheada: la segunda NO toca readers.
    assert pool.render_tile("S2EDGE", 14, 4824, 7981) == _TRANSPARENT_PNG
    assert bad_reader.tile.call_count <= 2  # 1 llamada (o 2 por el par), sin retries


def test_tesela_enmascara_nubes_con_scl(tmp_path, monkeypatch):
    """M2/#14: la tesela pone alpha=0 en píxeles nube (SCL), no rojo brillante.

    Alinea la tesela con las estadísticas/overlay, que YA enmascaran por SCL.
    """
    import io as _io
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.providers import Scene
    from imagery_mcp.tiles import _TILESIZE, TilePool
    from PIL import Image

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    def _band(v):
        t = MagicMock()
        t.data = np.full((1, _TILESIZE, _TILESIZE), v, dtype="float32")
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, dtype="uint8")
        return t

    def _scl():
        arr = np.full((1, _TILESIZE, _TILESIZE), 4, dtype="uint8")  # 4=vegetación
        arr[:, :, : _TILESIZE // 2] = 9   # 9=nube prob. alta (mitad izquierda)
        t = MagicMock()
        t.data = arr
        return t

    def _make_reader(href):
        r = MagicMock()
        if "scl" in href:
            r.tile.return_value = _scl()
        elif "nir" in href:
            r.tile.return_value = _band(0.6)
        else:
            r.tile.return_value = _band(0.2)
        return r

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    pool.register_scene(Scene(
        id="S2CLOUD", datetime="2026-06-11T15:00:00Z", cloud_pct=10.0,
        bbox=[-75, 4, -73, 5], red_href="mem://red", nir_href="mem://nir",
        provider="test", scl_href="mem://scl"))

    with patch("rio_tiler.io.Reader", side_effect=_make_reader):
        png = pool.render_tile("S2CLOUD", 13, 1, 1)

    alpha = np.array(Image.open(_io.BytesIO(png)).convert("RGBA"))[:, :, 3]
    assert (alpha[:, : _TILESIZE // 2] == 0).all()    # nubes → transparentes
    assert (alpha[:, _TILESIZE // 2:] == 255).all()   # despejado → visible


def test_render_diff_tile_resta_dos_escenas(tmp_path, monkeypatch):
    """M8: la tesela de cambio resta NDVI(b)−NDVI(a) alineado en web-mercator."""
    import io as _io
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.providers import Scene
    from imagery_mcp.tiles import _TILESIZE, TilePool
    from PIL import Image

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    def _band(v):
        t = MagicMock()
        t.data = np.full((1, _TILESIZE, _TILESIZE), v, dtype="float32")
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, dtype="uint8")
        return t

    def _make_reader(href):
        r = MagicMock()
        if "a_nir" in href:
            r.tile.return_value = _band(0.6)     # ndvi_a = 0.4/0.8 = 0.5
        elif "a_red" in href:
            r.tile.return_value = _band(0.2)
        elif "b_nir" in href:
            r.tile.return_value = _band(0.9)     # ndvi_b = 0.8/1.0 = 0.8
        else:
            r.tile.return_value = _band(0.1)
        return r

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    for sid, pre in (("S2A_AAA", "a"), ("S2B_BBB", "b")):
        pool.register_scene(Scene(
            id=sid, datetime="2026-06-11T15:00:00Z", cloud_pct=10.0,
            bbox=[-75, 4, -73, 5], red_href=f"mem://{pre}_red",
            nir_href=f"mem://{pre}_nir", provider="test", scl_href=None))

    with patch("rio_tiler.io.Reader", side_effect=_make_reader):
        png = pool.render_diff_tile("S2A_AAA", "S2B_BBB", 13, 1, 1)

    img = np.array(Image.open(_io.BytesIO(png)).convert("RGBA"))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert (img[:, :, 3] == 255).all()   # Δ=+0.3 válido en todos lados → opaco


def test_render_rgb_true_color_usa_visual(tmp_path, monkeypatch):
    """Color real: usa el asset `visual` (TCI, ya 8-bit) directo, sin estirar."""
    import io as _io
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.providers import Scene
    from imagery_mcp.tiles import _TILESIZE, TilePool
    from PIL import Image

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    def _visual():
        t = MagicMock()
        d = np.zeros((3, _TILESIZE, _TILESIZE), "uint8")
        d[0], d[1], d[2] = 200, 120, 60   # tono cálido cualquiera
        t.data = d
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, "uint8")
        return t

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    pool.register_scene(Scene(
        id="S2A_VIS", datetime="x", cloud_pct=5.0, bbox=[-75, 4, -73, 5],
        red_href="mem://red", nir_href="mem://nir", provider="t",
        bands={"visual": "mem://visual"}))
    r = MagicMock()
    r.tile.return_value = _visual()
    with patch("rio_tiler.io.Reader", return_value=r):
        png = pool.render_rgb_tile("S2A_VIS", "true_color", 13, 1, 1)
    im = np.array(Image.open(_io.BytesIO(png)).convert("RGBA"))
    assert tuple(int(x) for x in im[0, 0, :3]) == (200, 120, 60)  # TCI directo
    assert (im[:, :, 3] == 255).all()


def test_render_rgb_composite_estira_bandas(tmp_path, monkeypatch):
    """Falso color: compone 3 bandas crudas y las estira a 8-bit (no queda plano)."""
    import io as _io
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.providers import Scene
    from imagery_mcp.tiles import _TILESIZE, TilePool
    from PIL import Image

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))
    grad = np.tile(np.linspace(500, 4000, _TILESIZE, dtype="float32"), (_TILESIZE, 1))

    def _band():
        t = MagicMock()
        t.data = grad[None, :, :]
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, "uint8")
        return t

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    pool.register_scene(Scene(
        id="S2A_FC", datetime="x", cloud_pct=5.0, bbox=[-75, 4, -73, 5],
        red_href="mem://red", nir_href="mem://nir", provider="t",
        bands={"nir": "mem://nir", "red": "mem://red", "green": "mem://green"}))
    r = MagicMock()
    r.tile.return_value = _band()
    with patch("rio_tiler.io.Reader", return_value=r):
        png = pool.render_rgb_tile("S2A_FC", "false_color", 13, 1, 1)
    im = np.array(Image.open(_io.BytesIO(png)).convert("RGBA"))
    assert im[:, :, 0].max() > 200 and im[:, :, 0].min() < 55  # el gradiente se estira
    assert (im[:, :, 3] == 255).all()


def test_parse_rgb_tile_path():
    from imagery_mcp.tiles import parse_rgb_tile_path
    assert parse_rgb_tile_path("/tiles-rgb/S2A_X/true_color/13/1/1.png") == (
        "S2A_X", "true_color", 13, 1, 1)
    assert parse_rgb_tile_path(
        "/tiles-rgb/S2A_X/false_color/13/1/1.png")[1] == "false_color"
    assert parse_rgb_tile_path("/tiles-rgb/S2A_X/combo_malo/13/1/1.png") is None
    assert parse_rgb_tile_path("/tiles/S2A_X/13/1/1.png") is None


def test_bands_from_assets_pc_y_earth_search():
    # B6: el mapeo asset→banda canónica (del que depende el composite) debe ser
    # correcto para ambos proveedores, y un asset ausente queda FUERA.
    from imagery_mcp.providers import _ES_BANDS, _PC_BANDS, _bands_from_assets
    pc = {"B02": {"href": "b2"}, "B03": {"href": "b3"}, "B04": {"href": "b4"},
          "B08": {"href": "b8"}, "B11": {"href": "b11"}, "B12": {"href": "b12"},
          "visual": {"href": "vis"}, "SCL": {"href": "scl"}}
    assert _bands_from_assets(pc, _PC_BANDS) == {
        "blue": "b2", "green": "b3", "red": "b4", "nir": "b8",
        "swir16": "b11", "swir22": "b12", "visual": "vis"}
    # asset ausente → clave fuera (no None espurio)
    assert set(_bands_from_assets({"B04": {"href": "b4"}, "B08": {"href": "b8"}},
                                  _PC_BANDS)) == {"red", "nir"}
    # Earth Search usa nombres directos
    assert _bands_from_assets({"red": {"href": "r"}, "nir": {"href": "n"},
                               "visual": {"href": "v"}}, _ES_BANDS) == {
        "red": "r", "nir": "n", "visual": "v"}


def test_combos_engine_tiles_sincronizados():
    # B7: las dos fuentes de combos del servicio deben coincidir (un typo daría
    # 400/tesela en blanco silenciosa).
    from imagery_mcp.engine import _COMPOSITE_COMBOS
    from imagery_mcp.tiles import _COMPOSITES
    assert set(_COMPOSITE_COMBOS) == set(_COMPOSITES)


def test_render_rgb_agriculture_y_swir(tmp_path, monkeypatch):
    # B7: los combos SWIR (agriculture=swir16/nir/blue, swir=swir22/swir16/red)
    # también renderizan (usan bandas 20m que antes no se probaban).
    import io as _io
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.providers import Scene
    from imagery_mcp.tiles import _TILESIZE, TilePool
    from PIL import Image

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))
    grad = np.tile(np.linspace(300, 3500, _TILESIZE, dtype="float32"), (_TILESIZE, 1))

    def _band():
        t = MagicMock()
        t.data = grad[None, :, :]
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, "uint8")
        return t

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    pool.register_scene(Scene(
        id="S2A_SWIR", datetime="x", cloud_pct=5.0, bbox=[-75, 4, -73, 5],
        red_href="mem://red", nir_href="mem://nir", provider="t",
        bands={"swir16": "mem://s16", "swir22": "mem://s22", "nir": "mem://nir",
               "blue": "mem://blue", "red": "mem://red", "green": "mem://green"}))
    r = MagicMock()
    r.tile.return_value = _band()
    with patch("rio_tiler.io.Reader", return_value=r):
        for combo in ("agriculture", "swir"):
            png = pool.render_rgb_tile("S2A_SWIR", combo, 13, 1, 1)
            im = np.array(Image.open(_io.BytesIO(png)).convert("RGBA"))
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
            assert (im[:, :, 3] == 255).all()   # válido en todos lados → opaco


def test_ndvi_se_renderiza_aunque_falle_abrir_la_scl(tmp_path, monkeypatch):
    # B1: si ABRIR la SCL lanza (fallo transitorio), la tesela NDVI se renderiza
    # SIN máscara (best-effort) en vez de envenenar la entry y dar 502.
    import io as _io
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.providers import Scene
    from imagery_mcp.tiles import _TILESIZE, TilePool
    from PIL import Image

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    def _band(v):
        t = MagicMock()
        t.data = np.full((1, _TILESIZE, _TILESIZE), v, "float32")
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, "uint8")
        return t

    def _make_reader(href):
        if "scl" in href:
            raise RuntimeError("fallo transitorio al abrir la SCL")
        r = MagicMock()
        r.tile.return_value = _band(0.6 if "nir" in href else 0.2)
        return r

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    pool.register_scene(Scene(
        id="S2A_NOSCL", datetime="x", cloud_pct=5.0, bbox=[-75, 4, -73, 5],
        red_href="mem://red", nir_href="mem://nir", provider="t",
        scl_href="mem://scl"))
    with patch("rio_tiler.io.Reader", side_effect=_make_reader):
        png = pool.render_tile("S2A_NOSCL", 13, 1, 1)   # NO debe lanzar
    im = np.array(Image.open(_io.BytesIO(png)).convert("RGBA"))
    assert (im[:, :, 3] == 255).all()   # sin SCL → sin enmascarar, todo visible


@pytest.mark.asyncio
async def test_endpoint_tiles_diff_scope_formato_404(tmp_path, monkeypatch):
    """M8: /tiles-diff aplica scope (403), formato (400) y escena ausente (404)."""
    import httpx
    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.server import AuthMiddleware
    from imagery_mcp.tiles import TilePool

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    async def never(scope, receive, send):
        raise AssertionError("no debió llegar al app MCP")

    app = AuthMiddleware(never, _ring(), RateLimiter(), tile_pool=TilePool(None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t",
    ) as c:
        ro = {"Authorization": "Bearer secreto-ro"}
        ok = {"Authorization": "Bearer secreto-app"}
        a, b = "S2A_ESCENAUNO", "S2B_ESCENADOS"
        assert (await c.get(f"/tiles-diff/{a}/{b}/13/1/1.png", headers=ro)).status_code == 403
        assert (await c.get(f"/tiles-diff/{a}/{b}/13/1/1.png", headers=ok)).status_code == 404
        assert (await c.get(f"/tiles-diff/BAD/{b}/13/1/1.png", headers=ok)).status_code == 400


@pytest.mark.asyncio
async def test_endpoint_tiles_rgb_scope_formato_404(tmp_path, monkeypatch):
    """RGB: /tiles-rgb aplica scope (403), formato (400), combo inválido (no ruta),
    escena ausente (404)."""
    import httpx
    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.server import AuthMiddleware
    from imagery_mcp.tiles import TilePool

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    async def never(scope, receive, send):
        raise AssertionError("no debió llegar al app MCP")

    app = AuthMiddleware(never, _ring(), RateLimiter(), tile_pool=TilePool(None))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t",
    ) as c:
        ro = {"Authorization": "Bearer secreto-ro"}
        ok = {"Authorization": "Bearer secreto-app"}
        sid = "S2A_MSIL2A_TEST"
        assert (await c.get(f"/tiles-rgb/{sid}/true_color/13/1/1.png", headers=ro)).status_code == 403
        assert (await c.get(f"/tiles-rgb/{sid}/true_color/13/1/1.png", headers=ok)).status_code == 404
        assert (await c.get("/tiles-rgb/BAD/true_color/13/1/1.png", headers=ok)).status_code == 400
        # (combo inválido → parse_rgb_tile_path None → no es ruta de tesela;
        #  el rechazo del combo está cubierto en test_parse_rgb_tile_path.)


def test_pool_evict_no_cierra_reader_en_uso(tmp_path, monkeypatch):
    """H2: la evicción LRU NUNCA debe cerrar un reader que otro hilo está leyendo.

    Con el pool a >`_READER_POOL_MAX` escenas activas y N hilos, el código
    anterior cerraba (`old_r.close()`) readers de OTRA escena bajo `self._lock`
    mientras un hilo los usaba en `.tile()` sosteniendo solo su `scene_lock`
    (GDAL no thread-safe ⇒ segfault del worker). El refcount debe diferir ese
    cierre hasta que la escena quede libre.
    """
    import threading
    import time
    from unittest.mock import patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.tiles import _READER_POOL_MAX, _TILESIZE, TilePool

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    violations: list[str] = []

    class _FakeReader:
        def __init__(self, href):
            self.closed = False

        def tile(self, x, y, z, tilesize=256):
            if self.closed:
                violations.append("tile() sobre reader ya cerrado")
            time.sleep(0.004)  # ventana de lectura: da chance a la evicción
            if self.closed:
                violations.append("reader cerrado DURANTE tile()")
            r = type("T", (), {})()
            r.data = np.full((1, _TILESIZE, _TILESIZE), 0.4, dtype="float32")
            r.mask = np.full((_TILESIZE, _TILESIZE), 255, dtype="uint8")
            return r

        def close(self):
            self.closed = True

    class _Prov:
        def get_scene(self, sid):
            return _scene([-75, 4, -73, 5], sid=sid)

        def sign(self, h):
            return h

    pool = TilePool(_Prov())
    n_scenes = _READER_POOL_MAX * 3   # fuerza evicción continua

    with patch("rio_tiler.io.Reader", side_effect=lambda href: _FakeReader(href)):
        def worker(i):
            sid = f"S2-{i % n_scenes}"
            for k in range(6):
                # tiles distintas ⇒ no cortocircuita por caché
                pool.render_tile(sid, 13, 100 + i, 200 + k)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not violations, violations
    # Al terminar, ninguna escena diferida debe quedar con refs>0 sin cerrar.
    assert all(e.refs == 0 for e in pool._pending_close)


def test_teselas_de_una_escena_se_leen_en_paralelo_sin_compartir_reader(tmp_path, monkeypatch):
    """Carriles: varias teselas de UNA escena se leen a la vez, y cada reader (dataset
    GDAL, no thread-safe) lo usa un solo hilo por vez. Antes un lock por escena las
    dibujaba una por una (16 teselas z12 en frío: 13,8 s → 8,8 s con PC, 2026-10-06)."""
    import threading
    import time
    from unittest.mock import patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.tiles import _LANES_PER_SCENE, _TILESIZE, TilePool

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))
    guard = threading.Lock()
    activos = {"ahora": 0, "max": 0}
    violations: list[str] = []

    class _FakeReader:
        def __init__(self, href):
            self.en_uso = False

        def tile(self, x, y, z, tilesize=256):
            with guard:
                if self.en_uso:
                    violations.append("reader leído por dos hilos a la vez")
                self.en_uso = True
                activos["ahora"] += 1
                activos["max"] = max(activos["max"], activos["ahora"])
            time.sleep(0.02)
            with guard:
                self.en_uso = False
                activos["ahora"] -= 1
            r = type("T", (), {})()
            r.data = np.full((1, _TILESIZE, _TILESIZE), 0.4, dtype="float32")
            r.mask = np.full((_TILESIZE, _TILESIZE), 255, dtype="uint8")
            return r

        def close(self):
            pass

    class _Prov:
        def get_scene(self, sid):
            return _scene([-75, 4, -73, 5], sid=sid)

        def sign(self, h):
            return h

    pool = TilePool(_Prov())
    with patch("rio_tiler.io.Reader", side_effect=lambda href: _FakeReader(href)):
        threads = [threading.Thread(target=pool.render_tile, args=("S2-UNA", 13, 100 + i, 200))
                   for i in range(_LANES_PER_SCENE * 2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert not violations, violations
    assert activos["max"] > 1   # la escena ya no se lee de a una tesela
    assert pool.metrics["tiles_rendered"] == _LANES_PER_SCENE * 2


def test_id_irresoluble_cachea_negativo_y_no_deja_lock(tmp_path, monkeypatch):
    """M3: un id irresoluble se recuerda (un solo get_scene) y no crea scene_lock."""
    from unittest.mock import MagicMock

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.tiles import TilePool

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))
    provider = MagicMock()
    provider.get_scene.return_value = None   # el STAC no lo resuelve
    pool = TilePool(provider)

    for _ in range(3):
        with pytest.raises(KeyError, match="ni resoluble"):
            pool.render_tile("S2A_FAKE", 13, 1, 1)

    provider.get_scene.assert_called_once_with("S2A_FAKE")   # no re-POST
    assert "S2A_FAKE" not in pool._scene_locks               # sin lock huérfano
    assert "S2A_FAKE" in pool._negative


def test_scene_locks_acotado_lru():
    """M3: _scene_locks no crece sin cota (evicta LRU los no tomados)."""
    from unittest.mock import MagicMock

    from imagery_mcp.tiles import _SCENE_CACHE_MAX, TilePool

    pool = TilePool(MagicMock())
    for i in range(_SCENE_CACHE_MAX + 50):
        pool._get_scene_lock(f"S2A_{i:05d}")
    assert len(pool._scene_locks) <= _SCENE_CACHE_MAX


def test_scene_id_regex_rechaza_traversal_y_junk():
    """M3: el patrón acepta ids S2 reales y rechaza junk/traversal."""
    from imagery_mcp.tiles import _SCENE_RE

    assert _SCENE_RE.match("S2A_MSIL2A_20260611T152631_R025_T18NXL_20260612")
    assert _SCENE_RE.match("S2B_18NXL_20260611_0_L2A")
    assert not _SCENE_RE.match("S")
    assert not _SCENE_RE.match("../../etc/passwd")
    assert not _SCENE_RE.match("S2A_../evil")
    assert not _SCENE_RE.match("noslash")


# ---------------------------------------------------------------------------
# Umbral de nubes del usuario → STAC (bug: change usaba SIEMPRE el default 20)
# ---------------------------------------------------------------------------
class TestNubosidadPropagada:
    """max_cloud_pct del usuario DEBE llegar a provider.search en ndvi/change/zonal."""

    AOI = {"type": "Polygon", "coordinates": [[
        [-74.12, 4.60], [-74.08, 4.60], [-74.08, 4.64], [-74.12, 4.64],
        [-74.12, 4.60]]]}

    def _prov(self):
        class _Prov:
            def __init__(self):
                self.clouds = []

            def search(self, poly, d0, d1, cloud, limit):
                self.clouds.append(cloud)
                # id único por llamada: en `change` las dos ventanas resuelven
                # a escenas distintas (si no, el guard de auto-comparación de
                # run_change abortaría, ver TestRunChange).
                sid = f"SCLOUD_{len(self.clouds)}"
                return [_scene([-75.0, 4.0, -73.0, 5.0], sid=sid)]

            def sign(self, h):
                return h

        return _Prov()

    def _fake_win(self, monkeypatch):
        win = engine.BandWindow(
            data=np.array([[0.5, 0.2]], dtype="float32"),
            transform=None, crs=None, bounds4326=(-74.12, 4.60, -74.08, 4.64))
        monkeypatch.setattr(engine, "_ndvi_window", lambda *a, **k: win)

    def test_ndvi_usa_el_umbral_del_usuario(self, monkeypatch):
        self._fake_win(monkeypatch)
        prov = self._prov()
        engine.run_ndvi(prov, self.AOI, None, None, None, Limits(),
                        max_cloud_pct=80)
        assert prov.clouds == [80]

    def test_change_usa_el_umbral_en_ambas_ventanas(self, monkeypatch):
        self._fake_win(monkeypatch)
        prov = self._prov()
        engine.run_change(prov, self.AOI, "2026-05-09", "2026-07-06", 15,
                          Limits(), max_cloud_pct=75)
        assert prov.clouds == [75, 75]

    def test_sin_umbral_cae_al_default_declarado(self, monkeypatch):
        self._fake_win(monkeypatch)
        prov = self._prov()
        engine.run_ndvi(prov, self.AOI, None, None, None, Limits())
        assert prov.clouds == [Limits().default_max_cloud_pct]

    def test_umbral_cero_explicito_se_respeta(self, monkeypatch):
        # #23: max_cloud_pct=0 ("solo cielo limpio") NO debe caer al default 20.
        self._fake_win(monkeypatch)
        prov = self._prov()
        engine.run_ndvi(prov, self.AOI, None, None, None, Limits(), max_cloud_pct=0)
        assert prov.clouds == [0]


# ---------------------------------------------------------------------------
# run_change: guardas de alineación (auditoría 2026-07-20, H1/H4)
# Sin ellas, el Δ NDVI puede ser numéricamente falso sin ninguna señal:
#  - dos fechas cercanas → MISMA escena → diff≡0 (falso "vegetación estable")
#  - AOI en borde de zona UTM → escenas en CRS distintos → resta desalineada
# ---------------------------------------------------------------------------
class TestRunChange:
    AOI = {"type": "Polygon", "coordinates": [[
        [-74.12, 4.60], [-74.08, 4.60], [-74.08, 4.64], [-74.12, 4.64],
        [-74.12, 4.60]]]}

    def _prov_secuencial(self, scenes):
        """Devuelve una escena distinta por llamada a search (una por ventana)."""
        seq = iter(scenes)

        class _Prov:
            def search(self, *a, **k):
                return [next(seq)]

            def sign(self, h):
                return h

        return _Prov()

    def _win(self, shape=(2, 2), crs="EPSG:32618", transform="T", value=0.5):
        return engine.BandWindow(
            data=np.full(shape, value, dtype="float32"),
            transform=transform, crs=crs, bounds4326=(-74.12, 4.60, -74.08, 4.64))

    def test_misma_escena_aborta_con_error(self, monkeypatch):
        # Ambas ventanas resuelven al MISMO id → error honesto, no diff≡0.
        s = _scene([-75.0, 4.0, -73.0, 5.0], sid="S_DUP")
        prov = self._prov_secuencial([s, s])
        monkeypatch.setattr(engine, "_ndvi_window",
                            lambda *a, **k: self._win())
        with pytest.raises(engine.ImageryError, match="misma escena"):
            engine.run_change(prov, self.AOI, "2026-06-10", "2026-06-20", 15,
                              Limits())

    def test_grillas_distintas_reproyectan_y_declaran_degraded(self, monkeypatch):
        # scene_a en 18N, scene_b en 19N → CRS distinto → reproject_like.
        sa = _scene([-75.0, 4.0, -73.0, 5.0], sid="S_18N")
        sb = _scene([-75.0, 4.0, -73.0, 5.0], sid="S_19N")
        prov = self._prov_secuencial([sa, sb])
        win_a = self._win(shape=(2, 2), crs="EPSG:32618", value=0.6)
        win_b = self._win(shape=(3, 3), crs="EPSG:32619", value=0.2)
        wins = {"S_18N": win_a, "S_19N": win_b}
        monkeypatch.setattr(engine, "_ndvi_window",
                            lambda prov, scene, *a, **k: wins[scene.id])
        calls = []

        def _spy(win, like):
            calls.append((win, like))
            # simula el reamostreo: devuelve una ventana con la grilla de `like`
            return engine.BandWindow(
                data=np.full(like.data.shape, 0.5, dtype="float32"),
                transform=like.transform, crs=like.crs,
                bounds4326=like.bounds4326)

        monkeypatch.setattr(engine, "reproject_like", _spy)
        out = engine.run_change(prov, self.AOI, "2026-01-10", "2026-06-10", 15,
                                Limits())
        assert len(calls) == 1 and calls[0][1] is win_a  # win_b -> grilla de win_a
        assert "reamostr" in (out["degraded"] or "")
        assert out["diff_stats"] is not None

    def test_misma_grilla_no_reproyecta(self, monkeypatch):
        # Mismo CRS/transform/shape (distinta escena) → resta directa, sin aviso.
        sa = _scene([-75.0, 4.0, -73.0, 5.0], sid="S_A")
        sb = _scene([-75.0, 4.0, -73.0, 5.0], sid="S_B")
        prov = self._prov_secuencial([sa, sb])
        win = self._win()
        monkeypatch.setattr(engine, "_ndvi_window", lambda *a, **k: win)

        def _boom(*a, **k):
            raise AssertionError("no debió reproyectar en misma grilla")

        monkeypatch.setattr(engine, "reproject_like", _boom)
        out = engine.run_change(prov, self.AOI, "2026-01-10", "2026-06-10", 15,
                                Limits())
        assert out["degraded"] is None

    def test_registra_escenas_y_expone_tiles_diff(self, monkeypatch):
        # M8: run_change registra AMBAS escenas y publica un tiles-diff simétrico.
        sa = _scene([-75.0, 4.0, -73.0, 5.0], sid="S2A_AAA")
        sb = _scene([-75.0, 4.0, -73.0, 5.0], sid="S2B_BBB")
        prov = self._prov_secuencial([sa, sb])
        monkeypatch.setattr(engine, "_ndvi_window", lambda *a, **k: self._win())
        registered = []
        out = engine.run_change(prov, self.AOI, "2026-01-10", "2026-06-10", 15,
                                Limits(), on_scene=registered.append)
        assert [s.id for s in registered] == ["S2A_AAA", "S2B_BBB"]
        t = out["tiles"]
        assert t["url_template"].startswith("/tiles-diff/S2A_AAA/S2B_BBB/")
        assert t["kind"] == "diff"
        assert t["rescale"][0] == -t["rescale"][1]   # simétrico alrededor de 0
        assert t["rescale"][1] >= 0.1

    def test_reproject_like_alinea_a_la_grilla_destino(self):
        # Validación REAL de reproject_like con rasterio (sin red).
        from affine import Affine
        from rasterio.crs import CRS
        from rasterio.errors import CRSError
        try:
            CRS.from_epsg(32618)
        except CRSError as exc:  # PROJ del entorno roto (p.ej. PROJ_LIB de otra
            pytest.skip(f"PROJ del entorno no resuelve EPSG (env, no código): {exc}")
        src = engine.BandWindow(
            data=np.linspace(0.1, 0.9, 16, dtype="float32").reshape(4, 4),
            transform=Affine(10.0, 0, 500000, 0, -10.0, 460000),
            crs=CRS.from_epsg(32618), bounds4326=(-74.12, 4.60, -74.08, 4.64))
        like = engine.BandWindow(
            data=np.zeros((6, 5), dtype="float32"),
            transform=Affine(10.0, 0, 500010, 0, -10.0, 460010),
            crs=CRS.from_epsg(32618), bounds4326=(-74.12, 4.60, -74.08, 4.64))
        out = engine.reproject_like(src, like)
        assert out.data.shape == (6, 5)          # grilla de `like`
        assert out.crs == like.crs and out.transform == like.transform
        assert np.isfinite(out.data).any()       # hubo solape real

    def test_window_bounds4326_extent_real_y_fallback(self):
        # #16: extent REAL del recorte; fallback al bbox si falta transform/crs.
        from affine import Affine
        from rasterio.crs import CRS
        from rasterio.errors import CRSError
        try:
            CRS.from_epsg(32618)
        except CRSError as exc:
            pytest.skip(f"PROJ del entorno no resuelve EPSG (env, no código): {exc}")
        win = engine.BandWindow(
            data=np.zeros((10, 10), dtype="float32"),
            transform=Affine(10.0, 0, 500000, 0, -10.0, 460000),
            crs=CRS.from_epsg(32618), bounds4326=(0, 0, 0, 0))
        b = engine._window_bounds4326(win, (-99, -99, -99, -99))
        assert len(b) == 4 and b != [-99, -99, -99, -99]   # extent real, no fallback
        # Sin transform/crs (ventana fake de tests) → fallback al bbox pedido.
        win2 = engine.BandWindow(data=np.zeros((2, 2), "float32"),
                                 transform=None, crs=None, bounds4326=(0, 0, 0, 0))
        assert engine._window_bounds4326(win2, (-74.1, 4.6, -74.0, 4.7)) == \
            [-74.1, 4.6, -74.0, 4.7]


# ---------------------------------------------------------------------------
# SAS token: expiry real + re-firma ante 401/403 (auditoría 2026-07-20, M4)
# ---------------------------------------------------------------------------
class TestSASToken:
    def _resp(self, token, expiry):
        from unittest.mock import MagicMock
        r = MagicMock()
        r.raise_for_status = lambda: None
        r.json.return_value = {"token": token, "msft:expiry": expiry}
        return r

    def test_sign_usa_msft_expiry_real_no_now_mas_30(self, monkeypatch):
        from datetime import datetime, timezone

        import imagery_mcp.providers as pv
        prov = pv.PlanetaryComputerProvider()
        monkeypatch.setattr(pv.httpx, "get",
                            lambda *a, **k: self._resp("TOK", "2099-01-01T00:00:00Z"))
        assert prov.sign("https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/cog.tif") == "https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/cog.tif?TOK"
        exp = datetime(2099, 1, 1, tzinfo=UTC).timestamp() - 300
        assert abs(prov._tokens["sentinel2l2a01/sentinel2-l2"][1] - exp) < 1.0

    def test_sign_cachea_hasta_expiry(self, monkeypatch):
        import imagery_mcp.providers as pv
        prov = pv.PlanetaryComputerProvider()
        calls = {"n": 0}

        def _get(*a, **k):
            calls["n"] += 1
            return self._resp(f"TOK{calls['n']}", "2099-01-01T00:00:00Z")

        monkeypatch.setattr(pv.httpx, "get", _get)
        assert prov.sign("https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/a.tif").endswith("?TOK1")
        assert prov.sign("https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/b.tif").endswith("?TOK1")   # cacheado, sin re-fetch
        assert calls["n"] == 1

    def test_invalidate_token_fuerza_refirma(self, monkeypatch):
        import imagery_mcp.providers as pv
        prov = pv.PlanetaryComputerProvider()
        calls = {"n": 0}

        def _get(*a, **k):
            calls["n"] += 1
            return self._resp(f"TOK{calls['n']}", "2099-01-01T00:00:00Z")

        monkeypatch.setattr(pv.httpx, "get", _get)
        assert prov.sign("https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/a.tif").endswith("?TOK1")
        prov.invalidate_token()
        assert prov.sign("https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/b.tif").endswith("?TOK2")   # re-firmó tras invalidar
        assert calls["n"] == 2

    def test_cada_cuenta_de_almacenamiento_tiene_su_token(self, monkeypatch):
        """T5.5: Landsat y Sentinel-2 viven en cuentas distintas de PC; un solo token
        (el de sentinel-2-l2a) daba 403 en todas las bandas de Landsat."""
        import imagery_mcp.providers as pv
        prov = pv.PlanetaryComputerProvider()
        pedidas = []

        def _get(url, *a, **k):
            pedidas.append(url)
            return self._resp(f"TOK{len(pedidas)}", "2099-01-01T00:00:00Z")

        monkeypatch.setattr(pv.httpx, "get", _get)
        s2 = prov.sign("https://sentinel2l2a01.blob.core.windows.net/sentinel2-l2/a.tif")
        ls = prov.sign("https://landsateuwest.blob.core.windows.net/landsat-c2/l2/b.tif")
        assert s2.endswith("?TOK1") and ls.endswith("?TOK2")
        assert pedidas == [f"{pv._PC_SAS}/sentinel2l2a01/sentinel2-l2",
                           f"{pv._PC_SAS}/landsateuwest/landsat-c2"]

    def test_read_band_window_refirma_en_403(self, monkeypatch):
        from unittest.mock import MagicMock

        import rasterio
        monkeypatch.setattr(engine.time, "sleep", lambda *a: None)

        def _boom(*a, **k):
            raise RuntimeError("CPLE_AppDefined ... HTTP response code: 403")

        monkeypatch.setattr(rasterio, "open", _boom)
        resign = MagicMock(return_value="fresh?sig=new")
        with pytest.raises(engine.ImageryError):
            engine.read_band_window("stale?sig=old", (-74.12, 4.60, -74.08, 4.64),
                                    retries=2, resign=resign)
        assert resign.called   # ante el 403 se intentó re-firmar

    def test_is_auth_error_clasifica(self):
        assert engine._is_auth_error(RuntimeError("... HTTP response code: 403"))
        assert engine._is_auth_error(RuntimeError("AccessDenied"))
        assert not engine._is_auth_error(RuntimeError("connection reset by peer"))


# ---------------------------------------------------------------------------
# Reflectancia: scale/offset del asset STAC (auditoría 2026-09-08, §1.3)
#
# Desde el baseline 04.00 (enero de 2022) el L2A trae BOA_ADD_OFFSET = -1000. En
# un cociente normalizado el offset se cancela en el numerador pero NO en el
# denominador, así que un NDVI de DN crudo sale ~0,20 bajo. Con las reflectancias
# del informe (red 0,08 / nir 0,40):
#
#   NDVI verdadero (reflectancia)  : 0.6667
#   NDVI de DN crudo, pre-04.00    : 0.6667   (800 / 4000)
#   NDVI de DN crudo, post-04.00   : 0.4706   (1800 / 5000)
#   sesgo post-04.00               : -0.1961
#
# Toda esta sección es OFFLINE: los metadatos STAC se fabrican aquí.
# ---------------------------------------------------------------------------
_RED_REF, _NIR_REF = 0.08, 0.40          # reflectancias BOA del ejemplo
_NDVI_VERDADERO = 0.6667
_NDVI_SESGADO = 0.4706                   # el que da el DN crudo post-04.00
_DN_PRE = (800, 4000)                    # DN L2A baseline < 04.00
_DN_POST = (1800, 5000)                  # el MISMO suelo, baseline >= 04.00
_S2_SCALE, _S2_OFFSET = 0.0001, -0.1     # lo que publica `raster:bands`


def _scaling(scale=_S2_SCALE, offset=_S2_OFFSET, source=_SRC_ASSET):
    from imagery_mcp.providers import BandScaling
    return BandScaling(scale=scale, offset=offset, source=source)


def _ndvi_de(red_dn, nir_dn, scaling=None) -> float:
    """NDVI de un par de DN, con el factor aplicado como lo hace el motor."""
    red = engine.apply_scaling(np.array([[float(red_dn)]], "float32"), scaling)
    nir = engine.apply_scaling(np.array([[float(nir_dn)]], "float32"), scaling)
    return float(engine.compute_ndvi(red, nir)[0, 0])


class TestAritmeticaDelOffset:
    def test_post_0400_con_offset_da_el_mismo_ndvi_que_pre_0400(self):
        """El corazón del §1.3: el MISMO suelo, dos baselines, un solo NDVI.

        Sin aplicar el offset, la escena post-04.00 devuelve 0.4706 en vez de
        0.6667 — 0,20 por debajo — y nada en el payload lo delata."""
        sc = _scaling()
        pre = _ndvi_de(*_DN_PRE, scaling=_scaling(offset=0.0))   # pre-04.00: sin offset
        post = _ndvi_de(*_DN_POST, scaling=sc)
        crudo = _ndvi_de(*_DN_POST, scaling=None)                # el bug

        assert pre == pytest.approx(_NDVI_VERDADERO, abs=1e-4)
        assert post == pytest.approx(_NDVI_VERDADERO, abs=1e-4)
        assert post == pytest.approx(pre, abs=1e-6), (
            "el offset del baseline 04.00 no se está cancelando: post-04.00 y "
            "pre-04.00 deben dar el MISMO NDVI sobre el mismo suelo"
        )
        # Y la magnitud del sesgo que se evita es la que calcula la auditoría.
        assert crudo == pytest.approx(_NDVI_SESGADO, abs=1e-4)
        assert crudo - post == pytest.approx(-0.1961, abs=1e-3)

    def test_reflectancia_recuperada_es_la_real(self):
        # El factor no solo "arregla el cociente": devuelve la reflectancia BOA.
        sc = _scaling()
        arr = engine.apply_scaling(
            np.array([[float(_DN_POST[0]), float(_DN_POST[1])]], "float32"), sc)
        assert arr[0, 0] == pytest.approx(_RED_REF, abs=1e-6)
        assert arr[0, 1] == pytest.approx(_NIR_REF, abs=1e-6)

    def test_sin_metadatos_no_se_inventa_ningun_factor(self):
        """Datos viejos: sin scale/offset publicados, factor 1 / offset 0.

        Fabricar un BOA_ADD_OFFSET donde el proveedor no lo declara metería el
        sesgo INVERSO en toda la serie anterior a 2022."""
        arr = np.array([[800.0, 4000.0]], "float32")
        assert engine.apply_scaling(arr, None) is arr          # ni una copia
        from imagery_mcp.providers import BandScaling
        assert engine.apply_scaling(arr, BandScaling()) is arr  # identidad explícita
        assert _ndvi_de(*_DN_PRE) == pytest.approx(_NDVI_VERDADERO, abs=1e-4)

    def test_no_se_aplica_dos_veces_si_ya_viene_en_reflectancia(self):
        """Si el proveedor ya entrega reflectancia, el asset no publica factor
        (o publica la identidad) y el array no se toca."""
        refl = np.array([[_RED_REF, _NIR_REF]], "float32")
        from imagery_mcp.providers import BandScaling
        assert engine.apply_scaling(refl, BandScaling(1.0, 0.0)) is refl
        assert _ndvi_de(0.08, 0.40) == pytest.approx(_NDVI_VERDADERO, abs=1e-4)
        # Y aplicarlo dos veces sería visible: 0.08*1e-4-0.1 = -0.09992.
        doble = engine.apply_scaling(engine.apply_scaling(refl, _scaling()), _scaling())
        assert doble[0, 0] < -0.09, "guardarraíl: la doble aplicación sí se nota"

    def test_el_nodata_cero_sigue_siendo_nodata_con_offset(self):
        """El 0 del COG es nodata del recorte, no un DN.

        Con offset dejaría de valer 0 y `compute_ndvi` —que lo detecta comparando
        con 0— lo tomaría por dato válido, resucitando el NDVI espurio de ±1 que
        cerró el #13."""
        arr = np.array([[0.0, 1800.0]], "float32")
        out = engine.apply_scaling(arr, _scaling())
        assert np.isnan(out[0, 0]) and out[0, 1] == pytest.approx(_RED_REF)
        # y el NDVI de un píxel con una banda en nodata sigue siendo NaN
        assert np.isnan(_ndvi_de(0, 5000, scaling=_scaling()))
        assert np.isnan(_ndvi_de(1800, 0, scaling=_scaling()))


class TestScaleOffsetDesdeElStac:
    """Lectura del factor desde metadatos STAC fabricados (sin red)."""

    def test_raster_bands_lista_earth_search(self):
        from imagery_mcp.providers import _asset_scaling
        asset = {"href": "s3://x/B04.tif", "raster:bands": [
            {"nodata": 0, "data_type": "uint16", "spatial_resolution": 10,
             "scale": _S2_SCALE, "offset": _S2_OFFSET}]}
        sc = _asset_scaling(asset)
        assert (sc.scale, sc.offset) == (_S2_SCALE, _S2_OFFSET)

    def test_claves_planas_stac_11(self):
        from imagery_mcp.providers import _asset_scaling
        sc = _asset_scaling({"href": "h", "raster:scale": _S2_SCALE,
                             "raster:offset": _S2_OFFSET})
        assert (sc.scale, sc.offset) == (_S2_SCALE, _S2_OFFSET)
        # `bands` (STAC 1.1) con las claves prefijadas
        sc2 = _asset_scaling({"href": "h", "bands": [
            {"raster:scale": _S2_SCALE, "raster:offset": _S2_OFFSET}]})
        assert (sc2.scale, sc2.offset) == (_S2_SCALE, _S2_OFFSET)

    def test_asset_sin_factor_devuelve_none(self):
        from imagery_mcp.providers import _asset_scaling
        assert _asset_scaling({"href": "h"}) is None
        assert _asset_scaling({"href": "h", "raster:bands": [{"nodata": 0}]}) is None
        assert _asset_scaling({}) is None

    def test_factor_corrupto_no_se_adivina(self):
        from imagery_mcp.providers import _asset_scaling
        assert _asset_scaling({"raster:scale": "no-es-un-numero"}) is None
        # solo offset publicado → scale queda en 1.0 (no se inventa)
        sc = _asset_scaling({"raster:offset": -0.1})
        assert (sc.scale, sc.offset) == (1.0, -0.1)

    def _feature(self, assets: dict, baseline: str = "05.11") -> dict:
        return {
            "id": "S2A_MSIL2A_20240611T152631_R025_T18NXL_20240612",
            "bbox": [-76.0, 3.0, -74.0, 5.0],
            "properties": {"datetime": "2024-06-11T15:26:31Z",
                           "eo:cloud_cover": 8.2,
                           "s2:processing_baseline": baseline},
            "assets": assets,
        }

    def test_planetary_computer_parse_lee_factor_y_baseline(self):
        from imagery_mcp.providers import PlanetaryComputerProvider
        band = {"href": "https://x/B04.tif",
                "raster:bands": [{"scale": _S2_SCALE, "offset": _S2_OFFSET}]}
        scene = PlanetaryComputerProvider()._parse(self._feature(
            {"B04": band, "B08": dict(band, href="https://x/B08.tif"),
             "SCL": {"href": "https://x/SCL.tif"}}))
        assert scene.processing_baseline == "05.11"
        assert scene.scaling_for("red").offset == _S2_OFFSET
        assert scene.scaling_for("nir").scale == _S2_SCALE

    def test_earth_search_parse_lee_factor_y_baseline(self):
        from imagery_mcp.providers import EarthSearchProvider
        band = {"href": "s3://x/B04.tif",
                "raster:bands": [{"scale": _S2_SCALE, "offset": _S2_OFFSET}]}
        scene = EarthSearchProvider()._parse(self._feature(
            {"red": band, "nir": dict(band, href="s3://x/B08.tif")},
            baseline="04.00"))
        assert scene.processing_baseline == "04.00"
        assert scene.scaling_for("red").offset == _S2_OFFSET

    def test_escena_pre_0400_queda_en_la_identidad(self):
        """Producto viejo: assets sin `raster:bands` → factor identidad, no None
        suelto ni un offset fabricado."""
        from imagery_mcp.providers import EarthSearchProvider
        scene = EarthSearchProvider()._parse(self._feature(
            {"red": {"href": "s3://x/B04.tif"}, "nir": {"href": "s3://x/B08.tif"}},
            baseline="02.14"))
        sc = scene.scaling_for("red")
        # Escalón 3: se deriva del baseline, con offset 0 porque es pre-04.00.
        assert (sc.scale, sc.offset) == (_S2_SCALE, 0.0)
        assert sc.source == _SRC_BASELINE
        assert scene.processing_baseline == "02.14"
        # El número no se mueve respecto al DN crudo: un escalado puro se cancela
        # en un cociente normalizado. Lo que se gana es que el array queda en
        # reflectancia y por tanto es comparable con una post-04.00 corregida.
        assert _ndvi_de(*_DN_PRE, scaling=sc) == pytest.approx(_NDVI_VERDADERO, abs=1e-4)

    def test_sin_baseline_legible_no_hay_de_donde_derivar(self):
        """El único caso que queda sin factor: ni `raster:bands` ni un baseline
        parseable. Ahí sí identidad — y el payload lo advierte."""
        from imagery_mcp.providers import EarthSearchProvider
        prov = EarthSearchProvider()
        sin = prov._parse(self._feature(
            {"red": {"href": "r"}, "nir": {"href": "n"}}, baseline=None))
        basura = prov._parse(self._feature(
            {"red": {"href": "r"}, "nir": {"href": "n"}}, baseline="sin-dato"))
        assert sin.scaling == {} and sin.scaling_for("red").is_identity
        assert basura.scaling == {} and basura.scaling_for("nir").is_identity

    def test_el_tci_visual_nunca_recibe_factor_derivado(self):
        """`visual` es el TCI: 8-bit ya renderizado, no reflectancia. El
        BOA_ADD_OFFSET no le aplica y derivárselo sería un factor falso."""
        from imagery_mcp.providers import EarthSearchProvider
        scene = EarthSearchProvider()._parse(self._feature(
            {"red": {"href": "r"}, "nir": {"href": "n"},
             "visual": {"href": "tci"}}, baseline="05.12"))
        assert "visual" not in scene.scaling
        assert scene.scaling_for("visual").is_identity
        assert not scene.scaling_for("red").is_identity   # las bandas sí

    def test_search_y_get_scene_leen_lo_mismo(self, monkeypatch):
        """Las dos rutas de resolución comparten `_parse`: si solo una leyera el
        factor, el NDVI saldría bien o sesgado según por dónde entró la escena
        (search en el análisis, get_scene en el teselado tras un restart)."""
        import imagery_mcp.providers as pv
        band = {"href": "https://x/B04.tif",
                "raster:bands": [{"scale": _S2_SCALE, "offset": _S2_OFFSET}]}
        feat = self._feature({"B04": band, "B08": dict(band, href="https://x/B08.tif")})

        class _Resp:
            def raise_for_status(self): return None
            def json(self): return {"features": [feat]}

        monkeypatch.setattr(pv.httpx, "post", lambda *a, **k: _Resp())
        prov = pv.PlanetaryComputerProvider()
        por_search = prov.search({}, "2024-01-01", "2024-12-31", 20, 1)[0]
        por_id = prov.get_scene(feat["id"])
        assert por_search.scaling == por_id.scaling
        assert por_search.processing_baseline == por_id.processing_baseline == "05.11"


class TestPayloadDeclaraElFactor:
    """§1.3: el consumidor tiene que poder AUDITAR qué factor se aplicó."""

    def test_declara_el_factor_publicado_por_el_asset(self):
        from imagery_mcp.providers import Scene
        s = Scene(id="S", datetime="2024-06-11T15:00:00Z", cloud_pct=5.0,
                  bbox=[-76, 3, -74, 5], red_href="r", nir_href="n",
                  provider="test", processing_baseline="05.11",
                  scaling={"red": _scaling(), "nir": _scaling()})
        p = engine.reflectance_payload(s)
        assert p["aplicado"] is True
        assert p["red"] == {"scale": _S2_SCALE, "offset": _S2_OFFSET}
        assert p["processing_baseline"] == "05.11"
        assert p["origen"] == _SRC_ASSET and p["derivado"] is False
        assert "publicados por el asset" in p["fuente"]
        assert "aviso" not in p        # está corregida: no hay nada que advertir

    def test_declara_que_el_factor_es_DERIVADO_no_publicado(self):
        """Un dato publicado y una inferencia nuestra no valen lo mismo aunque
        den el mismo número: el consumidor tiene que poder distinguirlos."""
        from imagery_mcp.providers import PlanetaryComputerProvider
        scene = PlanetaryComputerProvider()._parse({
            "id": "S2A_PC", "bbox": [-76, 3, -74, 5],
            "properties": {"datetime": "2024-06-11T15:26:31Z",
                           "eo:cloud_cover": 5.0,
                           "s2:processing_baseline": "05.12"},
            "assets": {"B04": {"href": "r"}, "B08": {"href": "n"}}})
        p = engine.reflectance_payload(scene)
        assert p["aplicado"] is True
        assert p["origen"] == _SRC_BASELINE and p["derivado"] is True
        assert "DERIVADO" in p["fuente"] and "ESA" in p["fuente"]
        assert p["red"] == {"scale": _S2_SCALE, "offset": _S2_OFFSET}
        assert "aviso" not in p        # ya no hay sesgo: no hay nada que advertir

    def test_el_aviso_solo_queda_para_lo_irresoluble(self):
        """Ni `raster:bands` ni baseline legible. Si no se puede corregir, al
        menos se DECLARA — el silencio es lo que produjo el hallazgo."""
        s = _scene([-76, 3, -74, 5])          # sin scaling y sin baseline
        p = engine.reflectance_payload(s)
        assert p["aplicado"] is False and p["origen"] == _SRC_IDENTIDAD
        assert "aviso" in p
        # La escena del helper es de 2026 → posterior al corte: se dice.
        assert "BOA_ADD_OFFSET" in p["aviso"] and "2022-01-25" in p["aviso"]

    def test_sin_factor_y_escena_vieja_avisa_sin_acusar_de_sesgo(self):
        s = _scene([-76, 3, -74, 5])
        s.datetime = "2019-03-04T15:00:00Z"    # anterior al corte
        p = engine.reflectance_payload(s)
        assert "aviso" in p                     # sigue sin saberse el factor…
        assert "BOA_ADD_OFFSET" not in p["aviso"]   # …pero no se afirma el sesgo

    def test_baseline_value_no_adivina_y_compara_numerico(self):
        assert engine.baseline_value("04.00") == 4.0
        assert engine.baseline_value("05.11") == 5.11
        assert engine.baseline_value(None) is None
        assert engine.baseline_value("N/A") is None   # desconocido, NO 0.0
        # Comparación NUMÉRICA, no de texto.
        assert engine.baseline_value("10.00") > engine._BOA_OFFSET_BASELINE
        assert engine.baseline_value("05.12") > engine._BOA_OFFSET_BASELINE
        assert engine.baseline_value("02.14") < engine._BOA_OFFSET_BASELINE
        # El bug que se evita: como texto, '05.12' y '10.00' caerían los DOS del
        # lado pre-04.00 (comparan por el primer carácter) y sus escenas se
        # quedarían sin el BOA_ADD_OFFSET.
        assert "05.12" < "4" and "10.00" < "4"


class TestTresOrigenesDelFactor:
    """§1.3, escalón por escalón. Los tres dan el mismo par (0.0001, -0.1) para
    un item post-04.00 — que coincidan es justo lo que valida la derivación."""

    def _feat(self, assets, baseline="05.12"):
        return {"id": "S2A_X", "bbox": [-76, 3, -74, 5],
                "properties": {"datetime": "2024-06-11T15:26:31Z",
                               "eo:cloud_cover": 5.0,
                               "s2:processing_baseline": baseline},
                "assets": assets}

    def test_1_asset_gana_sobre_todo(self):
        from imagery_mcp.providers import PlanetaryComputerProvider
        band = {"href": "r", "raster:bands": [{"scale": 0.002, "offset": -0.5}]}
        scene = PlanetaryComputerProvider()._parse(
            self._feat({"B04": band, "B08": dict(band, href="n")}),
            coll_lookup=lambda: {"red": _scaling(), "nir": _scaling()})
        sc = scene.scaling_for("red")
        assert (sc.scale, sc.offset) == (0.002, -0.5)   # el del asset, no otro
        assert sc.source == _SRC_ASSET

    def test_2_coleccion_cuando_el_asset_calla(self):
        from imagery_mcp.providers import BandScaling, PlanetaryComputerProvider
        de_coleccion = BandScaling(0.0001, -0.1, source=_SRC_COLECCION)
        scene = PlanetaryComputerProvider()._parse(
            self._feat({"B04": {"href": "r"}, "B08": {"href": "n"}}),
            coll_lookup=lambda: {"red": de_coleccion, "nir": de_coleccion})
        assert scene.scaling_for("red").source == _SRC_COLECCION

    def test_3_derivado_cuando_callan_los_dos(self):
        from imagery_mcp.providers import PlanetaryComputerProvider
        scene = PlanetaryComputerProvider()._parse(
            self._feat({"B04": {"href": "r"}, "B08": {"href": "n"}}),
            coll_lookup=lambda: {})          # la colección tampoco lo trae (PC)
        sc = scene.scaling_for("red")
        assert (sc.scale, sc.offset) == (_S2_SCALE, _S2_OFFSET)
        assert sc.source == _SRC_BASELINE

    def test_la_derivacion_coincide_con_lo_que_publica_earth_search(self):
        """Comprobación cruzada: la fórmula de ESA aplicada al baseline da el
        MISMO par que Earth Search publica en `raster:bands` para el mismo
        `s2:processing_baseline`. Si algún día dejan de coincidir, la derivación
        dejó de ser válida y este test lo dice."""
        from imagery_mcp.providers import EarthSearchProvider, scaling_from_baseline
        publicado = EarthSearchProvider()._parse(self._feat({
            "red": {"href": "r", "raster:bands": [
                {"nodata": 0, "data_type": "uint16",
                 "scale": _S2_SCALE, "offset": _S2_OFFSET}]},
            "nir": {"href": "n", "raster:bands": [
                {"scale": _S2_SCALE, "offset": _S2_OFFSET}]}})).scaling_for("red")
        derivado = scaling_from_baseline("05.12")
        assert (publicado.scale, publicado.offset) == (derivado.scale, derivado.offset)
        assert publicado.source != derivado.source   # mismo número, distinta autoridad

    def test_la_coleccion_solo_se_consulta_si_hace_falta(self):
        """El lookup del escalón 2 es perezoso: con el factor en el asset no se
        invoca (ni una petición de red de más en cada búsqueda de Earth Search)."""
        from imagery_mcp.providers import EarthSearchProvider
        llamadas = []

        def _lookup():
            llamadas.append(1)
            return {}

        band = {"href": "r", "raster:bands": [
            {"scale": _S2_SCALE, "offset": _S2_OFFSET}]}
        EarthSearchProvider()._parse(
            self._feat({"red": band, "nir": dict(band, href="n")}), _lookup)
        assert llamadas == []
        # …y en cuanto una banda se queda sin factor, se consulta UNA sola vez.
        EarthSearchProvider()._parse(
            self._feat({"red": {"href": "r"}, "nir": {"href": "n"}}), _lookup)
        assert llamadas == [1]


class TestRunChangeBaseline:
    """§1.3: `run_change` acepta dos fechas cualesquiera. Un 'deforestación 2020
    vs 2024' cruza el corte del baseline 04.00 y el umbral `diff < -0.1` marcaría
    casi el 100 % de los píxeles como pérdida fuerte sobre vegetación intacta."""

    AOI = TestRunChange.AOI

    def _run(self, a: Scene, b: Scene, monkeypatch):
        prov = TestRunChange()._prov_secuencial([a, b])
        monkeypatch.setattr(
            engine, "_ndvi_window",
            lambda *ar, **k: engine.BandWindow(
                data=np.full((2, 2), 0.5, "float32"), transform="T",
                crs="EPSG:32618", bounds4326=(-74.12, 4.60, -74.08, 4.64)))
        return engine.run_change(prov, self.AOI, "2020-06-10", "2024-06-10", 15,
                                 Limits())

    def _con(self, sid, baseline, scaled=False, derivado=False) -> Scene:
        s = _scene([-75.0, 4.0, -73.0, 5.0], sid=sid)
        s.processing_baseline = baseline
        if scaled:
            s.scaling = {"red": _scaling(), "nir": _scaling()}
        elif derivado:
            # Lo que produce hoy el camino de Planetary Computer: factor derivado
            # del baseline, distinto a cada lado del corte 04.00.
            from imagery_mcp.providers import scaling_from_baseline
            sc = scaling_from_baseline(baseline)
            s.scaling = {"red": sc, "nir": sc}
        return s

    def test_rechaza_mezclar_baselines_sin_factor(self, monkeypatch):
        """2020 (02.14, DN sin offset) contra 2024 (05.11, DN CON offset dentro):
        el Δ sería un artefacto de ~-0,20 en todo el AOI. Error tipado.

        Este caso ya casi no se alcanza: hace falta que el baseline sea legible
        (para saber el lado del corte) y a la vez no lo sea (para no derivar el
        factor), lo que solo pasa con escenas construidas a mano. La guarda se
        queda porque es la red de seguridad si algún proveedor futuro entrega DN
        sin factor derivable."""
        with pytest.raises(engine.ImageryError, match="no son comparables"):
            self._run(self._con("S_2020", "02.14"),
                      self._con("S_2024", "05.11"), monkeypatch)

    def test_acepta_2020_vs_2024_con_derivacion_en_ambas(self, monkeypatch):
        """El caso REAL de Planetary Computer tras el arreglo: ninguna de las dos
        trae `raster:bands`, las dos derivan del baseline y quedan a lados
        opuestos del corte —02.14 con offset 0, 05.12 con offset -0.1—. Los
        números del factor DIFIEREN, pero las dos están en reflectancia BOA, así
        que la resta es legítima y la guarda NO debe dispararse.

        Es justo lo que rompería una guarda que comparase scale/offset en vez de
        comparar 'ambas en reflectancia'."""
        out = self._run(self._con("S_2020", "02.14", derivado=True),
                        self._con("S_2024", "05.12", derivado=True), monkeypatch)
        ra = out["reflectance"]["scene_a"]
        rb = out["reflectance"]["scene_b"]
        assert ra["red"]["offset"] == 0.0 and rb["red"]["offset"] == _S2_OFFSET
        assert ra["derivado"] is True and rb["derivado"] is True
        assert out["diff_stats"]["mean"] == pytest.approx(0.0)

    def test_acepta_una_publicada_contra_una_derivada(self, monkeypatch):
        """`fuente: stac-asset` contra `fuente: baseline-esa`: distinta autoridad,
        misma unidad física. Comparables."""
        out = self._run(self._con("S_ES", "05.12", scaled=True),
                        self._con("S_PC", "05.12", derivado=True), monkeypatch)
        assert out["reflectance"]["scene_a"]["origen"] == _SRC_ASSET
        assert out["reflectance"]["scene_b"]["origen"] == _SRC_BASELINE
        assert out["diff_stats"] is not None

    def test_rechaza_una_corregida_y_una_cruda(self, monkeypatch):
        """Peor aún que el anterior: una escena en reflectancia contra una en DN
        (órdenes de magnitud distintos), que es lo que pasa cuando solo una de
        las dos trae `raster:bands`."""
        with pytest.raises(engine.ImageryError, match="no son comparables"):
            self._run(self._con("S_A", "05.11", scaled=True),
                      self._con("S_B", "05.11"), monkeypatch)

    def test_acepta_dos_baselines_distintos_ya_corregidos(self, monkeypatch):
        """Lo que NO debe bloquear: 04.00 contra 05.11 con el factor publicado en
        ambas. Están las dos en reflectancia, son restables, y prohibirlo mataría
        el caso de uso legítimo (comparar años) sin ganar nada."""
        out = self._run(self._con("S_A", "04.00", scaled=True),
                        self._con("S_B", "05.11", scaled=True), monkeypatch)
        assert out["diff_stats"]["mean"] == pytest.approx(0.0)
        assert out["reflectance"]["scene_a"]["aplicado"] is True
        assert out["reflectance"]["scene_b"]["aplicado"] is True

    def test_acepta_dos_escenas_del_mismo_lado_del_corte(self, monkeypatch):
        out = self._run(self._con("S_A", "05.00"), self._con("S_B", "05.11"),
                        monkeypatch)
        assert out["reflectance"]["scene_a"]["processing_baseline"] == "05.00"

    def test_dos_desconocidos_NO_son_el_mismo_desconocido(self, monkeypatch):
        """BLOQUEANTE 2 de la revisión adversa 2026-09-08.

        `radiometry` devolvía `("dn-crudo", None)` cuando el baseline no era
        legible, con la excusa de que "desconocido solo casa con desconocido".
        Pero la tupla es igual a SÍ MISMA: dos escenas sin baseline casaban y la
        guarda no disparaba jamás. Escenario del revisor, con un proveedor que no
        publique ni `raster:bands` ni baseline: 2020-07 (DN sin offset) contra
        2024-07 (DN con el offset dentro) se restaban y salía una pérdida falsa
        de ~-0,20 en TODO el AOI → con `diff < -0.1`, casi el 100 % del AOI
        marcado como deforestación sobre bosque intacto."""
        a = self._con("S_2020", None)
        a.datetime = "2020-07-14T15:00:00Z"     # antes del corte (2022-01-25)
        b = self._con("S_2024", None)
        b.datetime = "2024-07-14T15:00:00Z"     # después
        with pytest.raises(engine.ImageryError, match="no son comparables"):
            self._run(a, b, monkeypatch)

    def test_dos_escenas_sin_baseline_del_mismo_lado_pasan(self, monkeypatch):
        """La otra mitad: si las fechas caen del MISMO lado del corte, las dos
        traen (o no traen) el offset y la resta es legítima. No se bloquea."""
        a = self._con("S_A", None)
        a.datetime = "2024-03-01T15:00:00Z"
        b = self._con("S_B", None)
        b.datetime = "2024-09-01T15:00:00Z"
        assert self._run(a, b, monkeypatch)["degraded"] is None

    def test_sin_baseline_ni_fecha_la_firma_es_unica_por_escena(self, monkeypatch):
        """Sin baseline y sin fecha no hay forma de saber el lado del corte. La
        firma tiene que ser ÚNICA por escena para que dos indeterminadas no casen
        entre sí — es el fondo del mismo bloqueante."""
        a = self._con("S_A", None)
        a.datetime = ""
        b = self._con("S_B", None)
        b.datetime = ""
        assert engine.radiometry(a) != engine.radiometry(b)
        with pytest.raises(engine.ImageryError, match="no son comparables"):
            self._run(a, b, monkeypatch)

    def test_fechada_no_casa_con_declarada(self, monkeypatch):
        """El lado deducido de la FECHA es de menor confianza que el declarado por
        el baseline (un archivo REPROCESADO tiene fecha vieja y baseline nuevo),
        así que viven en espacios de nombres distintos y no casan en silencio."""
        a = self._con("S_FECHA", None)
        a.datetime = "2024-07-14T15:00:00Z"
        with pytest.raises(engine.ImageryError, match="no son comparables"):
            self._run(a, self._con("S_BASELINE", "05.11"), monkeypatch)

    def test_las_guardas_deciden_ANTES_de_leer_ningun_COG(self, monkeypatch):
        """Media 13: las guardas se resuelven con metadatos de la escena.
        Rechazar después de leer las cuatro bandas eran cuatro lecturas remotas
        tiradas (en esta red, entre 8 y 48 segundos)."""
        def _no_leer(*a, **k):
            raise AssertionError("no debió leer ninguna banda para rechazar")

        monkeypatch.setattr(engine, "_ndvi_window", _no_leer)
        prov = TestRunChange()._prov_secuencial(
            [self._con("S_2020", "02.14"), self._con("S_2024", "05.11")])
        with pytest.raises(engine.ImageryError, match="no son comparables"):
            engine.run_change(prov, self.AOI, "2020-06-10", "2024-06-10", 15,
                              Limits())

    def test_misma_escena_tambien_se_rechaza_sin_leer(self, monkeypatch):
        def _no_leer(*a, **k):
            raise AssertionError("no debió leer ninguna banda para rechazar")

        monkeypatch.setattr(engine, "_ndvi_window", _no_leer)
        s = self._con("S_DUP", "05.11", scaled=True)
        prov = TestRunChange()._prov_secuencial([s, s])
        with pytest.raises(engine.ImageryError, match="misma escena"):
            engine.run_change(prov, self.AOI, "2026-06-10", "2026-06-20", 15,
                              Limits())


# ---------------------------------------------------------------------------
# §1.3 de punta a punta, SIN red: COGs sintéticos en disco con DN post-04.00
# ---------------------------------------------------------------------------
def _escribir_banda(path: str, dn: np.ndarray) -> None:
    """GeoTIFF uint16 en EPSG:32618, 10 m, origen (500000, 460000)."""
    import rasterio
    from rasterio.transform import from_origin
    h, w = dn.shape
    prof = {"driver": "GTiff", "height": h, "width": w, "count": 1,
            "dtype": "uint16", "crs": "EPSG:32618", "nodata": 0,
            "transform": from_origin(500000, 460000, 10, 10)}
    with rasterio.open(path, "w", **prof) as ds:
        ds.write(dn.astype("uint16"), 1)


def _bandas_sinteticas(tmp_path) -> tuple[str, str]:
    """red/nir en DISCO con DN de baseline post-04.00 (offset incorporado).

    nir varía de 0,30 a 0,50 de reflectancia y red es fija en 0,08 → el NDVI
    verdadero va de 0.579 a 0.724. Sobre el DN crudo, de 0.379 a 0.538: el
    p75 cae por debajo de 0.55 y es lo que caza el assert con dientes.
    """
    n = 20
    red_ref = np.full((n, n), _RED_REF, "float64")
    nir_ref = np.tile(np.linspace(0.30, 0.50, n), (n, 1))
    # DN post-04.00 = reflectancia * 10000 + 1000 (BOA_ADD_OFFSET = -1000)
    red_p, nir_p = str(tmp_path / "red.tif"), str(tmp_path / "nir.tif")
    _escribir_banda(red_p, np.round(red_ref * 10000 + 1000))
    _escribir_banda(nir_p, np.round(nir_ref * 10000 + 1000))
    return red_p, nir_p


def _escena_sintetica(tmp_path, *, con_factor: bool):
    """Escena con el factor publicado en el asset, o sin factor NI baseline."""
    from imagery_mcp.providers import Scene
    red_p, nir_p = _bandas_sinteticas(tmp_path)
    return Scene(
        id="S2A_MSIL2A_SINTETICA", datetime="2024-06-11T15:26:31Z", cloud_pct=4.0,
        bbox=[-76.0, 3.0, -74.0, 5.0], red_href=red_p, nir_href=nir_p,
        provider="test", scl_href=None,
        scaling=({"red": _scaling(), "nir": _scaling()} if con_factor else {}),
        # Sin factor Y sin baseline: el único estado en que ya no hay nada que
        # derivar. Con baseline legible el escalón 3 lo corregiría.
        processing_baseline=("05.11" if con_factor else None))


def _escena_como_la_sirve_pc(tmp_path):
    """La escena tal y como la entrega Planetary Computer HOY, parseada por el
    proveedor real: el asset B04/B08 trae `href, proj:*, gsd, type, roles, title,
    eo:bands` y **ninguna** clave `raster:bands`; el item sí trae
    `s2:processing_baseline: 05.12`. (Forma verificada contra la API el
    2026-09-08; la colección tampoco lo declara en `item_assets`.)"""
    from imagery_mcp.providers import PlanetaryComputerProvider
    red_p, nir_p = _bandas_sinteticas(tmp_path)

    def _asset(href, banda, centro):
        return {"href": href, "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                "roles": ["data"], "title": f"{banda} - 10m", "gsd": 10,
                "proj:bbox": [499960.0, 449980.0, 609780.0, 559800.0],
                "proj:shape": [10980, 10980],
                "proj:transform": [10.0, 0.0, 499960.0, 0.0, -10.0, 559800.0],
                "eo:bands": [{"name": banda, "common_name": banda.lower(),
                              "center_wavelength": centro}]}

    return PlanetaryComputerProvider()._parse({
        "id": "S2A_MSIL2A_20240611T152631_R025_T18NXL_20240612T203744",
        "bbox": [-76.0, 3.0, -74.0, 5.0],
        "properties": {"datetime": "2024-06-11T15:26:31.024000Z",
                       "eo:cloud_cover": 4.2,
                       "s2:processing_baseline": "05.12"},
        "assets": {"B04": _asset(red_p, "B04", 0.665),
                   "B08": _asset(nir_p, "B08", 0.842)},
    }, coll_lookup=lambda: {})   # PC tampoco lo declara en la colección


def _aoi_sintetico():
    """AOI 4326 dentro del raster sintético (32618, x 500050–500150)."""
    from rasterio.warp import transform_bounds
    return engine.bbox_polygon(transform_bounds(
        "EPSG:32618", "EPSG:4326", 500050, 459850, 500150, 459950))


def _proj_ok() -> bool:
    from rasterio.crs import CRS
    from rasterio.errors import CRSError
    try:
        CRS.from_epsg(32618)
        return True
    except CRSError:
        return False


def test_run_ndvi_post_0400_no_sale_sesgado(tmp_path):
    """§1.3 de punta a punta: STAC fabricado → read_band_window → compute_ndvi.

    El assert con dientes de la auditoría. Sin el factor aplicado, el p75 de esta
    escena sintética es ~0.50 y este test falla con el mensaje puesto."""
    if not _proj_ok():
        pytest.skip("PROJ del entorno no resuelve EPSG (env, no código)")
    scene = _escena_sintetica(tmp_path, con_factor=True)

    class _Prov:
        def search(self, *a, **k): return [scene]
        def sign(self, h): return h

    result = engine.run_ndvi(_Prov(), _aoi_sintetico(), None, None, None, Limits())

    assert 0.55 < result["stats"]["p75"] < 0.90, \
        "NDVI bajo: ¿falta el BOA_ADD_OFFSET?"
    # El NDVI verdadero de este suelo va de 0.579 a 0.724 (el AOI recorta una
    # franja del raster, así que se acota el rango en vez de fijar los extremos).
    assert result["stats"]["mean"] == pytest.approx(0.6567, abs=0.02)
    assert 0.578 <= result["stats"]["min"] <= result["stats"]["max"] <= 0.725, \
        "el NDVI se salió del rango verdadero del suelo sintético"
    # …y el payload declara con qué se calculó.
    assert result["reflectance"]["aplicado"] is True
    assert result["reflectance"]["red"] == {"scale": _S2_SCALE, "offset": _S2_OFFSET}


def test_run_ndvi_con_la_escena_REAL_de_planetary_computer(tmp_path):
    """El camino POR DEFECTO del despliegue (`IMAGERY_PROVIDER` = PC).

    PC no publica `raster:bands` ni en el item ni en la colección — comprobado
    contra la API el 2026-09-08 —, así que sin el escalón 3 este NDVI sale ~0,20
    bajo aunque el item declare `s2:processing_baseline: 05.12`. El factor se
    deriva de la fórmula de ESA y el número sale correcto; el payload declara que
    es DERIVADO y no publicado."""
    if not _proj_ok():
        pytest.skip("PROJ del entorno no resuelve EPSG (env, no código)")
    scene = _escena_como_la_sirve_pc(tmp_path)
    assert scene.scaling_for("red").source == _SRC_BASELINE   # escalón 3, no 1 ni 2

    class _Prov:
        def search(self, *a, **k): return [scene]
        def sign(self, h): return h

    result = engine.run_ndvi(_Prov(), _aoi_sintetico(), None, None, None, Limits())

    assert 0.55 < result["stats"]["p75"] < 0.90, \
        "NDVI bajo: ¿falta el BOA_ADD_OFFSET?"
    assert result["stats"]["mean"] == pytest.approx(0.6567, abs=0.02)
    refl = result["reflectance"]
    assert refl["aplicado"] is True and refl["derivado"] is True
    assert refl["origen"] == _SRC_BASELINE
    assert refl["processing_baseline"] == "05.12"
    assert refl["red"] == {"scale": _S2_SCALE, "offset": _S2_OFFSET}
    assert "aviso" not in refl        # ya no hay sesgo que advertir


def test_run_ndvi_sin_factor_ni_baseline_declara_el_dn_crudo(tmp_path):
    """El único caso que queda irresoluble: sin `raster:bands` y sin baseline
    legible. El número sale sesgado —no hay de dónde sacar el offset— pero el
    payload lo DICE, que es la diferencia entre un dato malo y uno invisible."""
    if not _proj_ok():
        pytest.skip("PROJ del entorno no resuelve EPSG (env, no código)")
    scene = _escena_sintetica(tmp_path, con_factor=False)
    assert scene.processing_baseline is None

    class _Prov:
        def search(self, *a, **k): return [scene]
        def sign(self, h): return h

    result = engine.run_ndvi(_Prov(), _aoi_sintetico(), None, None, None, Limits())
    assert result["stats"]["p75"] < 0.55          # el sesgo, medido
    assert result["reflectance"]["aplicado"] is False
    assert result["reflectance"]["origen"] == _SRC_IDENTIDAD
    assert "aviso" in result["reflectance"]       # y declarado


def test_tesela_usa_la_misma_ruta_escalada_que_el_motor(tmp_path, monkeypatch):
    """§1.3: `tiles.py` tenía su propia cuenta sobre el DN crudo. Con el mismo
    par de DN post-04.00, la tesela tiene que dar el NDVI verdadero (0.6667), no
    el sesgado (0.4706)."""
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.providers import Scene
    from imagery_mcp.tiles import _TILESIZE, TilePool

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    def _band(dn):
        t = MagicMock()
        t.data = np.full((1, _TILESIZE, _TILESIZE), float(dn), "float32")
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, "uint8")
        return t

    def _reader(href):
        r = MagicMock()
        r.tile.return_value = _band(_DN_POST[1] if "nir" in href else _DN_POST[0])
        return r

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    pool.register_scene(Scene(
        id="S2A_ESCALADA", datetime="2024-06-11T15:00:00Z", cloud_pct=4.0,
        bbox=[-76, 3, -74, 5], red_href="mem://red", nir_href="mem://nir",
        provider="test", scaling={"red": _scaling(), "nir": _scaling()},
        processing_baseline="05.11"))

    with patch("rio_tiler.io.Reader", side_effect=_reader):
        ndvi, valid, _esc = pool._read_scene_ndvi("S2A_ESCALADA", 13, 1, 1)

    assert valid.all()
    assert float(ndvi[0, 0]) == pytest.approx(_NDVI_VERDADERO, abs=1e-4), \
        "la tesela sigue calculando sobre el DN crudo"
    assert float(ndvi[0, 0]) != pytest.approx(_NDVI_SESGADO, abs=1e-3)


def test_tesela_sin_factor_conserva_el_dn_crudo(tmp_path, monkeypatch):
    """Escena sin factor publicado: la tesela NO debe inventar uno (y el nodata
    del recorte sigue siendo el 0 literal, que es lo que caza la máscara #13)."""
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.tiles import _TILESIZE, TilePool

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    def _band(dn, con_nodata=False):
        t = MagicMock()
        arr = np.full((1, _TILESIZE, _TILESIZE), float(dn), "float32")
        if con_nodata:
            arr[0, 0, 0] = 0.0
        t.data = arr
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, "uint8")
        return t

    def _reader(href):
        r = MagicMock()
        r.tile.return_value = (_band(_DN_PRE[1]) if "nir" in href
                               else _band(_DN_PRE[0], con_nodata=True))
        return r

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    pool.register_scene(_scene([-76, 3, -74, 5], sid="S2A_CRUDA"))

    with patch("rio_tiler.io.Reader", side_effect=_reader):
        ndvi, valid, _esc = pool._read_scene_ndvi("S2A_CRUDA", 13, 1, 1)

    assert float(ndvi[1, 1]) == pytest.approx(_NDVI_VERDADERO, abs=1e-4)
    assert not valid[0, 0]      # el 0 sigue siendo nodata, no un DN válido
    assert valid[1, 1]


# ---------------------------------------------------------------------------
# BLOQUEANTE 1 (revisión adversa 2026-09-08): el NDVI perdió su cota
#
# `den > 0` bastaba mientras la entrada era DN (uint16, siempre >= 0). Con
# reflectancia, el BOA_ADD_OFFSET produce valores NEGATIVOS —para eso existe— y
# el cociente se desmadra SIN salirse de lo finito, así que pasa `isfinite` y
# entra entero en las estadísticas, en la rampa de las teselas y en el zonal.
# ---------------------------------------------------------------------------
_DN_AGUA = (960, 1060)      # refl(-0.0040, +0.0060) → NDVI sin guarda = 5.0000076
_DN_AGUA_2 = (900, 1200)    # refl(-0.0100, +0.0200) → NDVI sin guarda = 3.000003
_DN_AGUA_3 = (800, 900)     # ambas negativas → den < 0 → NaN ya antes, en silencio


class TestCotaDelNdviConReflectancia:
    def test_agua_oscura_ya_no_produce_un_ndvi_de_5(self):
        """El número exacto del revisor. Sin la guarda de positividad esto vale
        5.0000076: finito, dentro de `den > 0`, y contamina max/mean/std."""
        sc = _scaling()
        # La aritmética cruda que producía el 5,0 (documentada aquí, no ejecutada
        # por el motor).
        r = _DN_AGUA[0] * _S2_SCALE + _S2_OFFSET
        n = _DN_AGUA[1] * _S2_SCALE + _S2_OFFSET
        assert (n - r) / (n + r) == pytest.approx(5.0, abs=1e-4)   # el desmadre
        # Y lo que devuelve el motor ahora:
        assert np.isnan(_ndvi_de(*_DN_AGUA, scaling=sc))
        assert np.isnan(_ndvi_de(*_DN_AGUA_2, scaling=sc))
        assert np.isnan(_ndvi_de(*_DN_AGUA_3, scaling=sc))

    def test_la_vegetacion_no_se_toca(self):
        # La guarda solo descarta reflectancia <= 0: el suelo con dato sigue igual.
        assert _ndvi_de(*_DN_POST, scaling=_scaling()) == \
            pytest.approx(_NDVI_VERDADERO, abs=1e-4)

    def test_sobre_DN_CRUDO_la_guarda_es_equivalente_a_la_anterior(self):
        """Un DN >= 0 solo falla `> 0` si vale 0, que ya era el nodata del #13.
        Así que los productos sin factor no mueven ni un número."""
        red = np.array([[100.0, 0.0], [300.0, 100.0]], dtype="float32")
        nir = np.array([[300.0, 0.0], [100.0, 100.0]], dtype="float32")
        ndvi, no_pos = engine.compute_ndvi_detallado(red, nir)
        assert ndvi[0, 0] == pytest.approx(0.5)
        assert np.isnan(ndvi[0, 1])
        assert ndvi[1, 0] == pytest.approx(-0.5)
        assert ndvi[1, 1] == pytest.approx(0.0)
        assert no_pos == 0, "sobre DN crudo no hay reflectancia negativa que contar"

    def test_cuenta_lo_que_descarta(self):
        """La política del repo es declarar lo que se filtró: sin el recuento, el
        agua se va al mismo NaN que el nodata y que la nube."""
        sc = _scaling()
        dn_red = np.array([[_DN_POST[0], _DN_AGUA[0], 0.0]], dtype="float32")
        dn_nir = np.array([[_DN_POST[1], _DN_AGUA[1], 0.0]], dtype="float32")
        red = engine.apply_scaling(dn_red, sc)
        nir = engine.apply_scaling(dn_nir, sc)
        ndvi, no_pos = engine.compute_ndvi_detallado(red, nir)
        assert not np.isnan(ndvi[0, 0])          # vegetación: vale
        assert np.isnan(ndvi[0, 1]) and np.isnan(ndvi[0, 2])
        assert no_pos == 1, "el nodata (0) NO cuenta como reflectancia no positiva"

    def test_compute_ndvi_sigue_devolviendo_solo_el_array(self):
        # Compatibilidad: los callers viejos no cambian de forma.
        out = engine.compute_ndvi(np.array([[100.0]], "float32"),
                                  np.array([[300.0]], "float32"))
        assert isinstance(out, np.ndarray) and out[0, 0] == pytest.approx(0.5)


def _bandas_con_agua(tmp_path) -> tuple[str, str]:
    """Como `_bandas_sinteticas` pero con una franja de AGUA (DN < 1000, o sea
    reflectancia negativa tras el offset) en las columnas 6-9, que caen DENTRO de
    la ventana que recorta `_aoi_sintetico` (columnas 5..15)."""
    n = 20
    red = np.full((n, n), _RED_REF * 10000 + 1000, "float64")
    nir = np.tile(np.linspace(0.30, 0.50, n) * 10000 + 1000, (n, 1))
    red[:, 6:10] = _DN_AGUA[0]
    nir[:, 6:10] = _DN_AGUA[1]
    red_p, nir_p = str(tmp_path / "red_agua.tif"), str(tmp_path / "nir_agua.tif")
    _escribir_banda(red_p, np.round(red))
    _escribir_banda(nir_p, np.round(nir))
    return red_p, nir_p


def test_run_ndvi_con_agua_no_desmadra_stats_ni_rampa(tmp_path):
    """De punta a punta. Sin la guarda el `max` sale 5,0, el `p98` se lo lleva y
    la rampa de las teselas se estira a [p2, 5.0] → el mapa NDVI sale lavado."""
    if not _proj_ok():
        pytest.skip("PROJ del entorno no resuelve EPSG (env, no código)")
    from imagery_mcp.providers import Scene
    red_p, nir_p = _bandas_con_agua(tmp_path)
    scene = Scene(id="S2A_AGUA", datetime="2024-06-11T15:26:31Z", cloud_pct=4.0,
                  bbox=[-76.0, 3.0, -74.0, 5.0], red_href=red_p, nir_href=nir_p,
                  provider="test", scl_href=None,
                  scaling={"red": _scaling(), "nir": _scaling()},
                  processing_baseline="05.12")

    class _Prov:
        def search(self, *a, **k): return [scene]
        def sign(self, h): return h

    out = engine.run_ndvi(_Prov(), _aoi_sintetico(), None, None, None, Limits())
    st = out["stats"]
    assert -1.0 <= st["min"] <= st["max"] <= 1.0, \
        "el NDVI se salió de [-1, 1]: ¿falta la guarda de reflectancia positiva?"
    assert st["p98"] <= 1.0 and out["tiles"]["rescale"][1] <= 1.0, \
        "un NDVI fuera de rango estiró la rampa de las teselas"
    # Y el agua queda DECLARADA, no disuelta en el mismo NaN que el nodata.
    assert out["descartes"]["px_reflectancia_no_positiva"] > 0
    assert "nota" in out["descartes"]
    assert st["px_validos"] < st["px_total"]


def test_tesela_de_agua_queda_transparente_y_no_lavada(tmp_path, monkeypatch):
    """La misma guarda en el teselado: el agua sale con alpha 0, no como un píxel
    de NDVI 5,0 saturando la rampa."""
    from unittest.mock import MagicMock, patch

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.providers import Scene
    from imagery_mcp.tiles import _TILESIZE, TilePool

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    def _reader(href):
        arr = np.full((1, _TILESIZE, _TILESIZE),
                      float(_DN_POST[1] if "nir" in href else _DN_POST[0]), "float32")
        arr[0, :, : _TILESIZE // 2] = float(
            _DN_AGUA[1] if "nir" in href else _DN_AGUA[0])   # mitad izq. = agua
        t = MagicMock()
        t.data = arr
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, "uint8")
        r = MagicMock()
        r.tile.return_value = t
        return r

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    pool.register_scene(Scene(
        id="S2A_AGUATILE", datetime="2024-06-11T15:00:00Z", cloud_pct=4.0,
        bbox=[-76, 3, -74, 5], red_href="mem://red", nir_href="mem://nir",
        provider="test", scaling={"red": _scaling(), "nir": _scaling()},
        processing_baseline="05.12"))

    with patch("rio_tiler.io.Reader", side_effect=_reader):
        ndvi, valid, _e = pool._read_scene_ndvi("S2A_AGUATILE", 13, 1, 1)

    assert not valid[:, : _TILESIZE // 2].any(), "el agua debe quedar inválida"
    assert valid[:, _TILESIZE // 2:].all()
    finitos = ndvi[np.isfinite(ndvi)]
    assert finitos.max() <= 1.0 and finitos.min() >= -1.0


# ---------------------------------------------------------------------------
# MEDIA 9: una escena, una unidad
# ---------------------------------------------------------------------------
def test_escena_que_mezcla_unidades_entre_bandas_es_error():
    """red con factor y nir sin él: el cociente cruzaría reflectancia (~0,08) con
    DN (~4000). El payload lo declaraba (`origen: mixto`) pero nada lo paraba."""
    s = _scene([-76, 3, -74, 5], sid="S_MIXTA")
    s.scaling = {"red": _scaling()}          # nir se queda en identidad
    assert engine._origen(s) == "mixto"
    with pytest.raises(engine.ImageryError, match="mezcla unidades"):
        engine.check_unidades_coherentes(s)


def test_run_ndvi_rechaza_la_escena_mixta_sin_leer_bandas():
    s = _scene([-75, 4, -73, 5], sid="S_MIXTA2")
    s.scaling = {"nir": _scaling()}          # ahora la que falta es red

    class _Prov:
        def search(self, *a, **k): return [s]
        def sign(self, h): raise AssertionError("no debió firmar ninguna banda")

    with pytest.raises(engine.ImageryError, match="mezcla unidades"):
        engine.run_ndvi(_Prov(), engine.bbox_polygon((-74.12, 4.60, -74.08, 4.64)),
                        None, None, None, Limits())


# ---------------------------------------------------------------------------
# MEDIA 6: /tiles-diff tenía que tener la misma guarda que run_change
# ---------------------------------------------------------------------------
def _pool_con_dos_escenas(tmp_path, monkeypatch, a_kwargs, b_kwargs):
    from unittest.mock import MagicMock

    import imagery_mcp.tiles as tiles_mod
    from imagery_mcp.providers import Scene
    from imagery_mcp.tiles import _TILESIZE, TilePool

    monkeypatch.setattr(tiles_mod, "_DISK_CACHE_DIR", str(tmp_path))

    def _reader(href):
        t = MagicMock()
        t.data = np.full((1, _TILESIZE, _TILESIZE),
                         float(_DN_POST[1] if "nir" in href else _DN_POST[0]),
                         "float32")
        t.mask = np.full((_TILESIZE, _TILESIZE), 255, "uint8")
        r = MagicMock()
        r.tile.return_value = t
        return r

    provider = MagicMock()
    provider.sign.side_effect = lambda h: h
    pool = TilePool(provider)
    for sid, kw in (("S2A_ESCUNO", a_kwargs), ("S2B_ESCDOS", b_kwargs)):
        pool.register_scene(Scene(
            id=sid, cloud_pct=4.0, bbox=[-76, 3, -74, 5],
            red_href=f"mem://{sid}_red", nir_href=f"mem://{sid}_nir",
            provider="test", **kw))
    return pool, _reader


def test_tiles_diff_rechaza_escenas_incompatibles(tmp_path, monkeypatch):
    """`_read_scene_ndvi` resuelve CUALQUIER id contra el STAC, así que un
    GET /tiles-diff/{2020}/{2024}/... a mano —o una plantilla de un run_change
    viejo servida del caché en disco tras un redeploy— pintaba el mapa de cambio
    sin pasar por ninguna comprobación."""
    from unittest.mock import patch

    from imagery_mcp.tiles import EscenasIncompatiblesError

    pool, _reader = _pool_con_dos_escenas(
        tmp_path, monkeypatch,
        {"datetime": "2020-07-14T15:00:00Z", "processing_baseline": None},
        {"datetime": "2024-07-14T15:00:00Z", "processing_baseline": None})
    with patch("rio_tiler.io.Reader", side_effect=_reader), \
            pytest.raises(EscenasIncompatiblesError, match="no son comparables"):
        pool.render_diff_tile("S2A_ESCUNO", "S2B_ESCDOS", 13, 1, 1)


def test_tiles_diff_sigue_sirviendo_las_compatibles(tmp_path, monkeypatch):
    from unittest.mock import patch

    pool, _reader = _pool_con_dos_escenas(
        tmp_path, monkeypatch,
        {"datetime": "2024-03-01T15:00:00Z", "processing_baseline": "05.12",
         "scaling": {"red": _scaling(), "nir": _scaling()}},
        {"datetime": "2024-09-01T15:00:00Z", "processing_baseline": "05.12",
         "scaling": {"red": _scaling(), "nir": _scaling()}})
    with patch("rio_tiler.io.Reader", side_effect=_reader):
        png = pool.render_diff_tile("S2A_ESCUNO", "S2B_ESCDOS", 13, 1, 1)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_endpoint_tiles_diff_incompatibles_da_409(tmp_path, monkeypatch):
    """409, no 502 ni PNG: la petición es válida, el cálculo que pide es falso."""
    from unittest.mock import patch

    import httpx
    from imagery_mcp.server import AuthMiddleware

    pool, _reader = _pool_con_dos_escenas(
        tmp_path, monkeypatch,
        {"datetime": "2020-07-14T15:00:00Z", "processing_baseline": None},
        {"datetime": "2024-07-14T15:00:00Z", "processing_baseline": None})

    async def never(scope, receive, send):
        raise AssertionError("no debió llegar al app MCP")

    app = AuthMiddleware(never, _ring(), RateLimiter(), tile_pool=pool)
    with patch("rio_tiler.io.Reader", side_effect=_reader):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t",
        ) as c:
            r = await c.get("/tiles-diff/S2A_ESCUNO/S2B_ESCDOS/13/1/1.png",
                            headers={"Authorization": "Bearer secreto-app"})
    assert r.status_code == 409
    assert "no son comparables" in r.json()["error"]


# ---------------------------------------------------------------------------
# MEDIA 10: distinguir "consultado, no lo declara" de "no se pudo consultar"
# ---------------------------------------------------------------------------
class TestCacheDelEscalonColeccion:
    def test_un_503_transitorio_no_degrada_para_siempre(self, monkeypatch):
        """Cachear el {} de un fallo de red dejaba el escalón 2 muerto durante
        toda la vida del proceso, y en silencio."""
        import imagery_mcp.providers as pv
        intentos = {"n": 0}

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                banda = {"raster:bands": [
                    {"scale": _S2_SCALE, "offset": _S2_OFFSET}]}
                return {"item_assets": {"B04": banda, "B08": banda}}

        def _get(*a, **k):
            intentos["n"] += 1
            if intentos["n"] == 1:
                raise pv.httpx.ConnectError("503 transitorio")
            return _Resp()

        monkeypatch.setattr(pv.httpx, "get", _get)
        prov = pv.PlanetaryComputerProvider()
        assert prov._collection_scaling() == {}      # falló: se degrada…
        assert prov._coll_scaling is None            # …pero NO se cachea
        segunda = prov._collection_scaling()         # se reintenta
        assert segunda["red"].source == _SRC_COLECCION
        assert intentos["n"] == 2

    def test_un_vacio_concluyente_si_se_cachea(self, monkeypatch):
        """El caso REAL de PC: la colección responde y no declara el factor. Eso
        sí se recuerda — no tiene sentido re-preguntar en cada búsqueda."""
        import imagery_mcp.providers as pv
        intentos = {"n": 0}

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                intentos["n"] += 1
                return {"item_assets": {"B04": {"gsd": 10, "roles": ["data"]}}}

        monkeypatch.setattr(pv.httpx, "get", lambda *a, **k: _Resp())
        prov = pv.PlanetaryComputerProvider()
        assert prov._collection_scaling() == {}
        assert prov._collection_scaling() == {}
        assert intentos["n"] == 1, "un vacío concluyente no se re-consulta"

    def test_el_fallo_se_registra(self, monkeypatch, caplog):
        import logging

        import imagery_mcp.providers as pv

        def _boom(*a, **k):
            raise pv.httpx.ConnectError("sin ruta al host")

        monkeypatch.setattr(pv.httpx, "get", _boom)
        with caplog.at_level(logging.WARNING, logger="imagery_mcp"):
            pv.EarthSearchProvider()._collection_scaling()
        assert any("colección STAC" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# REGISTRO: la respuesta REAL que respalda "PC no publica raster:bands"
#
# Hasta ahora eso era una afirmación en un comentario. Aquí queda pegado el
# recorte de las dos respuestas, con fecha y endpoint, para que el día que
# Planetary Computer empiece a publicar el factor un test lo diga en vez de que
# el escalón 3 siga derivando en silencio algo que ya viene publicado.
#
# Consultado el 2026-09-08 contra:
#   GET https://planetarycomputer.microsoft.com/api/stac/v1/search
#       (collections=sentinel-2-l2a)                      → asset B04 del item
#   GET https://planetarycomputer.microsoft.com/api/stac/v1/collections/
#       sentinel-2-l2a                                    → item_assets.B04
#   GET https://earth-search.aws.element84.com/v1/search  → asset red del item
# ---------------------------------------------------------------------------

# Claves EXACTAS del asset B04 de un item de PC (mismo item cuyo
# `s2:processing_baseline` es "05.12"). No hay `raster:bands`.
_PC_ITEM_B04_CLAVES_2026_09_08 = [
    "href", "type", "roles", "title", "gsd",
    "proj:bbox", "proj:shape", "proj:transform", "eo:bands",
]

# Claves EXACTAS de `item_assets.B04` en la colección de PC. Tampoco.
_PC_COLECCION_B04_CLAVES_2026_09_08 = ["gsd", "type", "roles", "title", "eo:bands"]

# Earth Search, mismo `s2:processing_baseline`, SÍ lo publica:
_ES_ITEM_RED_RASTER_BANDS_2026_09_08 = [
    {"scale": 0.0001, "offset": -0.1, "nodata": 0, "data_type": "uint16"},
]


def test_registro_pc_no_publica_raster_bands_2026_09_08():
    """Fija la evidencia: ni el item ni la colección de PC traen el factor.

    Si algún día lo publican, este test seguirá pasando (solo describe lo que se
    observó) pero `test_si_pc_empieza_a_publicarlo_gana_el_escalon_1` demuestra
    que el código lo preferiría automáticamente."""
    from imagery_mcp.providers import _asset_scaling
    assert "raster:bands" not in _PC_ITEM_B04_CLAVES_2026_09_08
    assert "raster:bands" not in _PC_COLECCION_B04_CLAVES_2026_09_08
    assert not any(k.startswith("raster:") for k in _PC_ITEM_B04_CLAVES_2026_09_08)
    # Reconstruido tal cual: `_asset_scaling` no encuentra factor → escalón 3.
    asset_pc = dict.fromkeys(_PC_ITEM_B04_CLAVES_2026_09_08, "x")
    assert _asset_scaling(asset_pc) is None
    # Y el mismo item en Earth Search sí lo trae.
    asset_es = {"href": "s3://x", "raster:bands": _ES_ITEM_RED_RASTER_BANDS_2026_09_08}
    sc = _asset_scaling(asset_es)
    assert (sc.scale, sc.offset) == (_S2_SCALE, _S2_OFFSET)


def test_si_pc_empieza_a_publicarlo_gana_el_escalon_1():
    """La derivación es un sustituto, no una preferencia: en cuanto el asset
    traiga `raster:bands`, ese dato manda sobre nuestra inferencia."""
    from imagery_mcp.providers import PlanetaryComputerProvider
    asset = dict.fromkeys(_PC_ITEM_B04_CLAVES_2026_09_08, "x")
    asset["href"] = "https://x/B04.tif"
    asset["raster:bands"] = _ES_ITEM_RED_RASTER_BANDS_2026_09_08
    scene = PlanetaryComputerProvider()._parse({
        "id": "S2A_PC_FUTURO", "bbox": [-76, 3, -74, 5],
        "properties": {"datetime": "2024-06-11T15:26:31Z", "eo:cloud_cover": 4.0,
                       "s2:processing_baseline": "05.12"},
        "assets": {"B04": asset, "B08": dict(asset, href="https://x/B08.tif")},
    }, coll_lookup=lambda: {})
    assert scene.scaling_for("red").source == _SRC_ASSET   # publicado, no derivado
    assert engine.reflectance_payload(scene)["derivado"] is False


# ---------------------------------------------------------------------------
# S0.6 (F0): el tiempo máximo por tool se aplica
# ---------------------------------------------------------------------------
def test_tool_que_se_cuelga_devuelve_error_por_tiempo():
    import threading

    import imagery_mcp.server as srv

    liberar = threading.Event()

    def colgada():
        liberar.wait(5)  # simula una lectura COG que no vuelve
        return {"ok": True}

    try:
        out = srv._wrap(colgada, timeout_s=0.2)
    finally:
        liberar.set()  # no dejar el hilo del pool bloqueado para otros tests
    assert "tiempo máximo" in out["error"]
    assert "Traceback" not in out["error"]


def test_tool_rapida_no_se_ve_afectada():
    import imagery_mcp.server as srv

    assert srv._wrap(lambda x: {"doble": x * 2}, 21) == {"doble": 42}


def test_timeout_por_defecto_sale_de_limits():
    import inspect

    import imagery_mcp.server as srv

    assert "settings.limits.tool_timeout_s" in inspect.getsource(srv._wrap)


# ---------------------------------------------------------------------------
# V5 F3: rango de fechas — validado y sin descartar un extremo en silencio
# ---------------------------------------------------------------------------
class TestRangoFechas:
    def _lim(self):
        return SimpleNamespace(default_lookback_days=45)

    def test_fecha_inexistente_es_error_legible_no_un_400_de_stac(self):
        with pytest.raises(engine.ImageryError, match="2026-02-29"):
            engine.rango_fechas("2026-01-01", "2026-02-29", self._lim())

    def test_solo_desde_no_se_ignora(self):
        d0, d1 = engine.rango_fechas("2026-01-01", None, self._lim())
        assert d0 == "2026-01-01" and d1 >= "2026-01-01"

    def test_solo_hasta_toma_la_ventana_por_defecto_hacia_atras(self):
        assert engine.rango_fechas(None, "2026-03-01", self._lim()) == ("2026-01-15", "2026-03-01")

    def test_rango_invertido_es_error(self):
        with pytest.raises(engine.ImageryError, match="posterior"):
            engine.rango_fechas("2026-03-01", "2026-01-01", self._lim())

    def test_sin_fechas_es_la_ventana_reciente(self):
        d0, d1 = engine.rango_fechas(None, None, self._lim())
        from datetime import date
        assert (date.fromisoformat(d1) - date.fromisoformat(d0)).days == 45

    def test_cambio_con_fecha_inexistente_es_error_legible(self):
        from imagery_mcp.config import Limits

        aoi = {"type": "Polygon", "coordinates": [[[-74.1, 4.6], [-74.09, 4.6], [-74.09, 4.61], [-74.1, 4.6]]]}
        with pytest.raises(engine.ImageryError, match="2026-02-30"):
            engine.run_change(None, aoi, "2026-02-30", "2026-06-15", 15, Limits())


def test_el_servicio_solo_importa_lo_que_declara_su_contenedor():
    """V5 F3: el motor importó shapely (está en el entorno de desarrollo, no en la
    imagen de imagery-mcp) y el NDVI por lote falló en producción con
    ModuleNotFoundError mientras los tests pasaban."""
    import ast
    import re

    raiz = Path(__file__).parent.parent / "services" / "imagery_mcp"
    declarados = {
        re.split(r"[<>=!\[ ]", linea.strip())[0].lower().replace("-", "_")
        for linea in (raiz / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if linea.strip() and not linea.startswith("#")
    }
    permitidos = declarados | set(sys.stdlib_module_names) | {"imagery_mcp", "geo_mcp_kit", "pil"}
    usados = set()
    for py in (raiz / "imagery_mcp").glob("*.py"):
        for nodo in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(nodo, ast.Import):
                usados |= {(a.name.split(".")[0], py.name) for a in nodo.names}
            elif isinstance(nodo, ast.ImportFrom) and nodo.module and not nodo.level:
                usados.add((nodo.module.split(".")[0], py.name))
    faltan = sorted(f"{m} ({f})" for m, f in usados if m.lower() not in permitidos)
    assert not faltan, f"importados sin declarar en requirements.txt: {faltan}"


def test_run_ndvi_en_un_punto_lee_el_pixel_que_lo_contiene(tmp_path):
    """V5 F4 (E4.5): «¿qué valor tiene el NDVI aquí?» con aoi = el punto marcado.

    Un punto tiene bbox de área cero: la ventana redondeada al píxel salía 0×0 y
    el servicio respondía «La ventana del AOI queda fuera de la escena», falso —
    el punto estaba dentro — y el agente se lo contó así al usuario."""
    if not _proj_ok():
        pytest.skip("PROJ del entorno no resuelve EPSG (env, no código)")
    from rasterio.warp import transform

    scene = _escena_sintetica(tmp_path, con_factor=True)

    class _Prov:
        def search(self, *a, **k): return [scene]
        def sign(self, h): return h

    xs, ys = transform("EPSG:32618", "EPSG:4326", [500100], [459900])
    punto = {"type": "Point", "coordinates": [xs[0], ys[0]]}
    result = engine.run_ndvi(_Prov(), punto, None, None, None, Limits())

    assert result["stats"]["count"] >= 1 if "count" in result["stats"] else True
    assert 0.578 <= result["stats"]["mean"] <= 0.725   # el NDVI de ese suelo, no un error


def test_un_aoi_fuera_de_la_escena_sigue_siendo_un_error_honesto(tmp_path):
    if not _proj_ok():
        pytest.skip("PROJ del entorno no resuelve EPSG (env, no código)")
    scene = _escena_sintetica(tmp_path, con_factor=True)
    # la ventana ya no se queda en 0×0 para un punto, pero lo que NO toca la escena
    # sigue siendo «fuera de la escena» (no se lee un píxel inventado)
    with pytest.raises(engine.ImageryError, match="fuera de la escena"):
        engine.read_band_window(scene.red_href, (10.0, 10.0, 10.001, 10.001), retries=1)


def test_sin_pixeles_validos_dice_por_que():
    """V5 F4: el NDVI del punto marcado salió «¿todo nodata?» — el agente no podía
    explicar si era nube, agua/sombra o falta de dato, ni proponer otra fecha con criterio."""
    import numpy as np

    win = engine.BandWindow(data=np.full((1, 1), np.nan, dtype="float32"), transform=None, crs=None,
                            bounds4326=(0, 0, 0, 0), cloud_pct_masked=100.0, px_no_positivos=0)
    escena = type("S", (), {"datetime": "2026-08-10T15:26:49Z"})()
    msg = engine.motivo_sin_validos(win, escena)
    assert "1 px" in msg and "nube" in msg and "2026-08-10" in msg and "otra fecha" in msg
    agua = engine.BandWindow(data=np.full((2, 2), np.nan, dtype="float32"), transform=None, crs=None,
                             bounds4326=(0, 0, 0, 0), cloud_pct_masked=0.0, px_no_positivos=4)
    assert "reflectancia ≤ 0" in engine.motivo_sin_validos(agua) and "nube" not in engine.motivo_sin_validos(agua)
