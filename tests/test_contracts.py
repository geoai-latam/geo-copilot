"""S1.1 — contratos del núcleo: `LayerRef`, `GeoResult`, `ArtifactBundle`.

Los fixtures tienen la forma de datos REALES del sistema: lotes del catastro
(como los devuelve PostGIS con `ST_AsGeoJSON`), la respuesta de `imagery_ndvi`
del servicio imagery-mcp y una tabla con geometría WKB como la entregaría un MCP
tabular (Snowflake/Postgres).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from geo_copilot.platform.contracts import (
    MAX_INLINE_FEATURES,
    ArtifactBundle,
    GeoResult,
    LayerRef,
    MapCommand,
)

AHORA = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)


def _lotes(n: int = 3) -> dict:
    """Lotes con el esquema de catastro.lotes (docker/init-db/02_seed_demo.sql)."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [-74.15 + i * 1e-3, 4.55], [-74.149 + i * 1e-3, 4.55],
                    [-74.149 + i * 1e-3, 4.551], [-74.15 + i * 1e-3, 4.551],
                    [-74.15 + i * 1e-3, 4.55],
                ]]},
                "properties": {
                    "objectid": i, "lotcodigo": f"0045{i:06d}", "lotdispers": "N",
                    "manzcodigo": "004512", "lotdistrit": "19",
                },
            }
            for i in range(n)
        ],
    }


def _procedencia(**kw) -> dict:
    return {"capability": "core.query_database", "produced_at": AHORA.isoformat(), **kw}


def _layer_lotes(**over) -> dict:
    base = {
        "id": "ds_7f3a",
        "name": "Lotes catastrales",
        "kind": "vector",
        "provider": "core",
        "crs": "EPSG:4326",
        "geometry_type": "Polygon",
        "bbox": [-74.15, 4.55, -74.147, 4.551],
        "feature_count": 3,
        "fields": [
            {"name": "lotcodigo", "type": "string", "sample": ["0045000000"]},
            {"name": "lotdispers", "type": "string", "sample": ["N", "D"]},
        ],
        "storage": {"kind": "geojson-inline", "data": _lotes()},
        "provenance": _procedencia(sql="SELECT ... FROM catastro.lotes LIMIT 3"),
    }
    base.update(over)
    return base


# ── LayerRef ────────────────────────────────────────────────────────────────


