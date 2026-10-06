"""La SEGURIDAD del código antes de ejecutarlo: módulos permitidos, nombres, submódulos y
métodos prohibidos, y el análisis estático que los aplica.

Salió de `PythonSandbox` (F4 del plan de calidad: sandbox.py tenía 593 líneas), tal cual.
"""

from __future__ import annotations

import ast
from typing import Any

from geo_copilot.core.logging import get_logger

logger = get_logger("geo_copilot.agents.gis_agent.sandbox")


def _sandbox():
    """`sandbox` importa este módulo; la clase (y lo que las pruebas sustituyen en ella) se busca allí."""
    from geo_copilot.agents.gis_agent import sandbox

    return sandbox


class SandboxError(Exception):
    """Error en la ejecución del sandbox."""


class SecurityViolation(SandboxError):
    """Violación de seguridad detectada por el filtro estático."""


class ResourceLimitExceeded(SandboxError):
    """Límite de recursos excedido."""


class SandboxSeguridadMixin:
    """La SEGURIDAD del código antes de ejecutarlo: módulos permitidos, nombres, submódulos y"""

    # Módulos permitidos para análisis geoespacial.
    ALLOWED_MODULES = {
        # Geoespaciales
        "geopandas", "shapely", "shapely.geometry", "shapely.ops",
        "pyproj", "fiona",
        # Datos
        "pandas", "numpy",
        # Estadísticas / análisis / ML. La validación por módulo base permite
        # sus submódulos EXCEPTO los de FORBIDDEN_SUBMODULES (datasets/ctypeslib/
        # f2py/distutils). ADVERTENCIA (modelo de amenaza real): el filtro AST es
        # DEFENSA EN PROFUNDIDAD, no una barrera contra RCE/egreso — pandas/numpy/
        # sklearn tienen I/O de red y deserialización por diseño. La contención
        # real es el CONTENEDOR endurecido (network:none, cap_drop:ALL, non-root);
        # el subproceso solo aísla la memoria del proceso padre. Ver SEC-SANDBOX-RCE.
        "scipy", "scipy.stats", "scipy.spatial", "scipy.cluster",
        "sklearn", "statsmodels",
        # Análisis espacial avanzado (PySAL) + redes: autocorrelación espacial
        # (Moran's I, LISA), hotspots (Getis-Ord Gi*), pesos espaciales
        # (KNN/Queen/Rook), regresión espacial (OLS/lag/error), patrones de
        # puntos (vecino más cercano, Ripley's K, densidad), grafos/redes.
        # scipy.spatial (arriba) cubre Voronoi/Delaunay/ConvexHull/cKDTree.
        "esda", "libpysal", "spreg", "pointpats", "networkx",
        # Utilidades
        # SBX-01: `operator` se RETIRÓ del allowlist. `operator.attrgetter(
        # '__class__.__base__')` / `operator.methodcaller('__subclasses__')`
        # reciben los atributos como STRINGS que el filtro AST nunca inspecciona
        # → getattr indirecto que evade por completo el denylist de atributos.
        # El análisis geoespacial no necesita `operator`.
        "json", "math", "datetime", "collections", "itertools",
        "functools", "statistics", "random", "heapq", "bisect",
        # Visualización (sólo generación)
        "matplotlib", "matplotlib.pyplot", "folium", "plotly",
        "plotly.express", "plotly.graph_objects",
    }

    # Nombres prohibidos: bloquean rutas obvias de escape estáticamente.
    # No son la barrera real (lo es el subproceso); son defensa en
    # profundidad para fallar rápido ante código claramente malicioso.
    FORBIDDEN_NAMES = {
        "exec", "eval", "compile", "__import__",
        "open", "file", "input",
        "globals", "locals", "vars",
        "os", "sys", "subprocess", "shutil", "ctypes",
        "socket", "requests", "urllib", "http",
        "getattr", "setattr", "delattr",
        "__builtins__", "__loader__", "__spec__",
    }

    # SEC-SANDBOX-RCE: submódulos PROHIBIDOS aunque su base esté permitida.
    # El allowlist valida por base (numpy/sklearn/... permiten todo submódulo),
    # y eso reexpone vectores de RCE/egreso: `numpy.ctypeslib` reexpone ctypes
    # (→ CDLL('libc.so.6')['system']), `numpy.f2py`/`distutils` compilan código,
    # y `*.datasets` (sklearn/statsmodels/scipy) descargan por red (SSRF/egreso).
    # Se comparan por SEGMENTO del path de import (last o cualquiera).
    FORBIDDEN_SUBMODULES = {
        "ctypeslib", "ctypes", "f2py", "distutils", "datasets",
        # libpysal.examples / *.examples descargan datasets de ejemplo por red.
        "examples",
    }

    # Métodos/callables PROHIBIDOS (acceso por atributo `x.metodo(...)`): cargan
    # librerías nativas, deserializan datos remotos (RCE por pickle) o hacen
    # egreso de red. El filtro por nombre es DEFENSA EN PROFUNDIDAD, no la
    # barrera real (esa es el contenedor endurecido); cierra los vectores
    # concretos hallados en la auditoría.
    FORBIDDEN_METHODS = {
        # Carga de librerías nativas → ejecución de código nativo.
        "CDLL", "cdll", "WinDLL", "windll", "LoadLibrary", "PyDLL",
        # Deserialización remota (RCE por pickle) / egreso de red.
        "read_pickle", "read_parquet", "read_orc", "read_feather",
        "urlretrieve", "urlopen", "fetch_openml", "get_rdataset",
        "DataSource",
        # R3.4: lectura de datos por URL/archivo (pd.read_csv('http://...')
        # etc.). En este sandbox los datos SIEMPRE entran inyectados como
        # gdf/gdf2 — no hay uso legítimo de read_*; con el backend subprocess
        # (default) estas llamadas eran egreso de red real. El prompt ya
        # afirmaba que se rechazan; ahora es verdad.
        "read_csv", "read_json", "read_html", "read_excel", "read_xml",
        "read_fwf", "read_table", "read_file",
        # SBX-01/SBX-04: getattr indirecto por string y traversal de tipos.
        # `attrgetter`/`methodcaller` construyen getattr desde strings (evaden
        # el AST); `mro()` alcanza `object` sin usar `__bases__`.
        "attrgetter", "methodcaller", "mro",
    }

    def analyze_security(self, code: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "is_safe": True,
            "violations": [],
            "warnings": [],
            "modules_used": [],
            "functions_called": [],
        }
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            result["is_safe"] = False
            result["violations"].append(f"Syntax error: {exc}")
            return result

        visitor = _Visitor()
        visitor.visit(tree)
        result["violations"] = visitor.violations
        result["warnings"] = visitor.warnings
        result["modules_used"] = sorted(visitor.modules)
        result["functions_called"] = sorted(visitor.functions)
        result["is_safe"] = not visitor.violations
        return result


