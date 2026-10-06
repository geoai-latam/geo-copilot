"""La EJECUCIÓN en el sandbox: los datasets de la sesión, la aprobación del código y la
exportación de lo que usó.

Salió de `PythonAgent` (F4 del plan de calidad: agent.py tenía 1.181 líneas), tal cual.
"""

import re
from typing import TYPE_CHECKING, Any

from geo_copilot.core.config import settings
from geo_copilot.core.logging import get_logger

if TYPE_CHECKING:
    from geo_copilot.agents.gis_agent.sandbox import PythonSandbox

logger = get_logger("geo_copilot.agents.python_agent.agent")


_USO_DATASET_RE = re.compile(r"""datasets\[\s*(['"])(.+?)\1\s*\]""")


async def _datasets_de_la_sesion(session_id: str | None) -> dict[str, Any]:
    """{clave: LayerRef} de los datasets vivos de la sesión; {} si no hay workspace.

    La clave es el nombre del dataset; si dos se llaman igual, las siguientes
    llevan " #2", " #3"... (el LLM las ve así en el prompt).
    """
    from geo_copilot.platform.workspace.context import store_actual

    store = store_actual()
    if not session_id or store is None:
        return {}
    try:
        refs = await store.list_datasets(session_id)
    except Exception:  # el workspace es aditivo: sin él, gdf/gdf2 como antes
        logger.warning("[PythonAgent] no se pudo leer el workspace de la sesión", exc_info=True)
        return {}
    salida: dict[str, Any] = {}
    for ref in refs:
        clave, n = ref.name, 1
        while clave in salida:
            n += 1
            clave = f"{ref.name} #{n}"
        salida[clave] = ref
    return salida


def _bloque_datasets(workspace: dict[str, Any]) -> str:
    lineas = []
    for clave, ref in workspace.items():
        campos = ", ".join(f.name for f in ref.fields) or "sin columnas"
        lineas.append(
            f"- datasets[{clave!r}]: {ref.feature_count} filas, geometría "
            f"{ref.geometry_type or 'ninguna'}; columnas: {campos}"
        )
    return (
        "\n\nDATASETS DEL WORKSPACE DE LA SESIÓN (resultados de turnos anteriores, "
        "completos, en EPSG:4326), disponibles como GeoDataFrames en el dict "
        "`datasets` con estas claves EXACTAS:\n" + "\n".join(lineas) + "\n"
        "Úsalos cuando la solicitud se refiera a esas capas o necesite más de dos; "
        "accede SIEMPRE con la clave literal, p.ej. `datasets['Lotes']` (no iteres "
        "el dict ni construyas la clave). `gdf` puede ser None si no hay capa activa "
        "en memoria: en ese caso usa `datasets`."
    )


async def _exportar_usados(
    session_id: str | None, code: str, workspace: dict[str, Any],
) -> dict[str, str]:
    """Exporta a GeoParquet SOLO los datasets que el código nombra.

    Leer las claves literales del código es un hecho, no un juicio: si el
    código no nombra un dataset, el runner no lo carga (memoria y tiempo).
    """
    if not workspace or not session_id:
        return {}
    from geo_copilot.platform.workspace.context import store_actual

    store = store_actual()
    if store is None:
        return {}
    usados = {m.group(2) for m in _USO_DATASET_RE.finditer(code)} & set(workspace)
    manifiesto: dict[str, str] = {}
    for clave in sorted(usados):
        manifiesto[clave] = await store.export_geoparquet(
            session_id, workspace[clave].id, settings.workspace_export_dir,
        )
    return manifiesto


