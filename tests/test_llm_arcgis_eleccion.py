"""Rama arcgis-busqueda, V4 con LLM REAL: con los hechos de cada candidato, el agente elige bien.

V5 (Chrome, como usuario): «necesito una capa con las vías de Bogotá» cargó 462 PUNTOS de la cartografía
de Cota y respondió «He cargado las vías de Bogotá». El LLM solo veía «5 servicio(s) encontrados»
(eligió «el 1» a ciegas), el cargador tomaba la capa 0 y, tras cargar, solo veía «462 elemento(s)».

Las observaciones son las que producen las herramientas REALES (`_observe`); solo se simulan los
servicios. Se mide la TASA en serie: el LLM no es determinista.

    pytest -m llm tests/test_llm_arcgis_eleccion.py -s
"""
from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from tests.conftest import get_real_llm_or_skip

pytestmark = pytest.mark.llm

CORRIDAS = 4

CANDIDATOS = [
    {"name": "Cartografía Básica. Municipio de Cota. Escala 1K. 2022", "type": "FeatureServer",
     "org": "Departamento de Cundinamarca", "owner": "ideradmin", "single_layer": False, "views": 96039,
     "description": "Producto cartográfico básico a escala 1:1.000 del municipio de Cota"},
    {"name": "Estratificación_Vias_Bogotá", "type": "FeatureServer", "owner": "andfardilar_UDFJC",
     "views": 645, "completeness": 66, "single_layer": False, "modified": "2023-12-02",
     "description": "Se realiza un mapa temático extrayendo datos sobre la estratificación"},
    {"name": "Malla Vial Integral Bogota D_C", "type": "FeatureServer", "owner": "SecretariaMovilidad",
     "credits": "Secretaría Distrital de Movilidad", "views": 60010, "completeness": 98, "single_layer": True,
     "modified": "2024-07-23",
     "description": "Conjunto de líneas que definen los ejes viales de cada una de las vías de la ciudad"},
    {"name": "Tramos Críticos de Siniestralidad de Peatones - Vías Locales", "type": "FeatureServer",
     "credits": "Oficina de Seguridad Vial - Secretaría Distrital de Movilidad", "views": 117,
     "single_layer": True, "description": "Tramos críticos de siniestralidad"},
]


def _grafo(llm):
    g = MagicMock()
    g.llm = llm
    g.agent_metrics = None
    return g


async def _correr(monkeypatch, query: str, responder) -> tuple[list[tuple[str, dict]], str]:
    from geo_copilot.orchestrator.nodes import agent_loop

    llamadas: list[tuple[str, dict]] = []

    async def espia(g, w, name, args):
        llamadas.append((name, dict(args or {})))
        return responder(name, dict(args or {}), llamadas)

    monkeypatch.setattr(agent_loop, "dispatch_tool", espia)
    estado = await agent_loop.run(_grafo(await get_real_llm_or_skip()), {
        "query": query, "session_id": "x", "map_context": {"layers": []},
        "messages": [{"agent": "router", "data": {"reasoning": "El usuario pide una capa que no está cargada: "
                                                             "hay que buscarla en los portales."}}]})
    return llamadas, str((estado or {}).get("final_response") or "")


def _obs(name: str, delta: dict):
    from geo_copilot.orchestrator.react_tools import _observe
    from geo_copilot.platform.capabilities import ToolOutcome

    texto, ok = _observe(name, delta)
    return ToolOutcome(texto, success=ok)


def _cargada(nombre, geometria, campos, n):
    return {"geojson": {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None,
                                                                   "properties": {}}] * n},
            "external_source_name": nombre,
            "external_layer_facts": {"servicio": nombre, "geometria": geometria, "campos": campos}}


