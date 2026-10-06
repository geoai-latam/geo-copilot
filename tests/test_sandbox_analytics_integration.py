"""Integración REAL del canal analítico con el sandbox POSIX (Docker/Linux).

Cierra el gap que señaló el audit: el scaffolding de ``_execute_in_sandbox``
nunca se ejercitaba (mockeado en los unit tests; POSIX-only en runtime). Aquí
se EJECUTA de verdad el sandbox:
  * clustering real con sklearn/DBSCAN → chart/table/stats,
  * un ``nan`` (columna constante) que debe llegar saneado a null
    (FIX-JSONABLE-NONFINITE) en vez de reventar la respuesta con 500,
  * y el exploit ``numpy.ctypeslib`` rechazado ANTES de ejecutar
    (SEC-SANDBOX-RCE p1), sin romper el clustering legítimo.

Marcado ``integration``: se salta por default y en Windows nativo
(SANDBOX_AVAILABLE=False). Corre en el contenedor Linux:
    pytest -m integration tests/test_sandbox_analytics_integration.py
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from geo_copilot.agents.gis_agent.sandbox import SANDBOX_AVAILABLE
from geo_copilot.agents.python_agent.agent import PythonAgent

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not SANDBOX_AVAILABLE, reason="sandbox POSIX-only (Docker/Linux)"),
]


def _fc():
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [c[0], c[1]]},
             "properties": {"area": c[2], "pob": c[3]}}
            for c in [(0, 0, 10, 100), (0.1, 0.1, 11, 110),
                      (10, 10, 50, 500), (10.1, 10.1, 51, 510)]
        ],
    }


_CLUSTER_CODE = """
import numpy as np
from sklearn.cluster import DBSCAN
from collections import Counter
coords = np.c_[gdf.geometry.x.values, gdf.geometry.y.values]
labels = DBSCAN(eps=1.0, min_samples=2).fit_predict(coords)
counts = Counter(int(l) for l in labels)
table = [{"cluster": int(k), "n": int(v)} for k, v in counts.items()]
stats = {"n_clusters": int(len(set(labels))), "corr_constante": float("nan")}
chart = {"chart_type": "bar", "x": "cluster", "y": "n", "data": table, "title": "Conteo por cluster"}
"""


@pytest.mark.asyncio
async def test_clustering_produce_chart_table_stats_y_sanea_nan():
    agent = PythonAgent(llm_client=MagicMock())
    res = await agent._execute_in_sandbox(_CLUSTER_CODE, _fc())

    assert res["success"] is True, res.get("error")
    assert res["chart"] is not None and res["chart"]["chart_type"] == "bar"
    assert isinstance(res["table"], list) and len(res["table"]) == 2
    # NaN saneado a None — sin esto, Starlette (allow_nan=False) daría 500.
    assert res["stats"]["corr_constante"] is None
    assert res["stats"]["n_clusters"] == 2


@pytest.mark.asyncio
async def test_exploit_ctypeslib_rechazado_en_ejecucion_real():
    agent = PythonAgent(llm_client=MagicMock())
    res = await agent._execute_in_sandbox(
        "import numpy.ctypeslib as n\nlibc = n.ctypes\n", _fc()
    )
    assert res["success"] is False