#: el andamiaje del sandbox alrededor del código del LLM: carga `gdf`/`gdf2` desde input_data (sin
#: interpolar el GeoJSON en el código) y recoge las salidas con su post-validación
_ANDAMIO_INICIO = """
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import Point, LineString, Polygon, MultiPoint, MultiLineString, MultiPolygon, box
from shapely.ops import unary_union
import json

# ``geojson_data`` viene inyectado por el sandbox como input_data. None si la
# solicitud trabaja solo con ``datasets`` (S2.4) y no hay capa activa en memoria.
gdf = None
if geojson_data and geojson_data.get('features'):
    gdf = gpd.GeoDataFrame.from_features(geojson_data['features'], crs='EPSG:4326')

# Segunda capa cargada (cross-source): si hay otra capa en el mapa (p.ej. una de
# la BD y otra de un servicio REST), llega como ``gdf2`` para cruces espaciales
# (gpd.sjoin, overlay, clip). Es None si solo hay una capa. ``geojson2_data``
# viaja como input_data (None si no aplica).
gdf2 = None
if geojson2_data and geojson2_data.get('features'):
    gdf2 = gpd.GeoDataFrame.from_features(geojson2_data['features'], crs='EPSG:4326')

# Salidas posibles, pre-inicializadas para poder referenciarlas sin globals()
# (la inspección AST prohíbe globals/locals/vars). El código del usuario
# sobrescribe las que use.
result = None
table = None
stats = None
chart = None
summary = None

# Código del usuario
"""

_ANDAMIO_FIN = """

# ===== Recolección de salidas =====
# El código del usuario puede producir:
#   - `result`: un GeoDataFrame (transformación geométrica → mapa). OPCIONAL.
#   - `table` : DataFrame/lista de registros (resultado tabular).
#   - `stats` : dict de estadísticas resumidas.
#   - `chart` : dict {chart_type, x, y, data, title} para visualizar.
# Geometría y analíticas son independientes: un análisis puede no tener mapa.
def _jsonable(v):
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, np.generic):
        v = v.item()  # escalar numpy -> escalar Python; sigue al saneo de floats abajo
    if isinstance(v, np.ndarray):
        return [_jsonable(x) for x in v.tolist()]
    if isinstance(v, pd.DataFrame):
        # to_json ya convierte NaN->null; re-saneamos por si hubiera Inf.
        return _jsonable(json.loads(v.to_json(orient='records')))
    if isinstance(v, pd.Series):
        return [_jsonable(x) for x in v.tolist()]
    if isinstance(v, float):
        # NaN/Inf NO son JSON validos: Starlette serializa con allow_nan=False
        # y la respuesta HTTP reventaria con 500. Caso real: pearsonr / .corr()
        # sobre una columna constante -> nan. Los mapeamos a null.
        return v if (v == v and v not in (float("inf"), float("-inf"))) else None
    return v

analysis_output = {}

# --- Geometría (result), OPCIONAL — post-validación igual que antes ---
_res = result
if isinstance(_res, gpd.GeoDataFrame):
    if _res.crs is None:
        analysis_output['result_geojson'] = {
            "error": "Result GeoDataFrame has no CRS set. Use result.set_crs(...) or result.to_crs(...).",
            "validation": "crs_missing",
        }
    elif len(_res) == 0:
        _gj = json.loads(_res.to_json())
        _gj["_warning"] = "result_empty"
        analysis_output['result_geojson'] = _gj
    else:
        _invalid = int((~_res.geometry.is_valid).sum())
        if _invalid > 0:
            try:
                _res.geometry = _res.geometry.make_valid()
                _still = int((~_res.geometry.is_valid).sum())
                if _still > 0:
                    analysis_output['result_geojson'] = {
                        "error": f"{_still} geometries invalid after make_valid()",
                        "validation": "invalid_geometry",
                        "invalid_count": _still,
                    }
                else:
                    _gj = json.loads(_res.to_json())
                    _gj["_warning"] = f"repaired_{_invalid}_invalid_geometries"
                    analysis_output['result_geojson'] = _gj
            except Exception as _e:
                analysis_output['result_geojson'] = {
                    "error": f"Invalid geometries and make_valid failed: {_e}",
                    "validation": "invalid_geometry",
                }
        else:
            analysis_output['result_geojson'] = json.loads(_res.to_json())

# --- Salidas analíticas (opcionales) ---
for _name, _v in (('table', table), ('stats', stats), ('chart', chart), ('summary', summary)):
    if _v is not None:
        analysis_output[_name] = _jsonable(_v)
"""


