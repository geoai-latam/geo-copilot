"""La GENERACIÓN del código: la estructura del GeoJSON que el LLM ve y el código que escribe.

Salió de `PythonAgent` (F4 del plan de calidad: agent.py tenía 1.181 líneas), tal cual.
"""

import re
from typing import TYPE_CHECKING, Any

from geo_copilot.agents.python_agent.ejecucion import _bloque_datasets
from geo_copilot.core.llm_client import LLMMessage
from geo_copilot.core.logging import get_logger
from geo_copilot.core.utils import clean_code_from_markdown

from .prompts import SYSTEM_PROMPT

if TYPE_CHECKING:
    from geo_copilot.core.llm_client import LLMClient

logger = get_logger("geo_copilot.agents.python_agent.agent")


def _pedido(query: str, profile_block: str | None) -> str:
    """La solicitud al LLM sobre `gdf`, con el perfil de los datos y lo que debe recordar."""
    return (
        "Genera código Python para resolver la siguiente solicitud sobre el "
        "GeoDataFrame `gdf`:\n\n"
        f"SOLICITUD: {query}\n\n"
        + (f"{profile_block}\n\n" if profile_block else "")
        + "Recuerda:\n"
        "- Elige las salidas según la solicitud: `result` (GeoDataFrame) SOLO "
        "si el resultado es geometría para el mapa; usa `table`/`stats`/`chart` "
        "para análisis, estadística o gráficos. NO fuerces un GeoDataFrame si la "
        "pregunta es analítica.\n"
        "- Si produces `result`, debe ser un GeoDataFrame válido con CRS.\n"
        "- Usa .copy() para no modificar el original.\n"
        "- Si necesitas metros, reproyecta a la zona UTM derivada del centroide "
        "real de los datos (zona = int((lon+180)//6)+1; EPSG = 32600+zona si "
        "lat>=0, 32700+zona si lat<0). NO asumas una zona fija.\n"
        "- Extrae tú mismo distancias/tolerancias del texto de la solicitud."
    )


def _bloque_segunda_capa(secondary_info: dict, secondary_geojson: dict | None) -> str:
    """La SEGUNDA capa (`gdf2`) en el prompt, con su perfil y sus columnas exactas."""
    # Cross-source: si hay una SEGUNDA capa cargada, está disponible como
    # `gdf2`. Se documenta en el prompt para que el LLM pueda cruzarla con
    # `gdf` (spatial join / overlay / clip). Ambas ya están en EPSG:4326.
    from geo_copilot.agents.python_agent.data_profile import build_data_profile

    g2_cols = secondary_info.get("columns", [])
    g2_profile = build_data_profile(secondary_geojson, name="gdf2")
    return (
        "\n\nSEGUNDA CAPA disponible como GeoDataFrame `gdf2` "
        f"(geometría {secondary_info.get('geometry_type', 'Unknown')}).\n"
        + (f"{g2_profile}\n" if g2_profile else "")
        + f"COLUMNAS EXACTAS de gdf2: {', '.join(g2_cols) or 'sin columnas'}\n"
        "❗Usa EXACTAMENTE esos nombres de columna (respeta mayúsculas/tildes); "
        "NO inventes nombres (p.ej. NO uses 'Clase'/'clase' si no está en la "
        "lista) — si dudas del campo de clasificación, elige el que MÁS se "
        "parezca de la lista. Igual para `gdf` (columnas arriba).\n"
        "Úsala para cruces entre las dos capas: `gpd.sjoin(gdf, gdf2, "
        "predicate='within'|'intersects')`, `gpd.overlay(gdf, gdf2, how='intersection')`, "
        "`gdf.clip(gdf2)`. Reproyecta ambas al MISMO CRS métrico si mides "
        "distancias/áreas. `gdf2` es None si no aplica; no la uses salvo que la "
        "solicitud pida combinar las capas."
    )


