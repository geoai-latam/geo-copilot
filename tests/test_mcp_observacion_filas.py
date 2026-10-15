"""Las filas de una capa que no caben en la observación: enteras hasta el tope y con cuántas faltan.

V5 (ogc-mcp, 2026-10-15): 8 áreas del RUNAP ocupaban 6 423 caracteres con un tope de 3 000. El corte a
mitad del JSON dejó ver unas 3 y el agente nombró 8: cinco inventadas (Jaboque, Entrenubes… no están en
el RUNAP), aunque el texto terminaba en «RECORTADO».
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from geo_copilot.platform.mcp import materializar, referencias
from geo_copilot.platform.mcp.materializar import _observacion


def _runap(n: int) -> dict:
    """Filas con la forma de las del RUNAP (23 campos, textos largos)."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {
            "ap_id": i, "ap_nombre": f"Área protegida número {i}", "ap_categoria": "Reserva Natural de la Sociedad Civil",
            "condicion": "REGISTRADA", "fecha_inscrita": "2016-08-11T00:00:00Z", "fecha_registro": "2016-08-11T00:00:00Z",
            "url": f"https://runap.parquesnacionales.gov.co/area-protegida/{1000 + i}",
            **{f"area_ha_{k}": 1234.5678 + i for k in range(10)}}} for i in range(n)]}


def _obs(delta: dict) -> dict:
    texto = _observacion(SimpleNamespace(id="ogc"), {"facts": {"capa": "RUNAP", "elementos": 8}},
                         [{"id": "ds_1", "name": "RUNAP", "feature_count": 8}], None, delta, [])
    assert "RECORTADO" not in texto  # la observación cabe: no hay JSON cortado a la mitad
    return json.loads(texto[texto.index("{"):])


def test_si_no_caben_se_ven_filas_enteras_y_cuantas_faltan(monkeypatch):
    monkeypatch.setattr(materializar, "_max_obs", lambda: 3000)
    obs = _obs({"geojson": _runap(8)})
    vistas = obs["contenido"]["elementos"]
    assert 0 < len(vistas) < 8
    assert all(len(f) == 17 for f in vistas)  # cada fila con TODOS sus campos (ninguna cortada)
    assert [f["ap_id"] for f in vistas] == list(range(len(vistas)))
    aviso = obs["contenido"]["aviso"]
    assert f"viste {len(vistas)} de 8" in aviso and f"los {8 - len(vistas)} que no viste" in aviso


def test_si_caben_van_todas_sin_aviso(monkeypatch):
    monkeypatch.setattr(materializar, "_max_obs", lambda: 20000)
    monkeypatch.setattr(referencias, "_max_obs", lambda: 20000)  # el tope final (`_limitar`) también
    obs = _obs({"geojson": _runap(8)})
    assert len(obs["contenido"]["elementos"]) == 8 and "aviso" not in obs["contenido"]