def _salida(sandbox_result: dict) -> dict:
    """Lo que devolvió el sandbox: geometría válida y/o analíticas, o el fallo con su causa."""
    results = sandbox_result.get("results", {})
    out = results.get("analysis_output") or {}
    stdout = sandbox_result.get("output") or ""

    result_geojson = out.get("result_geojson")
    table = out.get("table")
    stats = out.get("stats") or out.get("summary")
    chart = out.get("chart")
    # R4.3: un resultado analítico VACÍO es legítimo (p.ej. table=[] porque
    # el filtro no dejó filas) — `bool(table or ...)` lo clasificaba como
    # "no hay análisis" y disparaba correcciones inútiles. Presencia = el
    # código DEFINIÓ la salida (is not None), no que tenga contenido.
    has_analytics = any(x is not None for x in (table, stats, chart))

    # Geometría válida = dict sin `error` (puede traer `_warning`).
    geom_ok = isinstance(result_geojson, dict) and "error" not in result_geojson
    warning = result_geojson.pop("_warning", None) if geom_ok and isinstance(result_geojson, dict) else None
    if warning:
        logger.warning(f"[PythonAgent] Post-validation warning: {warning}")

    # Si NO hay geometría válida NI analíticas → fallo (con la causa de
    # validación si la geometría falló, para que el CodeCorrector entienda).
    if not geom_ok and not has_analytics:
        if isinstance(result_geojson, dict):
            return {
                "success": False,
                "error": result_geojson.get("error", "Post-validation failed"),
                "validation": result_geojson.get("validation"),
                "invalid_count": result_geojson.get("invalid_count"),
            }
        return {
            "success": False,
            "error": (
                "El código no produjo ni geometría (`result`) ni análisis "
                "(`table`/`stats`/`chart`)."
            ),
        }

    return {
        "success": True,
        "result_geojson": result_geojson if geom_ok else None,
        "table": table,
        "stats": stats,
        "chart": chart,
        "stdout": stdout,
        "warning": warning,
        "execution_time": sandbox_result.get("execution_time"),
    }


class EjecucionMixin:
    """La ejecución del código en el sandbox."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        sandbox: PythonSandbox

    async def _execute_in_sandbox(
        self,
        code: str,
        geojson: dict,
        geojson2: dict | None = None,
        *,
        datasets: dict[str, str] | None = None,
    ) -> dict:
        """
        Ejecutar código en el sandbox.

        Args:
            code: Código Python a ejecutar
            geojson: GeoJSON de entrada

        Returns:
            Resultado de la ejecución
        """
        # Construir el scaffolding del sandbox SIN interpolar el GeoJSON en
        # el código fuente. El GeoJSON viaja como ``input_data`` y entra al
        # namespace del child por nombre — esto evita inyección a través
        # del contenido del feature collection (ver hallazgo P2 del
        # diagnóstico).
        #
        # Sprint C: post-validación del GeoDataFrame resultante — el child
        # devuelve `result_geojson` SOLO si pasa las invariantes (es GDF,
        # tiene CRS, geometrías válidas). Si no, devuelve un dict con
        # `error` + `validation` para que el caller (agent.py) decida si
        # auto-corregir con LLM o reportar al usuario.
        full_code = _ANDAMIO_INICIO + code + _ANDAMIO_FIN

        # Ejecutar en sandbox (sin HITL adicional, ya se aprobó arriba).
        sandbox_result = await self.sandbox.execute(
            code=full_code,
            input_data={"geojson_data": geojson, "geojson2_data": geojson2},
            require_approval=False,  # Ya aprobado en process()
            datasets=datasets or None,
        )

        if not sandbox_result.get("success"):
            return {
                "success": False,
                "error": sandbox_result.get("error", "Sandbox execution failed"),
                "traceback": sandbox_result.get("traceback"),
            }

        return _salida(sandbox_result)

    async def execute_code(
        self,
        code: str,
        geojson: dict
    ) -> dict:
        """
        Herramienta: Ejecutar código en sandbox.

        Args:
            code: Código Python a ejecutar
            geojson: GeoJSON de entrada

        Returns:
            Resultado de la ejecución
        """
        return await self._execute_in_sandbox(code, geojson)