class GeneracionMixin:
    """La generación del código: la estructura del GeoJSON que el LLM ve y el código que escribe."""

    if TYPE_CHECKING:  # lo que el mixin usa de su clase anfitriona
        llm_client: LLMClient

    def _analyze_geojson_structure(self, geojson: dict) -> dict:
        """
        Analizar la estructura de un GeoJSON.

        Args:
            geojson: GeoJSON a analizar

        Returns:
            Diccionario con información de estructura
        """
        features = geojson.get("features", [])
        if not features:
            return {
                "count": 0,
                "columns": [],
                "geometry_type": None,
                "crs": "EPSG:4326",
            }

        # Obtener propiedades del primer feature
        first_feature = features[0]
        properties = first_feature.get("properties", {})
        columns = list(properties.keys()) if properties else []

        # Obtener tipo de geometría
        geometry = first_feature.get("geometry", {})
        geometry_type = geometry.get("type", "Unknown")

        # Detectar CRS. RFC 7946 §4: si el GeoJSON no declara CRS, ES WGS84.
        # Normalizamos los formatos comunes (EPSG:4326, urn:ogc:def:crs:EPSG::4326)
        # usando regex para no confundir códigos como 43261 con 4326.
        crs_raw = geojson.get("crs", {}).get("properties", {}).get("name")
        if crs_raw:
            m = re.search(r"EPSG[:/]+(\d+)", str(crs_raw), re.IGNORECASE)
            crs = f"EPSG:{m.group(1)}" if m else str(crs_raw)
        else:
            crs = "EPSG:4326"  # default por spec RFC 7946

        return {
            "count": len(features),
            "columns": columns,
            "geometry_type": geometry_type,
            "crs": crs,
            "sample_properties": properties,
        }

    async def _generate_code(
        self,
        query: str,
        geojson_info: dict,
        secondary_info: dict | None = None,
        geojson: dict | None = None,
        secondary_geojson: dict | None = None,
        workspace: dict[str, Any] | None = None,
    ) -> str | None:
        """Generar código Python para la operación usando LLM.

        El LLM extrae por sí mismo distancias/tolerancias/parámetros del texto
        del usuario — antes había `extract_distance_from_query` con regex que
        servía solo de pista; eliminada en Sprint C para no duplicar la
        interpretación NL en dos lugares.

        A4: recibe además los GeoJSON crudos para inyectar al prompt un PERFIL
        determinista de los datos (dtypes/nulos/min-max/top categorías) — el
        LLM escribe código contra datos reales, no contra nombres a ciegas.
        """
        system_prompt = SYSTEM_PROMPT.format(
            columns=", ".join(geojson_info.get("columns", [])),
            crs=geojson_info.get("crs", "EPSG:4326"),
            count=geojson_info.get("count", 0),
            geometry_type=geojson_info.get("geometry_type", "Unknown"),
        )

        # A4: perfil determinista de la capa activa (hechos, no juicio).
        from geo_copilot.agents.python_agent.data_profile import build_data_profile
        profile_block = build_data_profile(geojson, name="gdf")

        user_prompt = _pedido(query, profile_block)

        if secondary_info:
            user_prompt += _bloque_segunda_capa(secondary_info, secondary_geojson)

        if workspace:
            user_prompt += _bloque_datasets(workspace)

        try:
            response = await self.llm_client.chat([
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt)
            ])

            code = response.content.strip()

            # Limpiar el código (remover markdown si viene)
            code = clean_code_from_markdown(code, "python")

            return code

        except Exception as e:  # captura amplia a propósito: llamada al LLM (red/proveedor/timeout); None = no se pudo generar código
            logger.error(f"[PythonAgent] Code generation error: {e}", exc_info=True)
            return None

    async def generate_code(
        self,
        query: str,
        geojson_info: dict | None = None
    ) -> dict:
        """
        Herramienta: Generar código Python.

        Args:
            query: Descripción de la operación
            geojson_info: Información del GeoJSON

        Returns:
            Diccionario con código generado
        """
        code = await self._generate_code(
            query=query,
            geojson_info=geojson_info or {}
        )

        return {
            "code": code,
        }

    async def analyze_geojson(self, geojson: dict) -> dict:
        """
        Herramienta: Analizar estructura de GeoJSON.

        Args:
            geojson: GeoJSON a analizar

        Returns:
            Información de estructura
        """
        return self._analyze_geojson_structure(geojson)
