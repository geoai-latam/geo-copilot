"""Tests para el proxy de teselas de imagery: /api/v1/proxy/imagery.

Cubre el happy-path (reemite el PNG), el guard anti-SSRF (host fuera de la
allowlist + IP privada) y la validación de parámetros (bbox, endpoint).

Nota: `URLValidator.validate_url` hace resolución DNS real; para el happy-path
y el caso "upstream no-imagen" se mockea (tiene su propia batería de tests en
test_discovery_ssrf), de modo que estos tests corren offline y deterministas.
Los tests de rechazo usan literales (127.0.0.1 / host inexistente) que el
validador bloquea sin depender de la red.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from geo_copilot.api.app import create_app

# Host presente en settings.allowed_domains por defecto (IGAC principal).
ALLOWED = (
    "https://mapas2.igac.gov.co/server/rest/services/Cartografia/IGAC/ImageServer"
)
BBOX = "-8237000,500000,-8236000,501000"

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def client():
    return TestClient(create_app())


def _httpx_mock(status: int = 200, content: bytes = _PNG_MAGIC, content_type: str = "image/png"):
    """Devuelve un stub de httpx.AsyncClient usable como context manager async."""
    resp = MagicMock()
    resp.status_code = status
    resp.content = content
    resp.headers = {"content-type": content_type}

    inner = AsyncMock()
    inner.get = AsyncMock(return_value=resp)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=inner)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestProxyImageryHappyPath:
    def test_reemite_el_png_con_content_type_de_imagen(self, client):
        with patch(
            "geo_copilot.api.routes.proxy.URLValidator.resolve_validated_ip",
            return_value=("93.184.216.34", "mapas2.igac.gov.co"),
        ), patch(
            "geo_copilot.api.routes.proxy.httpx.AsyncClient",
            return_value=_httpx_mock(),
        ):
            resp = client.get(
                "/api/v1/proxy/imagery",
                params={"service": ALLOWED, "endpoint": "exportImage", "bbox": BBOX},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/")
        assert resp.content.startswith(_PNG_MAGIC)

    def test_upstream_que_no_es_imagen_da_502(self, client):
        # ArcGIS puede responder JSON de error incluso con f=image.
        with patch(
            "geo_copilot.api.routes.proxy.URLValidator.resolve_validated_ip",
            return_value=("93.184.216.34", "mapas2.igac.gov.co"),
        ), patch(
            "geo_copilot.api.routes.proxy.httpx.AsyncClient",
            return_value=_httpx_mock(content_type="application/json", content=b'{"error":{}}'),
        ):
            resp = client.get(
                "/api/v1/proxy/imagery",
                params={"service": ALLOWED, "endpoint": "exportImage", "bbox": BBOX},
            )
        assert resp.status_code == 502

    def test_upstream_status_no_200_da_502(self, client):
        with patch(
            "geo_copilot.api.routes.proxy.URLValidator.resolve_validated_ip",
            return_value=("93.184.216.34", "mapas2.igac.gov.co"),
        ), patch(
            "geo_copilot.api.routes.proxy.httpx.AsyncClient",
            return_value=_httpx_mock(status=404),
        ):
            resp = client.get(
                "/api/v1/proxy/imagery",
                params={"service": ALLOWED, "endpoint": "exportImage", "bbox": BBOX},
            )
        assert resp.status_code == 502


class TestProxyImagerySSRF:
    def test_bloquea_ip_loopback(self, client):
        # 127.0.0.1 es literal → el validador lo bloquea sin tocar la red.
        resp = client.get(
            "/api/v1/proxy/imagery",
            params={
                "service": "http://127.0.0.1/rest/services/x/ImageServer",
                "endpoint": "exportImage",
                "bbox": BBOX,
            },
        )
        assert resp.status_code == 400

    def test_bloquea_host_fuera_de_la_allowlist(self, client):
        # Aunque resuelva, no está en settings.allowed_domains → 400.
        resp = client.get(
            "/api/v1/proxy/imagery",
            params={
                "service": "https://evil.example.org/rest/services/x/ImageServer",
                "endpoint": "exportImage",
                "bbox": BBOX,
            },
        )
        assert resp.status_code == 400

    def test_bloquea_esquema_no_http(self, client):
        resp = client.get(
            "/api/v1/proxy/imagery",
            params={
                "service": "file:///etc/passwd",
                "endpoint": "exportImage",
                "bbox": BBOX,
            },
        )
        assert resp.status_code == 400


class TestProxyImageryPinning:
    """#25: se conecta a la IP validada (sin re-resolver), Host+SNI del nombre."""

    def test_conecta_a_la_ip_validada_con_host_y_sni(self, client):
        resp_obj = MagicMock()
        resp_obj.status_code = 200
        resp_obj.content = _PNG_MAGIC
        resp_obj.headers = {"content-type": "image/png"}
        inner = AsyncMock()
        inner.get = AsyncMock(return_value=resp_obj)
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=inner)
        cm.__aexit__ = AsyncMock(return_value=False)
        with patch(
            "geo_copilot.api.routes.proxy.URLValidator.resolve_validated_ip",
            return_value=("93.184.216.34", "mapas2.igac.gov.co"),
        ), patch(
            "geo_copilot.api.routes.proxy.httpx.AsyncClient", return_value=cm,
        ):
            r = client.get("/api/v1/proxy/imagery", params={
                "service": ALLOWED, "endpoint": "exportImage", "bbox": BBOX})
        assert r.status_code == 200
        args, kwargs = inner.get.call_args
        assert "93.184.216.34" in args[0]              # conecta a la IP fijada
        assert "mapas2.igac.gov.co" not in args[0]     # NO re-resuelve por nombre
        assert kwargs["headers"]["Host"] == "mapas2.igac.gov.co"
        assert kwargs["extensions"]["sni_hostname"] == "mapas2.igac.gov.co"

    def test_resolve_validated_ip_bloquea_rebinding(self):
        from geo_copilot.core.security.url_validator import URLValidator
        # Conjunto con IP pública Y privada → bloquea TODO (anti-rebinding).
        with patch("socket.gethostbyname_ex",
                   return_value=("h", [], ["93.184.216.34", "127.0.0.1"])):
            with pytest.raises(ValueError, match="private network"):
                URLValidator.resolve_validated_ip(
                    "https://mapas2.igac.gov.co/x", ["mapas2.igac.gov.co"])
        # Solo pública → devuelve (ip, hostname).
        with patch("socket.gethostbyname_ex",
                   return_value=("h", [], ["93.184.216.34"])):
            ip, host = URLValidator.resolve_validated_ip(
                "https://mapas2.igac.gov.co/x", ["mapas2.igac.gov.co"])
        assert ip == "93.184.216.34" and host == "mapas2.igac.gov.co"