class _Visitor(ast.NodeVisitor):
    """El análisis estático: recorre el AST y anota módulos, llamadas y violaciones."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.warnings: list[str] = []
        self.modules: set[str] = set()
        self.functions: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.modules.add(alias.name)
            segments = alias.name.split(".")
            base = segments[0]
            if alias.name not in _sandbox().PythonSandbox.ALLOWED_MODULES and base not in _sandbox().PythonSandbox.ALLOWED_MODULES:
                self.violations.append(f"Módulo no permitido: {alias.name}")
            elif any(s in _sandbox().PythonSandbox.FORBIDDEN_SUBMODULES for s in segments):
                self.violations.append(f"Submódulo prohibido: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.modules.add(node.module)
            segments = node.module.split(".")
            base = segments[0]
            if node.module not in _sandbox().PythonSandbox.ALLOWED_MODULES and base not in _sandbox().PythonSandbox.ALLOWED_MODULES:
                self.violations.append(f"Módulo no permitido: {node.module}")
            elif any(s in _sandbox().PythonSandbox.FORBIDDEN_SUBMODULES for s in segments):
                self.violations.append(f"Submódulo prohibido: {node.module}")
            else:
                # `from numpy import ctypeslib`: el submódulo peligroso
                # es el NOMBRE importado, no un segmento del módulo.
                for alias in node.names:
                    if alias.name in _sandbox().PythonSandbox.FORBIDDEN_SUBMODULES:
                        self.violations.append(
                            f"Submódulo prohibido: {node.module}.{alias.name}"
                        )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _sandbox().PythonSandbox.FORBIDDEN_NAMES:
            self.violations.append(f"Nombre prohibido: {node.id}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            self.functions.add(node.func.id)
            if node.func.id in _sandbox().PythonSandbox.FORBIDDEN_NAMES:
                self.violations.append(f"Función prohibida: {node.func.id}")
            elif node.func.id in _sandbox().PythonSandbox.FORBIDDEN_METHODS:
                self.violations.append(f"Llamada prohibida: {node.func.id}")
        elif isinstance(node.func, ast.Attribute):
            self.functions.add(node.func.attr)
            if node.func.attr in {"system", "popen", "spawn", "call"}:
                self.violations.append(f"Función de sistema prohibida: {node.func.attr}")
            elif node.func.attr in _sandbox().PythonSandbox.FORBIDDEN_METHODS:
                self.violations.append(f"Llamada prohibida: {node.func.attr}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # ``__mro__`` and friends are useful escape vectors. Block at
        # the AST layer; the subprocess is still the real barrier.
        if node.attr in {
            "__class__", "__bases__", "__subclasses__", "__globals__",
            "__mro__", "__reduce__", "__getattribute__", "__dict__",
            "__code__", "__closure__",
            # SBX-04: vías equivalentes de traversal de tipos que el
            # denylist original omitía. `__base__` (singular) alcanza
            # `object` igual que `__bases__`; `mro()` idem; los
            # `__*_subclass__`/`__class_getitem__`/`__reduce_ex__`
            # exponen el árbol de clases o la reconstrucción; los
            # `__getattr__`/`__setattr__`/`__delattr__` son getattr
            # indirecto a nivel de dunder.
            "__base__", "mro", "__subclasshook__", "__init_subclass__",
            "__class_getitem__", "__reduce_ex__",
            "__getattr__", "__setattr__", "__delattr__",
            # SEC-SANDBOX-RCE: numpy.ctypeslib reexpone el módulo ctypes
            # (→ CDLL('libc.so.6')['system']). Bloquear el acceso al
            # atributo cierra ese vector en el filtro estático.
            "ctypes", "ctypeslib",
            # R0.5 (auditoría 2026-07-26, AUD-03): `__builtins__` sólo
            # se comprobaba en `visit_Name`, es decir a secas — no como
            # ATRIBUTO de un módulo. `import json; json.__builtins__`
            # devolvía el diccionario COMPLETO de builtins reales
            # (eval, exec, open, getattr, __import__...), verificado
            # ejecutando el runner. Eso anulaba de una sola línea
            # ALLOWED_MODULES, FORBIDDEN_NAMES, FORBIDDEN_SUBMODULES y
            # FORBIDDEN_METHODS enteros.
            "__builtins__", "__loader__", "__spec__", "__module__",
            "__init__",
        }:
            self.violations.append(f"Acceso a atributo prohibido: {node.attr}")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # R0.5 (AUD-03): el escape usaba `b['__import__']`, un Subscript
        # con literal de cadena — que ni `visit_Name` ni `visit_Call`
        # inspeccionan. Cualquier indexación por un nombre prohibido o
        # por un dunder es un intento de alcanzar la tabla de builtins
        # por la puerta de atrás; ningún análisis geoespacial legítimo
        # necesita `algo["__import__"]`.
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            key = sl.value
            if key in _sandbox().PythonSandbox.FORBIDDEN_NAMES or (
                key.startswith("__") and key.endswith("__")
            ):
                self.violations.append(f"Indexación prohibida: [{key!r}]")
        self.generic_visit(node)
