"""Un servidor MCP remoto con llave: defectos por servidor, errores estructurados y llave ausente."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from geo_copilot.platform.mcp.config import McpConfig, ServerConfig
from geo_copilot.platform.mcp.connection import McpConnection, ServidorNoDisponible
from geo_copilot.platform.mcp.ejecucion import _con_fijos, _llamar
from geo_copilot.platform.mcp.hub import McpHub


def _cfg(**extra):
    return ServerConfig(id="tn_territorio", url="https://puerta.example/mcp/territorio",
                        auth={"type": "bearer", "secret_ref": "env:LLAVE_DE_PRUEBA_REMOTA"},
                        tools={"allow": ["*"]}, **extra)


def _est(props):
    return SimpleNamespace(esquema={"type": "object", "properties": props})


def test_el_fijo_solo_llega_a_la_tool_que_lo_declara():
    cfg = _cfg(argumentos_fijos={"geometria": True})
    assert _con_fijos(cfg, _est({"bbox": {}, "geometria": {}}), {"bbox": [1, 2, 3, 4]}) == {
        "bbox": [1, 2, 3, 4], "geometria": True}
    # una tool sin `geometria` la rechazaría
    assert _con_fijos(cfg, _est({"bbox": {}}), {"bbox": [1, 2, 3, 4]}) == {"bbox": [1, 2, 3, 4]}


def test_el_fijo_gana_al_valor_que_copio_el_llm_del_esquema():
    """V5: el LLM mandó `geometria: false` en «muéstrame en el mapa» y la capa salió sin forma."""
    cfg = _cfg(argumentos_fijos={"geometria": True})
    assert _con_fijos(cfg, _est({"geometria": {}}), {"geometria": False}) == {"geometria": True}


@pytest.mark.asyncio
async def test_la_traza_muestra_lo_enviado_no_lo_que_pidio_el_llm(monkeypatch):
    """«Cómo lo hice» decía `geometria: False` cuando a la fuente le llegó `True` (navegador, 2026-11-02)."""
    from geo_copilot.platform.capabilities import ToolOutcome
    from geo_copilot.platform.mcp import ejecucion

    enviados: dict = {}

    async def _llamar_falso(hub, con, cfg, tool, est, working, args, **kw):
        enviados.update(args)
        return ToolOutcome("ok", success=True)

    async def _sin_geo(est, working, pedidos):
        return dict(pedidos), None

    monkeypatch.setattr(ejecucion, "_llamar", _llamar_falso)
    monkeypatch.setattr(ejecucion, "_resolver_geo", _sin_geo)
    cfg = _cfg(argumentos_fijos={"geometria": True})
    hub = SimpleNamespace(conexiones={cfg.id: object()})
    est = SimpleNamespace(esquema={"type": "object", "properties": {"lon": {}, "geometria": {}}},
                          servidor=cfg.id, tool="ficha_lugar", geo=None)
    out = await ejecucion.ejecutar_tool(hub, cfg, "ficha_lugar", est, {}, {"lon": -74.0, "geometria": False})
    assert enviados == {"lon": -74.0, "geometria": True}
    assert out.args_enviados == {"lon": -74.0, "geometria": True}
    # sin cambios no hay nada que corregir en la traza
    out = await ejecucion.ejecutar_tool(hub, cfg, "ficha_lugar", est, {}, {"lon": -74.0, "geometria": True})
    assert out.args_enviados is None


def test_sin_fijos_los_argumentos_no_cambian():
    args = {"bbox": [1, 2, 3, 4]}
    assert _con_fijos(_cfg(), _est({"bbox": {}}), args) is args


def _con_error(texto, estructurado):
    async def call_tool(*_):
        return SimpleNamespace(content=[SimpleNamespace(text=texto)], isError=True,
                               structuredContent=estructurado)

    return SimpleNamespace(call_tool=call_tool)


@pytest.mark.asyncio
async def test_el_codigo_estructurado_corta_los_reintentos_aunque_el_texto_no_lo_diga():
    con = _con_error("La fuente no respondió a tiempo.", {"codigo": "fuente_lenta", "status": 504})
    out = await _llamar(None, con, _cfg(), "igac_algo", _est({}), {}, {})
    assert out.success is False
    assert out.degraded_code == "fuente_lenta"
    assert "codigo=fuente_lenta" in out.observation


@pytest.mark.asyncio
async def test_un_error_que_no_es_de_la_fuente_no_corta_los_reintentos():
    con = _con_error("Sin saldo.", {"codigo": "saldo_agotado", "status": 402})
    out = await _llamar(None, con, _cfg(), "igac_algo", _est({}), {}, {})
    assert out.success is False and out.degraded_code is None
    assert "codigo=saldo_agotado" in out.observation


@pytest.mark.asyncio
async def test_el_codigo_no_se_repite_si_ya_viene_en_el_texto():
    con = _con_error("fuente_no_disponible: caída", {"codigo": "fuente_no_disponible"})
    out = await _llamar(None, con, _cfg(), "igac_algo", _est({}), {}, {})
    assert out.degraded_code == "fuente_no_disponible"
    assert out.observation.count("fuente_no_disponible") == 1


@pytest.mark.asyncio
async def test_sin_llave_el_servidor_remoto_no_esta_disponible(monkeypatch):
    monkeypatch.delenv("LLAVE_DE_PRUEBA_REMOTA", raising=False)
    with pytest.raises(ServidorNoDisponible, match="no tiene credencial"):
        async with McpConnection(_cfg())._sesion():
            pass


def test_un_servidor_sin_llave_no_entra_al_prompt(monkeypatch):
    monkeypatch.delenv("LLAVE_DE_PRUEBA_REMOTA", raising=False)
    hub = McpHub(McpConfig(servers=[_cfg(description="datos remotos")]))
    assert "tn_territorio" not in hub.resumen_prompt()
    monkeypatch.setenv("LLAVE_DE_PRUEBA_REMOTA", "x")
    assert "tn_territorio" in hub.resumen_prompt()


def test_las_entradas_de_terrnova_del_repo_son_validas_y_sin_secretos():
    ruta = Path(__file__).resolve().parents[1] / "config" / "mcp_servers.yaml"
    servidores = yaml.safe_load(ruta.read_text(encoding="utf-8"))["servers"]
    tn = [ServerConfig(**s) for s in servidores if s["id"].startswith("tn_")]
    assert len(tn) == 8
    for cfg in tn:
        assert cfg.url.startswith("https://terrnova.com/mcp/")
        assert cfg.auth.secret_ref == "env:TERRANOVA_LLAVE"
        assert cfg.policy.default_risk == "read"
        assert cfg.policy.listar_timeout_s > 20  # su catálogo no se lista en el tope de la red interna
        assert cfg.policy.timeout_s < 120  # una llamada colgada no se come el turno entero
        assert cfg.argumentos_fijos == {"geometria": True}
