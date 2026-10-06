"""Búsqueda en ArcGIS Hub: normalización y parámetros (servidor arcgis-mcp) y orden (núcleo, hub_items)."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from geo_copilot.agents.data_agent.discovery import DiscoveryHints, _plan_to_search_params
from geo_copilot.agents.data_agent.hub_items import HubItem, rank_results

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "arcgis_mcp"))
from arcgis_mcp import hub

HUB_PAGE_SIZE_MAX = hub.HUB_PAGE_SIZE_MAX
_service_kind = hub.tipo_de_servicio
_bbox_intersects = hub.bbox_intersecta


def _normalize_item(raw):
    """Como en producción: el servidor normaliza el item y el núcleo lo reconstruye."""
    d = hub.normalizar(raw)
    return HubItem.from_dict(d) if d is not None else None


def _build_params(*, page_number, page_size=None, **kw):
    return hub.parametros(pagina=page_number, **kw)


def search_hub(transport_handler, **kw):
    """`hub.buscar` con el Hub simulado (sin red)."""
    with patch.object(hub, "cliente", lambda url, **k: httpx.Client(transport=httpx.MockTransport(transport_handler))):
        items, _ = hub.buscar(**kw)
    return [HubItem.from_dict(d) for d in items]


def _fake_attrs(**overrides):
    base = {
        "name": "Manzanas Bogota",
        "description": "Capa oficial",
        "url": "https://services2.arcgis.com/ABC/arcgis/rest/services/Manzanas/FeatureServer/0",
        "serverURL": "https://services2.arcgis.com/ABC/arcgis/rest/services/Manzanas/FeatureServer",
        "owner": "IGAC.Comunicaciones",
        "source": "Instituto Geográfico Agustín Codazzi",
        "type": "Feature Service",
        "layerId": 0,
        "tags": ["catastro", "bogota", "colombia"],
        "modified": datetime.now(UTC).isoformat(),
        "extent": {"coordinates": [[-74.22, 4.47], [-73.99, 4.83]]},
        "itemId": "abc123",
    }
    base.update(overrides)
    return base


def _fake_item(**overrides):
    return {"id": "x", "attributes": _fake_attrs(**overrides)}


# =============================================================================
# Unit helpers
# =============================================================================
class TestServiceKind:
    def test_feature_service(self):
        assert _service_kind("Feature Service", "") == "FeatureServer"

    def test_map_service(self):
        assert _service_kind("Map Service", "") == "MapServer"

    def test_image_service(self):
        assert _service_kind("Image Service", "") == "ImageServer"

    def test_falls_back_to_url_when_type_missing(self):
        url = "https://x/rest/services/Y/ImageServer"
        assert _service_kind("", url) == "ImageServer"

    def test_unknown(self):
        assert _service_kind("Web Map", "") == "Other"


class TestBboxIntersects:
    def test_no_extent_rejected(self):
        """Cambio 2026-05-24: items sin extent declarado se descartan cuando
        hay bbox filter (antes pasaban → inundaba el panel con items
        globales sin metadatos geográficos)."""
        assert _bbox_intersects(None, [-74.91, 3.69, -73.04, 5.83]) is False

    def test_overlap(self):
        # Item en Bogotá vs bbox de Colombia
        assert _bbox_intersects([-74.2, 4.5, -74.0, 4.8], [-81.85, -4.23, -66.85, 13.41])

    def test_disjoint(self):
        # Item en Asia vs bbox de Colombia
        assert not _bbox_intersects([100.0, 30.0, 110.0, 40.0], [-81.85, -4.23, -66.85, 13.41])

    def test_global_extent_rejected_when_bbox_small(self):
        """Item con extent global ([-180,-90,180,90], típico de MODIS/NWS)
        no es específico de Cundinamarca aunque 'intersecte' por matemática
        de bboxes. Se descarta."""
        cundi = [-74.91, 3.69, -73.04, 5.83]
        assert _bbox_intersects([-180, -90, 180, 90], cundi) is False

    def test_continental_extent_rejected_when_bbox_small(self):
        """Item USA (extent ~50° lado) cuando se pide Cundinamarca (~2°) →
        área ratio >50× → descartar."""
        cundi = [-74.91, 3.69, -73.04, 5.83]
        usa = [-125.0, 24.0, -66.0, 49.0]  # USA continental, ~60×25°
        assert _bbox_intersects(usa, cundi) is False

    def test_country_extent_passes_when_bbox_small(self):
        """Item con extent Colombia entero pasa cuando se pide
        Cundinamarca — Colombia es ~6× el área de Cundinamarca."""
        cundi = [-74.91, 3.69, -73.04, 5.83]
        colombia = [-81.85, -4.23, -66.85, 13.41]
        assert _bbox_intersects(colombia, cundi) is True


class TestNormalizeItem:
    def test_normalizes_feature_service(self):
        item = _normalize_item(_fake_item())
        assert item is not None
        assert item.service_type == "FeatureServer"
        assert item.layer_id == 0
        assert item.owner == "IGAC.Comunicaciones"
        assert "bogota" in item.tags
        assert item.hub_url and "abc123" in item.hub_url

    def test_returns_none_when_no_url(self):
        item = _normalize_item({"attributes": {"url": "", "serverURL": ""}})
        assert item is None

    def test_dedupe_id_stable(self):
        a = _normalize_item(_fake_item())
        b = _normalize_item(_fake_item())
        assert a.id == b.id


# =============================================================================
# Param building — la regla clave: NO filter[extent]
# =============================================================================
class TestBuildParams:
    def test_no_filter_extent_emitted(self):
        params = _build_params(
            text_query="x",
            tags_all=None,
            tags_any=None,
            owner_any=None,
            source_any=None,
            service_types=None,
            modified_after=None,
            sort="-modified",
            page_number=1,
            page_size=20,
        )
        keys = [k for k, _ in params]
        assert "filter[extent]" not in keys

    def test_page_size_capped(self):
        params = _build_params(
            text_query="x",
            tags_all=None,
            tags_any=None,
            owner_any=None,
            source_any=None,
            service_types=None,
            modified_after=None,
            sort="-modified",
            page_number=1,
            page_size=100,  # se debe capar a 20
        )
        sizes = [v for k, v in params if k == "page[size]"]
        assert sizes == [str(HUB_PAGE_SIZE_MAX)]

    def test_tags_all_serialization(self):
        params = _build_params(
            text_query=None,
            tags_all=["catastro", "colombia"],
            tags_any=None,
            owner_any=None,
            source_any=None,
            service_types=None,
            modified_after=None,
            sort="-modified",
            page_number=1,
            page_size=20,
        )
        d = dict(params)
        assert d["filter[tags]"] == "all(catastro,colombia)"

    def test_tags_any_serialization_when_no_all(self):
        params = _build_params(
            text_query=None,
            tags_all=None,
            tags_any=["a", "b"],
            owner_any=None,
            source_any=None,
            service_types=None,
            modified_after=None,
            sort="-modified",
            page_number=1,
            page_size=20,
        )
        d = dict(params)
        assert d["filter[tags]"] == "any(a,b)"

    def test_service_types_csv(self):
        params = _build_params(
            text_query=None,
            tags_all=None,
            tags_any=None,
            owner_any=None,
            source_any=None,
            service_types=["Feature Service", "Map Service"],
            modified_after=None,
            sort="-modified",
            page_number=1,
            page_size=20,
        )
        d = dict(params)
        assert d["filter[type]"] == "Feature Service,Map Service"


# =============================================================================
# Ranking
# =============================================================================
class TestDiversification:
    def test_top_window_interleaves_types(self):
        """Dado un top dominado por MapServers, el round-robin los baraja
        con Feature/Image antes de devolver."""
        items = []
        # 8 MapServers
        for i in range(8):
            items.append(
                _normalize_item(
                    _fake_item(
                        type="Map Service",
                        url=f"https://services.arcgis.com/X/rest/services/M{i}/MapServer",
                        owner="",
                        source="",
                    )
                )
            )
        # 2 FeatureServers
        for i in range(2):
            items.append(
                _normalize_item(
                    _fake_item(
                        type="Feature Service",
                        url=f"https://services.arcgis.com/X/rest/services/F{i}/FeatureServer/0",
                        owner="",
                        source="",
                    )
                )
            )
        # 2 ImageServers
        for i in range(2):
            items.append(
                _normalize_item(
                    _fake_item(
                        type="Image Service",
                        url=f"https://services.arcgis.com/X/rest/services/I{i}/ImageServer",
                        owner="",
                        source="",
                    )
                )
            )

        ranked = rank_results(items)
        # En los primeros 6 NO deberían venir solo MapServers
        first_types = [it.service_type for it in ranked[:6]]
        unique_types = set(first_types)
        assert len(unique_types) >= 2, f"Top sin diversidad: {first_types}"


class TestRanking:
    def test_official_owner_outranks_random(self):
        official = _normalize_item(_fake_item(owner="IGAC.Comunicaciones"))
        random_user = _normalize_item(_fake_item(owner="someuser123", source=""))
        ranked = rank_results([random_user, official])
        assert ranked[0].owner == "IGAC.Comunicaciones"

    def test_loadable_outranks_webmap(self):
        feat = _normalize_item(
            _fake_item(owner="", source="", type="Feature Service")
        )
        webmap = _normalize_item(
            _fake_item(
                owner="",
                source="",
                type="Web Map",
                url="https://services2.arcgis.com/X/rest/services/Y/MapServer",
            )
        )
        ranked = rank_results([webmap, feat])
        assert ranked[0].title == feat.title
        assert ranked[0].rank_score > ranked[1].rank_score

    def test_recent_outranks_old(self):
        old_iso = (datetime.now(UTC) - timedelta(days=1500)).isoformat()
        new = _normalize_item(_fake_item(owner="", source=""))
        old = _normalize_item(_fake_item(owner="", source="", modified=old_iso))
        ranked = rank_results([old, new])
        assert ranked[0].modified == new.modified


# =============================================================================
# search_hub end-to-end con httpx mockeado
# =============================================================================
class TestSearchHubE2E:
    def test_refuses_when_no_filters(self):
        with pytest.raises(hub.BusquedaInvalida):
            hub.buscar()

    def test_only_official_co_expands_to_official_owners(self):
        """El chip «Oficial CO» lo resuelve el núcleo con SU catálogo: pasa `owner_any` al servidor."""
        params = _plan_to_search_params({}, hints=DiscoveryHints(only_official_co=True))
        assert "only_official_co" not in params
        assert len(params["owner_any"]) > 1

    def test_paginates_until_max(self):
        def make_page(page_num):
            return {"data": [{"id": f"p{page_num}-i{i}", "attributes": _fake_attrs(
                url=f"https://services2.arcgis.com/X/rest/services/L{page_num}_{i}/FeatureServer/0",
                serverURL=f"https://services2.arcgis.com/X/rest/services/L{page_num}_{i}/FeatureServer",
                itemId=f"id-{page_num}-{i}")} for i in range(20)]}

        calls = {"n": 0}

        def handler(req):
            calls["n"] += 1
            return httpx.Response(200, json=make_page(int(req.url.params.get("page[number]", "1"))))

        items = search_hub(handler, text_query="manzanas", max_results=35)
        assert len(items) == 35
        assert calls["n"] >= 2

    def test_bbox_filters_client_side(self):
        data = {"data": [
            {"id": "bog", "attributes": _fake_attrs(
                url="https://services2.arcgis.com/X/rest/services/Bog/FeatureServer/0",
                extent={"coordinates": [[-74.2, 4.5], [-74.0, 4.8]]})},
            {"id": "asia", "attributes": _fake_attrs(
                url="https://services2.arcgis.com/X/rest/services/Asia/FeatureServer/0",
                extent={"coordinates": [[100.0, 30.0], [110.0, 40.0]]})},
        ]}
        items = search_hub(lambda req: httpx.Response(200, json=data), text_query="manzanas",
                           bbox=[-81.85, -4.23, -66.85, 13.41], max_results=10)
        urls = [it.service_url for it in items]
        assert any("Bog" in u for u in urls)
        assert not any("Asia" in u for u in urls)

    def test_does_not_send_filter_extent_to_api(self):
        vistos = []

        def handler(req):
            vistos.extend(k for k, _ in req.url.params.multi_items())
            return httpx.Response(200, json={"data": []})

        search_hub(handler, text_query="x", bbox=[-81.85, -4.23, -66.85, 13.41])
        assert vistos and "filter[extent]" not in vistos