class TestLayerRef:
    def test_round_trip_de_lotes_reales(self):
        capa = LayerRef.model_validate(_layer_lotes())
        otra = LayerRef.model_validate_json(capa.model_dump_json())
        assert otra == capa
        assert otra.storage.kind == "geojson-inline"

    def test_sin_crs_se_rechaza(self):
        datos = _layer_lotes()
        del datos["crs"]
        with pytest.raises(ValidationError, match="crs"):
            LayerRef.model_validate(datos)

    @pytest.mark.parametrize("crs", ["4326", "WGS84", "epsg:4326", ""])
    def test_crs_mal_formado_se_rechaza(self, crs):
        with pytest.raises(ValidationError):
            LayerRef.model_validate(_layer_lotes(crs=crs))

    @pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:9377", "OGC:CRS84", "ESRI:102100"])
    def test_crs_validos(self, crs):
        assert LayerRef.model_validate(_layer_lotes(crs=crs)).crs == crs

    def test_bbox_invertida_se_rechaza(self):
        with pytest.raises(ValidationError, match="bbox invertida"):
            LayerRef.model_validate(_layer_lotes(bbox=[-74.0, 4.6, -74.2, 4.5]))

    def test_demasiadas_features_inline_se_rechazan(self):
        grande = {"type": "FeatureCollection", "features": [{}] * (MAX_INLINE_FEATURES + 1)}
        with pytest.raises(ValidationError, match="superan el tope"):
            LayerRef.model_validate(_layer_lotes(storage={"kind": "geojson-inline", "data": grande}))

    def test_raster_no_puede_ser_geojson(self):
        with pytest.raises(ValidationError, match="no puede guardarse"):
            LayerRef.model_validate(_layer_lotes(kind="raster"))

    def test_capa_ndvi_como_teselas(self):
        capa = LayerRef.model_validate(_layer_lotes(
            id="ds_ndvi", name="NDVI 2026-08-10", kind="raster", provider="mcp.imagery",
            geometry_type=None, fields=[],
            storage={
                "kind": "raster-tiles",
                "url_template": "/api/v1/proxy/mcp/imagery/tiles/S2B_X/{z}/{x}/{y}.png",
                "minzoom": 8, "maxzoom": 15,
                "legend": {"type": "ramp", "min": -0.03, "max": 0.70},
            },
            provenance=_procedencia(
                capability="mcp.imagery.imagery_ndvi",
                source_version="S2B_MSIL2A_20260810T152649_R025_T18NWL",
            ),
        ))
        assert capa.storage.kind == "raster-tiles"

    def test_teselas_sin_plantilla_xyz_se_rechazan(self):
        with pytest.raises(ValidationError, match="plantilla XYZ"):
            LayerRef.model_validate(_layer_lotes(
                kind="raster", storage={"kind": "raster-tiles", "url_template": "/tiles/ndvi.png"},
            ))

    def test_tabla_del_workspace(self):
        capa = LayerRef.model_validate(_layer_lotes(storage={
            "kind": "workspace-table", "schema_name": "ws_a1b2", "table": "lotes_ndvi",
        }))
        assert capa.storage.schema_name == "ws_a1b2"

    def test_esquema_de_workspace_invalido_se_rechaza(self):
        # El nombre de esquema entra en SQL: solo ws_[a-z0-9_]+.
        with pytest.raises(ValidationError):
            LayerRef.model_validate(_layer_lotes(storage={
                "kind": "workspace-table", "schema_name": "public; DROP", "table": "x",
            }))

    def test_campos_desconocidos_se_rechazan(self):
        with pytest.raises(ValidationError, match="Extra inputs"):
            LayerRef.model_validate(_layer_lotes(geojson={"type": "FeatureCollection"}))

    def test_el_nombre_es_parte_del_contrato(self):
        """H3 de F0: re-estilar renombraba la capa. El nombre es un campo propio,
        separado del estilo: cambiar `style` no toca `name`."""
        capa = LayerRef.model_validate(_layer_lotes())
        restyled = capa.model_copy(update={"style": {"symbology_type": "single_symbol"}})
        assert restyled.name == capa.name


# ── GeoResult (MCP) ─────────────────────────────────────────────────────────


def _ndvi_geo_result() -> dict:
    """La respuesta de `imagery_ndvi` expresada como GeoResult (S3.6 la migra)."""
    return {
        "geo_result": "1",
        "artifacts": [
            {
                "kind": "raster_tiles", "name": "NDVI 2026-08-10",
                "tiles": "/tiles/S2B_MSIL2A_20260810T152649_R025_T18NWL/{z}/{x}/{y}.png",
                "crs": "EPSG:4326", "bounds": [-74.16, 4.54, -74.14, 4.56],
                "minzoom": 8, "maxzoom": 15,
                "legend": {"type": "ramp", "rescale": [-0.0343, 0.7021]},
            },
            {"kind": "stats", "items": [
                {"label": "NDVI medio", "value": 0.2069},
                {"label": "píxeles válidos", "value": 260869},
            ]},
        ],
        "facts": {"scene_date": "2026-08-10", "cloud_pct": 1.31},
    }


