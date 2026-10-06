"""Exporta los JSON Schema de los contratos a `contracts/schema/`.

    python -m geo_copilot.platform.contracts.export            # escribe
    python -m geo_copilot.platform.contracts.export --check    # falla si difieren

Los consumen el frontend (tipos TS generados, S4.1) y el kit de servidores MCP
(S3.1). `tests/test_contracts.py` corre el `--check`: un contrato cambiado sin
regenerar los schema rompe la suite.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from geo_copilot.platform.contracts import ArtifactBundle, GeoResult, LayerRef, MapAction
from geo_copilot.platform.contracts.common import CONTRACT_VERSION
from geo_copilot.platform.contracts.respuesta import QueryResponse, TraceEntry

#: Raíz del repo: src/geo_copilot/platform/contracts/export.py → 5 niveles arriba.
SCHEMA_DIR = Path(__file__).resolve().parents[4] / "contracts" / "schema"

MODELOS: dict[str, type[BaseModel]] = {
    "layer_ref": LayerRef,
    "geo_result": GeoResult,
    "artifact_bundle": ArtifactBundle,
    "map_action": MapAction,
    "query_response": QueryResponse,
}

#: Contratos que el núcleo recibe de fuera (el resto los emite).
_ENTRADAS = frozenset({"geo_result", "map_action"})


def render() -> dict[str, str]:
    """Nombre de archivo → contenido JSON (determinista)."""
    out: dict[str, str] = {}
    for nombre, modelo in MODELOS.items():
        # Lo que el núcleo EMITE (respuestas, capas) se describe como sale serializado:
        # todo campo con default viaja siempre, así que es obligatorio para quien lo lee
        # (el discriminador `kind` deja de ser opcional en los tipos TS). Lo que el
        # núcleo RECIBE (GeoResult de un MCP, MapAction del usuario) se describe como entrada.
        modo: Literal["validation", "serialization"] = (
            "validation" if nombre in _ENTRADAS else "serialization")
        schema = modelo.model_json_schema(mode=modo)
        schema["$id"] = f"geo-copilot/{nombre}/{CONTRACT_VERSION}"
        out[f"{nombre}.schema.json"] = (
            json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    return out


def _ejemplo_respuesta() -> str:
    """Una respuesta REAL de /query (capa + tabla + gráfico + orden al mapa + servicios),
    construida con el mismo código que la API. El frontend la valida en su test de
    contrato: si un lado cambia sin el otro, falla uno de los dos."""
    from datetime import UTC, datetime

    from geo_copilot.platform.artefactos import construir_artefactos

    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-74.1, 4.6]},
         "properties": {"uso": "res", "area": 10}}]}
    ref = {"id": "ds_0123456789abcdef", "name": "Lotes", "kind": "vector", "provider": "core",
           "crs": "EPSG:4326",
           "storage": {"kind": "workspace-table", "schema_name": "ws_ejemplo", "table": "d_0123456789abcdef"},
           "provenance": {"capability": "core.query_data", "produced_at": "2026-09-25T00:00:00+00:00"},
           "feature_count": 1}
    sql = "SELECT uso, area FROM catastro.lotes LIMIT 1"
    resultado: dict[str, Any] = {
        "intent": "query_data", "geojson": fc, "sql": sql,
        "symbology": {"symbology_type": "unique_values", "classification_field": "uso",
                      "class_breaks": [{"label": "res", "color": "#1b9e77", "count": 1}]},
        "data": {"results": [{"uso": "res", "n": 1}]},
        "visualization": {"type": "chart", "chart_type": "bar", "x_axis": "uso", "y_axis": "n"},
        "external_imagery": {"service_url": "/api/v1/proxy/mcp/imagery/tiles/S2/{z}/{x}/{y}.png",
                             "name": "NDVI", "legend": {"type": "ramp", "field": "NDVI", "min": 0, "max": 1}},
        "found_services": [{"name": "Parques", "url": "https://ejemplo.org/FeatureServer",
                            "type": "FeatureServer"}],
        "new_search_executed": True,
    }
    arts = construir_artefactos(resultado, layer_ref=ref, tiles=None, target_layer_id=None, row_count=1)
    resp = QueryResponse(
        query_id="q-ejemplo", session_id="s-ejemplo", status="completed", intent="query_data",
        message="ejemplo", artifacts=arts, sql=sql,
        reasoning_trace=[TraceEntry(step=0, kind="decision", agent="router", detail="query_data", success=True)],
        created_at=datetime(2026, 9, 25, tzinfo=UTC),
    )
    # los campos generados al vuelo (fecha de procedencia de la capa raster) se fijan
    datos = json.loads(resp.model_dump_json())
    for a in datos["artifacts"]:
        if a.get("kind") == "layer":
            a["layer"]["provenance"]["produced_at"] = "2026-09-25T00:00:00Z"
    return json.dumps(datos, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    desactualizados = []
    archivos = {**render(), "../examples/query_response.json": _ejemplo_respuesta()}
    for archivo, contenido in archivos.items():
        destino = SCHEMA_DIR / archivo
        # Sin normalizar, un checkout con core.autocrlf (Windows) deja CRLF y el
        # --check fallaría sin que el contrato haya cambiado.
        actual = (
            destino.read_text(encoding="utf-8").replace("\r\n", "\n")
            if destino.exists() else None
        )
        if actual != contenido:
            desactualizados.append(archivo)
            if not check:
                destino.parent.mkdir(parents=True, exist_ok=True)
                destino.write_text(contenido, encoding="utf-8", newline="\n")
    if check and desactualizados:
        print(
            "Schema desactualizados: " + ", ".join(desactualizados)
            + "\n→ python -m geo_copilot.platform.contracts.export",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