class TestProxyImageryValidation:
    def test_rechaza_bbox_invalido(self, client):
        resp = client.get(
            "/api/v1/proxy/imagery",
            params={"service": ALLOWED, "endpoint": "exportImage", "bbox": "no-bbox"},
        )
        assert resp.status_code == 400

    def test_rechaza_bbox_con_pocos_valores(self, client):
        resp = client.get(
            "/api/v1/proxy/imagery",
            params={"service": ALLOWED, "endpoint": "exportImage", "bbox": "1,2,3"},
        )
        assert resp.status_code == 400

    def test_rechaza_endpoint_no_permitido(self, client):
        resp = client.get(
            "/api/v1/proxy/imagery",
            params={"service": ALLOWED, "endpoint": "identify", "bbox": BBOX},
        )
        assert resp.status_code == 400

    def test_acepta_bbox_en_notacion_cientifica(self, client):
        # El bbox debe pasar la validación; luego se mockea el fetch.
        with patch(
            "geo_copilot.api.routes.proxy.URLValidator.resolve_validated_ip",
            return_value=("93.184.216.34", "mapas2.igac.gov.co"),
        ), patch(
            "geo_copilot.api.routes.proxy.httpx.AsyncClient",
            return_value=_httpx_mock(),
        ):
            resp = client.get(
                "/api/v1/proxy/imagery",
                params={
                    "service": ALLOWED,
                    "endpoint": "exportImage",
                    "bbox": "-8.23e6,5.0e5,-8.236e6,5.01e5",
                },
            )
        assert resp.status_code == 200


