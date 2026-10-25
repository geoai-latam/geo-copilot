"""Plan de fiabilidad F3: una capa que no se pudo pintar no se cuenta como hecha."""

from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_capa_que_no_se_pinta_sale_como_fallo_con_su_causa(monkeypatch):
    from geo_copilot.platform.mcp import materializar as m

    async def falla(*a, **k):
        raise ValueError("Geometry has Z dimension but column does not")

    monkeypatch.setattr(m, "_cargar_artefacto", falla)
    cfg = SimpleNamespace(id="tn_ambiente", tiles=SimpleNamespace(prefixes=[]))
    gr = {"geo_result": "1", "facts": {"total_en_catalogo": 6},
          "artifacts": [{"kind": "feature_collection", "name": "Sismos", "data": {"type": "FeatureCollection", "features": []}}]}
    out = await m._materializar(cfg, "usgs_sismos_en_zona", gr, {"session_id": "s"}, {})
    assert out.success is False
    assert "Geometry has Z dimension" in out.observation and "no_se_pudo_pintar" in out.observation
    assert "NO quedó ninguna capa" in out.observation and "total_en_catalogo" in out.observation


@pytest.mark.asyncio
async def test_si_otra_capa_si_quedo_el_paso_no_falla(monkeypatch):
    from geo_copilot.platform.mcp import materializar as m

    llamadas = {"n": 0}

    async def una_falla(cfg, art, nombre, *a, **k):
        llamadas["n"] += 1
        if llamadas["n"] == 1:
            raise ValueError("geometría inválida")
        return {"id": "ds_1", "name": nombre, "feature_count": 3}, None

    monkeypatch.setattr(m, "_cargar_artefacto", una_falla)
    monkeypatch.setattr(m, "_delta_de_capas", lambda *a, **k: None)
    cfg = SimpleNamespace(id="x", tiles=SimpleNamespace(prefixes=[]))
    gr = {"geo_result": "1", "facts": {},
          "artifacts": [{"kind": "feature_collection", "name": "A"}, {"kind": "feature_collection", "name": "B"}]}
    out = await m._materializar(cfg, "t", gr, {"session_id": "s"}, {})
    assert out.success is True and "no_se_pudo_pintar" in out.observation and "NO quedó" not in out.observation