class TestGeoResult:
    def test_round_trip_ndvi(self):
        r = GeoResult.model_validate(_ndvi_geo_result())
        assert GeoResult.model_validate_json(r.model_dump_json()) == r
        assert [a.kind for a in r.artifacts] == ["raster_tiles", "stats"]

    def test_feature_collection_sin_crs_se_rechaza(self):
        with pytest.raises(ValidationError, match="crs"):
            GeoResult.model_validate({"artifacts": [
                {"kind": "feature_collection", "name": "x", "data": _lotes()},
            ]})

    def test_feature_collection_grande_exige_feature_ref(self):
        grande = {"type": "FeatureCollection", "features": [{}] * (MAX_INLINE_FEATURES + 1)}
        with pytest.raises(ValidationError, match="feature_ref"):
            GeoResult.model_validate({"artifacts": [
                {"kind": "feature_collection", "name": "x", "crs": "EPSG:4326", "data": grande},
            ]})

    def test_feature_ref_a_geoparquet(self):
        r = GeoResult.model_validate({"artifacts": [{
            "kind": "feature_ref", "name": "Predios", "crs": "EPSG:4326",
            "uri": "https://datos.example/predios.parquet", "format": "geoparquet",
            "feature_count": 250_000,
        }]})
        assert r.artifacts[0].format == "geoparquet"

    def test_feature_ref_con_uri_local_se_rechaza(self):
        with pytest.raises(ValidationError):
            GeoResult.model_validate({"artifacts": [{
                "kind": "feature_ref", "name": "x", "crs": "EPSG:4326",
                "uri": "file:///etc/passwd", "format": "geojson",
            }]})

    def test_tabla_con_geometria_wkb(self):
        """Forma de un MCP tabular (Snowflake: ST_ASWKB sobre GEOGRAPHY)."""
        r = GeoResult.model_validate({"artifacts": [{
            "kind": "table", "name": "ventas_municipio",
            "columns": ["municipio", "ventas", "geom_wkb"],
            "rows": [{"municipio": "Soacha", "ventas": 1200, "geom_wkb": "0103000020E6100000"}],
            "geometry": {"column": "geom_wkb", "encoding": "wkb_hex", "crs": "EPSG:4326"},
        }]})
        assert r.artifacts[0].geometry.encoding == "wkb_hex"

    def test_latlon_exige_columna_de_latitud(self):
        with pytest.raises(ValidationError, match="lat_column"):
            GeoResult.model_validate({"artifacts": [{
                "kind": "table", "name": "t", "columns": ["lon", "lat"], "rows": [],
                "geometry": {"column": "lon", "encoding": "latlon", "crs": "EPSG:4326"},
            }]})

    def test_artefacto_desconocido_se_rechaza(self):
        with pytest.raises(ValidationError):
            GeoResult.model_validate({"artifacts": [{"kind": "script", "code": "rm -rf /"}]})


# ── ArtifactBundle (back → front) ───────────────────────────────────────────


class TestArtifactBundle:
    def test_respuesta_con_capa_tabla_grafico_y_comando(self):
        bundle = ArtifactBundle.model_validate({
            "message": "Encontré 3 lotes.",
            "artifacts": [
                {"kind": "layer", "layer": _layer_lotes()},
                {"kind": "table", "columns": ["lotcodigo"], "preview": [{"lotcodigo": "1"}],
                 "total_rows": 3, "rows_ref": "ds_7f3a"},
                {"kind": "chart", "spec": {"chart_type": "bar", "x_key": "lotdispers",
                                           "y_key": "n"},
                 "data": [{"lotdispers": "N", "n": 2}, {"lotdispers": "D", "n": 1}]},
                {"kind": "map_command",
                 "command": {"op": "zoom_to", "layer_id": "ds_7f3a"}},
            ],
        })
        assert [a.kind for a in bundle.artifacts] == ["layer", "table", "chart", "map_command"]
        from geo_copilot.platform.contracts import CONTRACT_VERSION

        assert bundle.contract_version == CONTRACT_VERSION
        assert ArtifactBundle.model_validate_json(bundle.model_dump_json()) == bundle

    def test_operacion_de_mapa_desconocida_se_rechaza(self):
        from pydantic import TypeAdapter

        with pytest.raises(ValidationError):
            TypeAdapter(MapCommand).validate_python({"op": "delete_database"})

    def test_cada_operacion_valida_sus_argumentos(self):
        """FH.1: set_style trae un StyleSpec válido; set_opacity un número en [0, 1]."""
        from pydantic import TypeAdapter

        orden = TypeAdapter(MapCommand)
        with pytest.raises(ValidationError):
            orden.validate_python({"op": "set_style", "layer_id": "l", "args": {"style": {"symbology_type": "arcoiris"}}})
        with pytest.raises(ValidationError):
            orden.validate_python({"op": "set_opacity", "layer_id": "l", "args": {"opacity": 2}})
        assert orden.validate_python({"op": "reorder", "layer_id": "l", "args": {"to": "top"}}).args.to == "top"


# ── Schema publicados ───────────────────────────────────────────────────────


def test_los_schema_publicados_estan_al_dia():
    """Cambiar un contrato sin regenerar contracts/schema/ rompe la suite."""
    from geo_copilot.platform.contracts import export

    assert export.main(["--check"]) == 0, (
        "Regenera: python -m geo_copilot.platform.contracts.export"
    )
