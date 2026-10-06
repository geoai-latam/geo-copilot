"""SEC-01 — IP-pinning contra SSRF por DNS rebinding.

``pinned_client`` resuelve+valida el host UNA vez y fija la IP en el transporte
del cliente httpx; el rebinding queda cerrado porque la conexión va a la IP
validada (con Host+SNI del hostname original), no a lo que el DNS re-resuelva.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from geo_copilot.core.security.safe_http import _PinnedTransport, pinned_client


def _mock_resolve(ips: list[str]):
    """Parchea la resolución DNS de resolve_validated_ip a un set fijo de IPs."""
    return patch(
        "geo_copilot.core.security.url_validator.socket.gethostbyname_ex",
        return_value=("host", [], ips),
    )


class TestPinnedClientValida:
    def test_ip_privada_resuelta_rechaza(self):
        # El DNS devuelve una IP privada → SSRF → ValueError (no se abre client).
        with _mock_resolve(["10.0.0.5"]):
            with pytest.raises(ValueError, match="IP blocked"):
                pinned_client("https://evil.example.com/x")

    def test_metadata_link_local_rechaza(self):
        # 169.254.169.254 (metadata cloud) bloqueado.
        with _mock_resolve(["169.254.169.254"]):
            with pytest.raises(ValueError, match="IP blocked"):
                pinned_client("https://rebind.attacker.com/latest/meta-data")

    def test_rebinding_una_ip_privada_en_el_set_rechaza_todo(self):
        # Anti-rebinding: si UNA de las IPs resueltas es privada, se bloquea todo.
        with _mock_resolve(["93.184.216.34", "127.0.0.1"]):
            with pytest.raises(ValueError, match="IP blocked"):
                pinned_client("https://rebind.attacker.com/x")

    def test_scheme_no_http_rechaza(self):
        with pytest.raises(ValueError, match="Scheme not allowed"):
            pinned_client("ftp://example.com/x")

    def test_puerto_no_estandar_rechaza_por_defecto(self):
        with _mock_resolve(["93.184.216.34"]):
            with pytest.raises(ValueError, match="Port not allowed"):
                pinned_client("https://example.com:22/x")  # allow_any_port=False

    def test_puerto_no_estandar_permitido_con_flag(self):
        with _mock_resolve(["93.184.216.34"]):
            client = pinned_client("https://example.com:9000/x", allow_any_port=True)
            assert isinstance(client, httpx.AsyncClient)

    def test_ip_publica_fija_el_mapa_del_transporte(self):
        with _mock_resolve(["93.184.216.34"]):
            client = pinned_client("https://example.com/x")
        transport = client._transport  # el _PinnedTransport
        assert isinstance(transport, _PinnedTransport)
        assert transport._host_to_ip == {"example.com": "93.184.216.34"}
        assert client.follow_redirects is False  # no seguir a hosts sin pin


class TestTransporteReescribe:
    @pytest.mark.asyncio
    async def test_reescribe_host_a_ip_con_host_y_sni(self):
        transport = _PinnedTransport({"example.com": "93.184.216.34"})
        captured: dict = {}

        async def _fake_super(request):
            captured["url"] = request.url
            captured["host_header"] = request.headers.get("Host")
            captured["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200)

        request = httpx.Request("GET", "https://example.com/path?f=json")
        with patch.object(
            httpx.AsyncHTTPTransport, "handle_async_request",
            new=AsyncMock(side_effect=_fake_super),
        ):
            await transport.handle_async_request(request)

        # Conecta a la IP fijada, pero conserva Host + SNI del hostname original.
        assert captured["url"].host == "93.184.216.34"
        assert captured["host_header"] == "example.com"
        assert captured["sni"] == "example.com"

    @pytest.mark.asyncio
    async def test_host_no_pinneado_no_se_reescribe(self):
        # Un host fuera del mapa (p.ej. un redirect a otro dominio) NO se reescribe.
        transport = _PinnedTransport({"example.com": "93.184.216.34"})
        captured: dict = {}

        async def _fake_super(request):
            captured["url"] = request.url
            return httpx.Response(200)

        request = httpx.Request("GET", "https://other.com/path")
        with patch.object(
            httpx.AsyncHTTPTransport, "handle_async_request",
            new=AsyncMock(side_effect=_fake_super),
        ):
            await transport.handle_async_request(request)

        assert captured["url"].host == "other.com"  # sin reescribir
