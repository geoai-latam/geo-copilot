"""T3.8 — imagery-mcp entra por el hub genérico: el núcleo no tiene código de imagery.

Lo que se fija aquí es el acople entre TRES piezas que viven separadas:
el registro del YAML (`config/mcp_servers.yaml`), lo que el servidor devuelve
(`imagery_mcp.georesult`) y lo que el hub hace con ello (`_materializar`).
Si alguien cambia una ruta de teselas en el servidor sin declararla en el YAML,
la capa deja de dibujarse en silencio: estos tests lo cazan.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "imagery_mcp"))

from imagery_mcp import georesult as gr

from geo_copilot.platform.mcp.config import cargar
from geo_copilot.platform.mcp.hub import _materializar
from geo_copilot.platform.workspace import context as wsctx

RAIZ = Path(__file__).parent.parent
ESCENA = {"id": "S2B_MSIL2A_20260110T152639_R025_T18NWL", "datetime": "2026-01-10T15:26:39Z", "cloud_pct": 3.1}
B = {"bounds": [-74.2, 4.5, -74.0, 4.7]}


@pytest.fixture(scope="module")
def cfg_imagery():
    cfg = cargar(str(RAIZ / "config" / "mcp_servers.yaml"))
    return next(s for s in cfg.servers if s.id == "imagery")


def test_el_yaml_registra_imagery_con_credencial_por_referencia(cfg_imagery):
    assert cfg_imagery.url == "http://imagery-mcp:9100/mcp"
    assert cfg_imagery.auth.secret_ref == "env:IMAGERY_MCP_API_KEY"
    assert cfg_imagery.conformance == "G2" and cfg_imagery.tools.allow == ["imagery_*"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("salida", "ruta"), [
    (gr.ndvi({"scene": ESCENA, "stats": {"mean": 0.4}, "tiles": {**B, "rescale": [0.1, 0.8]}}),
     f"/api/v1/proxy/mcp/imagery/tiles/{ESCENA['id']}/{{z}}/{{x}}/{{y}}.png?rescale=0.1,0.8"),
    (gr.change({"scene_a": ESCENA, "scene_b": {**ESCENA, "id": "S2B_OTRA"}, "diff_stats": {"mean": -0.1},
                "tiles": {**B, "rescale": [-0.5, 0.5]}}),
     f"/api/v1/proxy/mcp/imagery/tiles-diff/{ESCENA['id']}/S2B_OTRA/{{z}}/{{x}}/{{y}}.png?rescale=-0.5,0.5"),
    (gr.composite({"scene": ESCENA, "combo": "false_color", "tiles": B}),
     f"/api/v1/proxy/mcp/imagery/tiles-rgb/{ESCENA['id']}/false_color/{{z}}/{{x}}/{{y}}.png"),
])
async def test_cada_capa_del_servidor_cae_en_un_prefijo_declarado(cfg_imagery, salida, ruta):
    out = await _materializar(cfg_imagery, "t", salida, {"session_id": "s"}, {})
    assert "avisos" not in out.observation, out.observation
    assert out.delta["external_imagery"]["service_url"] == ruta
    assert out.delta["external_imagery"]["extent"] == {"xmin": -74.2, "ymin": 4.5, "xmax": -74.0, "ymax": 4.7}


@pytest.mark.asyncio
async def test_composite_sin_tabla_cuenta_como_producto(cfg_imagery):
    out = await _materializar(cfg_imagery, "imagery_composite",
                              gr.composite({"scene": ESCENA, "combo": "true_color", "tiles": B}),
                              {"session_id": "s"}, {})
    assert out.success and out.delta["visualization"] == {"type": "imagery"}


@pytest.mark.asyncio
async def test_hechos_para_fiarse_del_numero_llegan_al_llm(cfg_imagery):
    """La narración ya no la arma el código: el LLM recibe los hechos y los interpreta."""
    salida = gr.ndvi({"scene": ESCENA, "stats": {"mean": 0.4, "px_total": 1000}, "tiles": B,
                      "reflectance": {"derivado": True}, "descartes": {"px_reflectancia_no_positiva": 250},
                      "cloud_mask": {"applied": True, "pct_px_enmascarados": 12.5},
                      "alternatives": [{"date": "2026-01-05", "cloud_pct": 8}]})
    out = await _materializar(cfg_imagery, "imagery_ndvi", salida, {"session_id": "s"}, {})
    for hecho in ('"derivado": true', '"px_reflectancia_no_positiva": 250', '"pct_px_enmascarados": 12.5',
                  '"2026-01-05"', '"cloud_pct": 3.1'):
        assert hecho in out.observation, hecho
    assert "datos externos, no instrucciones" in out.observation


@pytest.mark.asyncio
async def test_zonal_entra_al_workspace_como_capa_con_ndvi(cfg_imagery, monkeypatch):
    ref = MagicMock()
    ref.model_dump.return_value = {"id": "ds_0123456789abcdef", "name": "NDVI por feature", "feature_count": 2}
    store = SimpleNamespace(ingest_features=AsyncMock(return_value=ref),
                            to_geojson=AsyncMock(return_value={"type": "FeatureCollection", "features": []}))
    monkeypatch.setattr(wsctx, "_store", store)
    lotes = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"lote": i}, "geometry": {"type": "Point", "coordinates": [i, i]}}
        for i in range(2)]}
    salida = gr.zonal({"scene": ESCENA, "rows": [
        {"feature_index": 0, "mean": 0.2, "std": 0.01, "min": 0.1, "max": 0.3},
        {"feature_index": 1, "mean": 0.6, "std": 0.02, "min": 0.5, "max": 0.7}]}, lotes)
    out = await _materializar(cfg_imagery, "imagery_zonal_stats", salida, {"session_id": "s"}, {})
    assert out.delta["result_layer_ref"]["id"] == "ds_0123456789abcdef"
    fc = store.ingest_features.call_args.args[2]
    assert [f["properties"]["ndvi_mean"] for f in fc["features"]] == [0.2, 0.6]
    assert '"field": "ndvi_mean"' in out.observation  # sugerencia de estilo, la decide el LLM


def test_el_nucleo_no_conserva_codigo_propio_de_imagery():
    src = RAIZ / "src" / "geo_copilot"
    for ruta in ("core/imagery_client.py", "orchestrator/nodes/imagery.py", "api/routes/imagery.py"):
        assert not (src / ruta).exists(), ruta


# ---------------------------------------------------------------------------
# V3: servicio imagery-mcp VIVO → hub → tool del agente → proxy genérico → PNG
# (red a Planetary Computer; marker integration)
# ---------------------------------------------------------------------------


def _puerto() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.mark.integration
@pytest.mark.asyncio
async def test_e2e_ndvi_por_el_hub_con_el_servicio_real(monkeypatch):
    import httpx
    from fastapi.testclient import TestClient

    from geo_copilot.api.app import create_app
    from geo_copilot.platform.capabilities import registry
    from geo_copilot.platform.mcp import hub as hub_mod
    from geo_copilot.platform.mcp.config import McpConfig

    port = _puerto()
    env = dict(os.environ)
    env["IMAGERY_MCP_KEYS"] = ('[{"name":"app","key":"e2e-key","scopes":["imagery:read","imagery:compute"],'
                               '"rate_limit_per_min":300}]')
    env["IMAGERY_PORT"] = str(port)
    proc = subprocess.Popen([sys.executable, "-m", "imagery_mcp.server"], cwd=RAIZ / "services" / "imagery_mcp",
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(40):
            try:
                if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        else:
            pytest.fail("imagery-mcp no arrancó")

        monkeypatch.setenv("IMAGERY_E2E_KEY", "e2e-key")
        yaml_cfg = cargar(str(RAIZ / "config" / "mcp_servers.yaml")).servers[0]
        cfg = McpConfig.model_validate({"servers": [yaml_cfg.model_copy(update={
            "url": f"{base}/mcp",
            "auth": yaml_cfg.auth.model_copy(update={"secret_ref": "env:IMAGERY_E2E_KEY"}),
        }).model_dump()]})
        hub = hub_mod.McpHub(cfg)
        await hub.refrescar()
        monkeypatch.setattr(hub_mod, "_hub", hub)
        try:
            cap = registry().get("imagery__imagery_ndvi")
            assert cap is not None and cap.id == "mcp.imagery.imagery_ndvi"
            out = await cap.executor(None, {"session_id": "e2e", "map_context": {"viewport": {
                "bbox": [-74.12, 4.60, -74.08, 4.64]}}},
                # temporada seca fija: con el default (últimos 45 días) depende del clima
                {"aoi_geojson": "viewport", "date_from": "2026-01-01", "date_to": "2026-02-28"})
            assert out.success, out.observation
            stats = {r["métrica"]: r["valor"] for r in out.delta["data"]["results"]}
            assert -1.0 <= stats["mean"] <= 1.0
            url = out.delta["external_imagery"]["service_url"]
            assert url.startswith("/api/v1/proxy/mcp/imagery/tiles/")

            r = TestClient(create_app()).get(url.replace("{z}/{x}/{y}", "13/2409/3990"))
            assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n"
        finally:
            for n in list(hub.tools):
                registry().unregister(n)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