@pytest.mark.asyncio
async def test_con_los_hechos_elige_la_malla_vial_de_movilidad_y_no_la_cartografia_de_cota(monkeypatch):
    elegidos = []
    for _ in range(CORRIDAS):
        def responder(name, args, _ll):
            if name == "search_external":
                return _obs(name, {"found_services": CANDIDATOS})
            if name == "select_service":
                return _obs(name, _cargada("Malla Vial Integral Bogota D_C", "Polyline",
                                           ["MVINOMBRE", "MVITIPO", "MVICCAT"], 8))
            return _obs(name, {})

        llamadas, resp = await _correr(monkeypatch, "necesito una capa con las vías de Bogotá, búscala y "
                                                    "muéstramela en el mapa", responder)
        eleccion = next((a.get("number") for n, a in llamadas if n == "select_service"), None)
        print(f"[elección] {eleccion} <- {[n for n, _ in llamadas]} | {resp[:300]}")
        elegidos.append(eleccion)
    assert elegidos.count(3) >= CORRIDAS - 1, elegidos  # la Malla Vial Integral de la Secretaría de Movilidad


@pytest.mark.asyncio
async def test_en_un_servicio_con_varias_capas_elige_la_de_vias(monkeypatch):
    capas = [{"id": 0, "nombre": "Puntos de control geodésico", "tipo_geometria": "Point"},
             {"id": 3, "nombre": "Vías", "tipo_geometria": "Polyline"},
             {"id": 5, "nombre": "Construcciones", "tipo_geometria": "Polygon"}]
    elegidas = []
    for _ in range(CORRIDAS):
        def responder(name, args, _ll):
            if name == "search_external":
                return _obs(name, {"found_services": CANDIDATOS[:1]})
            vio = any(n == "select_service" and a.get("layer") is None for n, a in _ll[:-1])
            if name == "select_service" and (args.get("layer") is None or not vio):
                # como el código real: una capa adivinada sin haber visto las del servicio se ignora
                return _obs(name, {"service_layers": capas, "service_layers_of": {
                    "name": CANDIDATOS[0]["name"], "url": "https://s/x/Cota/FeatureServer"}})
            if name == "select_service":
                return _obs(name, _cargada(CANDIDATOS[0]["name"] + " · Vías", "Polyline", ["NOMBRE", "TIPO"], 6))
            return _obs(name, {})

        llamadas, resp = await _correr(monkeypatch, "trae las vías del municipio de Cota", responder)
        # la capa CARGADA: la última elegida después de ver las capas
        capa = next((a.get("layer") for n, a in reversed(llamadas) if n == "select_service" and a.get("layer") is not None),
                    None)
        print(f"[capa] {capa} <- {[(n, a) for n, a in llamadas]} | {resp[:200]}")
        elegidas.append(capa)
    assert elegidas.count(3) >= CORRIDAS - 1, elegidas


@pytest.mark.asyncio
async def test_si_lo_cargado_no_es_lo_pedido_no_lo_narra_como_si_lo_fuera(monkeypatch):
    """La V5 original: se cargaron PUNTOS de control de Cota pidiendo vías de Bogotá. Con la geometría y
    los campos en la observación, el agente no debe decir «he cargado las vías de Bogotá» sin más."""
    malos = []
    for _ in range(CORRIDAS):
        def responder(name, args, ll):
            if name == "search_external":
                return _obs(name, {"found_services": CANDIDATOS[:1]})
            if name == "select_service":
                return _obs(name, _cargada("Cartografía Básica. Municipio de Cota · Puntos de control",
                                           "Point", ["CCATEGOR", "CIDENTIF", "CTIPO"], 462))
            return _obs(name, {})

        llamadas, resp = await _correr(monkeypatch, "necesito una capa con las vías de Bogotá", responder)
        t = resp.lower()
        siguio = sum(1 for n, _ in llamadas if n in ("search_external", "select_service")) > 2
        dice_que_no = re.search(r"punto|no (son|corresponde|es)|cota|no encontr|otra (capa|fuente|búsqueda)", t)
        afirma = re.search(r"(he )?cargad[oa].{0,60}v[ií]as de bogot", t)
        malo = bool(afirma) and not dice_que_no and not siguio
        print(f"[verificación] {'MAL' if malo else 'ok'} <- {[n for n, _ in llamadas]} | {resp[:300]}")
        malos.append(malo)
    assert malos.count(True) <= 1, malos