class TestProxyImageryIdentify:
    """FRT-03: proxy de /identify (JSON de atributos/valor de píxel) con SSRF."""

    def test_reemite_json_y_aplica_allowlist_de_params(self, client):
        payload = b'{"results":[{"layerName":"Predios","attributes":{"ESTRATO":3}}]}'
        mock = _httpx_mock(content=payload, content_type="application/json")
        with patch(
            "geo_copilot.api.routes.proxy.URLValidator.resolve_validated_ip",
            return_value=("93.184.216.34", "mapas2.igac.gov.co"),
        ), patch(
            "geo_copilot.api.routes.proxy.httpx.AsyncClient",
            return_value=mock,
        ):
            resp = client.get(
                "/api/v1/proxy/imagery-identify",
                params={
                    "service": ALLOWED,
                    "geometry": "-74.1,4.6",
                    "geometryType": "esriGeometryPoint",
                    "tolerance": "2",
                    "evil": "DROP TABLE",  # fuera de la allowlist → debe descartarse
                },
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert b"Predios" in resp.content
        # Allowlist: 'evil' NO se reenvió; f=json fijado server-side.
        sent = mock.__aenter__.return_value.get.await_args.kwargs["params"]
        assert "evil" not in sent
        assert sent["f"] == "json"
        assert sent["geometry"] == "-74.1,4.6"
        assert sent["geometryType"] == "esriGeometryPoint"

    def test_host_no_allowlisted_da_400(self, client):
        # IP link-local: URLValidator (real, sin mock) la bloquea sin red.
        resp = client.get(
            "/api/v1/proxy/imagery-identify",
            params={
                "service": "http://169.254.169.254/rest/services/x/ImageServer",
                "geometry": "0,0",
                "geometryType": "esriGeometryPoint",
            },
        )
        assert resp.status_code == 400


class TestProxyContentTypeAllowlist:
    """R0.10 (auditoría 2026-07-26, AUD-07).

    El filtro era `content_type.startswith("image/")`. `image/svg+xml` lo
    cumple y es CONTENIDO ACTIVO (admite <script>): reemitido desde el origen
    de la aplicación —al que el BFF de nginx adjunta la API key— se ejecutaba
    con nuestro origen y podía llamar a /api/v1/* ya autenticado.
    """

    def _pide(self, client, content_type, content=b"\x89PNG\r\n\x1a\n"):
        with patch(
            "geo_copilot.api.routes.proxy.URLValidator.resolve_validated_ip",
            return_value=("93.184.216.34", "mapas2.igac.gov.co"),
        ), patch(
            "geo_copilot.api.routes.proxy.httpx.AsyncClient",
            return_value=_httpx_mock(content_type=content_type, content=content),
        ):
            return client.get(
                "/api/v1/proxy/imagery",
                params={"service": ALLOWED, "endpoint": "exportImage", "bbox": BBOX},
            )

    @pytest.mark.parametrize(
        "content_type",
        [
            "image/svg+xml",
            "image/svg+xml; charset=utf-8",
            "IMAGE/SVG+XML",
            "text/html",
            "application/xhtml+xml",
        ],
    )
    def test_rechaza_contenido_activo_aunque_empiece_por_image(self, client, content_type):
        assert self._pide(client, content_type).status_code == 502

    @pytest.mark.parametrize(
        "content_type", ["image/png", "image/jpeg", "image/webp", "image/png; charset=binary"]
    )
    def test_acepta_los_formatos_raster(self, client, content_type):
        resp = self._pide(client, content_type)
        assert resp.status_code == 200
        # El media_type se normaliza (sin parámetros).
        assert resp.headers["content-type"] in {"image/png", "image/jpeg", "image/webp"}

    def test_emite_cabeceras_que_impiden_ejecucion(self, client):
        resp = self._pide(client, "image/png")
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert "default-src 'none'" in resp.headers["content-security-policy"]
