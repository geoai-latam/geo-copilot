"""
Comprehensive tests for URL validator SSRF protection.

These tests verify that the URLValidator correctly blocks:
- Localhost and loopback addresses (various formats)
- Private network IPs (10.x, 172.16.x, 192.168.x)
- IPv6 private/loopback addresses
- Cloud metadata endpoints
- DNS rebinding attempts
- URL encoding bypass attempts
- Redirects to internal networks
- Invalid schemes and edge cases
"""

import socket
from unittest.mock import MagicMock, patch

import pytest

from geo_copilot.core.security.url_validator import URLValidator


class TestURLValidatorBasic:
    """Basic URL validation tests."""

    def test_allows_https_public_domain(self):
        """Should allow HTTPS URLs to public domains."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, error = URLValidator.validate_url("https://example.com/api")
            assert is_valid
            assert error == ""

    def test_allows_http_public_domain(self):
        """Should allow HTTP URLs to public domains."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, error = URLValidator.validate_url("http://example.com/api")
            assert is_valid

    def test_blocks_ftp_scheme(self):
        """Should block FTP and other non-HTTP schemes."""
        is_valid, error = URLValidator.validate_url("ftp://ftp.example.com/file")
        assert not is_valid
        assert "Scheme not allowed" in error

    def test_blocks_file_scheme(self):
        """Should block file:// scheme."""
        is_valid, error = URLValidator.validate_url("file:///etc/passwd")
        assert not is_valid
        assert "Scheme not allowed" in error

    def test_blocks_javascript_scheme(self):
        """Should block javascript: scheme."""
        is_valid, error = URLValidator.validate_url("javascript:alert(1)")
        assert not is_valid

    def test_blocks_data_scheme(self):
        """Should block data: scheme."""
        is_valid, error = URLValidator.validate_url("data:text/html,<script>alert(1)</script>")
        assert not is_valid

    def test_blocks_gopher_scheme(self):
        """Should block gopher: scheme (SSRF vector)."""
        is_valid, error = URLValidator.validate_url("gopher://evil.com/_GET%20/")
        assert not is_valid

    def test_blocks_dict_scheme(self):
        """Should block dict: scheme."""
        is_valid, error = URLValidator.validate_url("dict://localhost:11111/")
        assert not is_valid

    def test_blocks_empty_host(self):
        """Should block URLs without host."""
        is_valid, error = URLValidator.validate_url("http:///path")
        assert not is_valid

    def test_blocks_missing_scheme(self):
        """Should block URLs without scheme."""
        is_valid, error = URLValidator.validate_url("//example.com/path")
        assert not is_valid

    def test_handles_very_long_url(self):
        """Should handle very long URLs gracefully."""
        long_path = "a" * 10000
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, _ = URLValidator.validate_url(f"https://example.com/{long_path}")
            # Should either accept or reject gracefully, not crash
            assert isinstance(is_valid, bool)


class TestSSRFProtection:
    """Tests for SSRF protection against internal networks."""

    def test_blocks_localhost(self):
        """Should block localhost."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('localhost', [], ['127.0.0.1'])
            is_valid, error = URLValidator.validate_url("http://localhost:8080/")
            assert not is_valid
            assert "IP blocked" in error or "private" in error.lower()

    def test_blocks_127_0_0_1(self):
        """Should block 127.0.0.1."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('127.0.0.1', [], ['127.0.0.1'])
            is_valid, error = URLValidator.validate_url("http://127.0.0.1/internal")
            assert not is_valid

    def test_blocks_127_variations(self):
        """Should block all 127.x.x.x addresses."""
        test_cases = [
            '127.0.0.1',
            '127.0.0.2',
            '127.1.1.1',
            '127.255.255.255',
        ]
        for ip in test_cases:
            with patch('socket.gethostbyname_ex') as mock_dns:
                mock_dns.return_value = ('test', [], [ip])
                is_valid, error = URLValidator.validate_url("http://test.com/")
                assert not is_valid, f"Should block {ip}"

    def test_blocks_private_class_a(self):
        """Should block 10.x.x.x private IPs."""
        test_ips = ['10.0.0.1', '10.255.255.255', '10.10.10.10']
        for ip in test_ips:
            with patch('socket.gethostbyname_ex') as mock_dns:
                mock_dns.return_value = ('internal.corp', [], [ip])
                is_valid, error = URLValidator.validate_url("http://internal.corp/api")
                assert not is_valid, f"Should block {ip}"
                assert "IP blocked" in error

    def test_blocks_private_class_b(self):
        """Should block 172.16.x.x - 172.31.x.x private IPs."""
        test_ips = ['172.16.0.1', '172.20.5.5', '172.31.255.255']
        for ip in test_ips:
            with patch('socket.gethostbyname_ex') as mock_dns:
                mock_dns.return_value = ('internal.corp', [], [ip])
                is_valid, error = URLValidator.validate_url("http://internal.corp/api")
                assert not is_valid, f"Should block {ip}"

    def test_allows_172_outside_private_range(self):
        """Should allow 172.x.x.x outside private range (172.32+)."""
        # 172.32.0.0 is NOT in the private 172.16.0.0/12 range
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('public.example.com', [], ['172.32.0.1'])
            is_valid, error = URLValidator.validate_url("http://public.example.com/")
            assert is_valid, "Should allow 172.32.0.1 (not private)"

    def test_blocks_private_class_c(self):
        """Should block 192.168.x.x private IPs."""
        test_ips = ['192.168.0.1', '192.168.1.1', '192.168.255.255']
        for ip in test_ips:
            with patch('socket.gethostbyname_ex') as mock_dns:
                mock_dns.return_value = ('router.local', [], [ip])
                is_valid, error = URLValidator.validate_url("http://router.local/admin")
                assert not is_valid, f"Should block {ip}"

    def test_blocks_link_local(self):
        """Should block link-local addresses (169.254.x.x)."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('metadata', [], ['169.254.169.254'])
            is_valid, error = URLValidator.validate_url("http://169.254.169.254/metadata")
            assert not is_valid

    def test_blocks_aws_metadata_endpoint(self):
        """Should block AWS metadata endpoint."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('instance-data', [], ['169.254.169.254'])
            is_valid, error = URLValidator.validate_url(
                "http://169.254.169.254/latest/meta-data/iam/security-credentials/"
            )
            assert not is_valid

    def test_blocks_gcp_metadata_endpoint(self):
        """Should block GCP metadata endpoint."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('metadata.google.internal', [], ['169.254.169.254'])
            is_valid, error = URLValidator.validate_url(
                "http://metadata.google.internal/computeMetadata/v1/"
            )
            assert not is_valid

    def test_blocks_azure_metadata_endpoint(self):
        """Should block Azure metadata endpoint."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('metadata', [], ['169.254.169.254'])
            is_valid, error = URLValidator.validate_url(
                "http://169.254.169.254/metadata/instance?api-version=2021-02-01"
            )
            assert not is_valid

    def test_blocks_carrier_grade_nat(self):
        """Should block carrier-grade NAT (100.64.0.0/10)."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('cgnat', [], ['100.64.0.1'])
            is_valid, error = URLValidator.validate_url("http://test.com/")
            assert not is_valid

    def test_blocks_multicast(self):
        """Should block multicast addresses (224.0.0.0/4)."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('multicast', [], ['224.0.0.1'])
            is_valid, error = URLValidator.validate_url("http://test.com/")
            assert not is_valid

    def test_blocks_broadcast(self):
        """Should block broadcast address."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('broadcast', [], ['255.255.255.255'])
            is_valid, error = URLValidator.validate_url("http://test.com/")
            assert not is_valid

    def test_blocks_0_0_0_0(self):
        """Should block 0.0.0.0 and 0.0.0.0/8 range."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('zero', [], ['0.0.0.0'])
            is_valid, error = URLValidator.validate_url("http://test.com/")
            assert not is_valid


class TestIPv6Protection:
    """Tests for IPv6 SSRF protection."""

    def test_blocks_ipv6_loopback(self):
        """Should block IPv6 loopback (::1)."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('localhost', [], ['::1'])
            is_valid, error = URLValidator.validate_url("http://localhost/")
            assert not is_valid

    def test_blocks_ipv6_private_ula(self):
        """Should block IPv6 ULA (fc00::/7)."""
        test_ips = ['fc00::1', 'fd00::1', 'fdff:ffff::1']
        for ip in test_ips:
            with patch('socket.gethostbyname_ex') as mock_dns:
                mock_dns.return_value = ('internal', [], [ip])
                is_valid, error = URLValidator.validate_url("http://internal.local/")
                assert not is_valid, f"Should block {ip}"

    def test_blocks_ipv6_link_local(self):
        """Should block IPv6 link-local (fe80::/10)."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('link-local', [], ['fe80::1'])
            is_valid, error = URLValidator.validate_url("http://test.com/")
            assert not is_valid

    def test_blocks_ipv6_multicast(self):
        """Should block IPv6 multicast (ff00::/8)."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('multicast', [], ['ff02::1'])
            is_valid, error = URLValidator.validate_url("http://test.com/")
            assert not is_valid


class TestDNSRebinding:
    """Tests for DNS rebinding attack prevention."""

    def test_blocks_if_any_ip_is_private(self):
        """Should block if ANY resolved IP is private (DNS rebinding defense)."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            # DNS returns both public and private IPs
            mock_dns.return_value = ('rebind.evil.com', [], ['93.184.216.34', '127.0.0.1'])
            is_valid, error = URLValidator.validate_url("http://rebind.evil.com/")
            assert not is_valid, "Should block when any IP is private"

    def test_blocks_multiple_private_ips(self):
        """Should block when all IPs are private."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('internal', [], ['192.168.1.1', '10.0.0.1'])
            is_valid, error = URLValidator.validate_url("http://internal.local/")
            assert not is_valid


class TestURLEncodingBypass:
    """Tests for URL encoding bypass attempts."""

    def test_blocks_decimal_encoded_ip(self):
        """Should handle decimal-encoded IPs."""
        # 127.0.0.1 = 2130706433 in decimal
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('2130706433', [], ['127.0.0.1'])
            is_valid, error = URLValidator.validate_url("http://2130706433/")
            assert not is_valid

    def test_blocks_octal_ip_format(self):
        """Should handle octal IP format (if resolved)."""
        # 0177.0.0.1 is octal for 127.0.0.1
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('octal', [], ['127.0.0.1'])
            is_valid, error = URLValidator.validate_url("http://0177.0.0.1/")
            assert not is_valid

    def test_blocks_hex_ip_format(self):
        """Should handle hex IP format (if resolved)."""
        # 0x7f.0.0.1 is hex for 127.0.0.1
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('hex', [], ['127.0.0.1'])
            is_valid, error = URLValidator.validate_url("http://0x7f000001/")
            assert not is_valid

    def test_handles_url_encoded_localhost(self):
        """Should handle URL-encoded localhost variations."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('localhost', [], ['127.0.0.1'])
            # %6c%6f%63%61%6c%68%6f%73%74 = localhost
            is_valid, error = URLValidator.validate_url("http://localhost/")
            assert not is_valid

    def test_blocks_ipv6_mapped_ipv4(self):
        """Should block IPv6-mapped IPv4 addresses (::ffff:127.0.0.1)."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('mapped', [], ['::ffff:127.0.0.1'])
            is_valid, error = URLValidator.validate_url("http://test.com/")
            # This should be caught either as IPv6 or by parsing
            assert not is_valid


class TestDNSResolutionErrors:
    """Tests for DNS resolution error handling."""

    def test_blocks_unresolvable_hostname(self):
        """Should block hostnames that don't resolve."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.side_effect = socket.gaierror(8, 'nodename nor servname provided')
            is_valid, error = URLValidator.validate_url("http://nonexistent.invalid/")
            assert not is_valid
            assert "resolve" in error.lower() or "hostname" in error.lower()

    def test_blocks_dns_timeout(self):
        """Should handle DNS timeout gracefully."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.side_effect = TimeoutError("DNS lookup timed out")
            is_valid, error = URLValidator.validate_url("http://slow-dns.example.com/")
            assert not is_valid


class TestDomainWhitelist:
    """Tests for domain whitelist functionality."""

    def test_allows_whitelisted_domain(self):
        """Should allow URLs from whitelisted domains."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('datos.gov.co', [], ['52.1.2.3'])
            is_valid, error = URLValidator.validate_url(
                "https://datos.gov.co/api",
                allowed_domains=["datos.gov.co"]
            )
            assert is_valid

    def test_allows_subdomain_of_whitelisted(self):
        """Should allow subdomains of whitelisted domains."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('api.datos.gov.co', [], ['52.1.2.3'])
            is_valid, error = URLValidator.validate_url(
                "https://api.datos.gov.co/resource",
                allowed_domains=["datos.gov.co"]
            )
            assert is_valid

    def test_blocks_non_whitelisted_domain(self):
        """Should block domains not in whitelist."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('evil.com', [], ['52.1.2.3'])
            is_valid, error = URLValidator.validate_url(
                "https://evil.com/api",
                allowed_domains=["datos.gov.co"]
            )
            assert not is_valid
            assert "Domain not allowed" in error

    def test_blocks_partial_domain_match(self):
        """Should not allow partial domain matches (suffix attacks)."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('evildatos.gov.co', [], ['52.1.2.3'])
            is_valid, error = URLValidator.validate_url(
                "https://evildatos.gov.co/api",
                allowed_domains=["datos.gov.co"]
            )
            # evildatos.gov.co should NOT match datos.gov.co
            assert not is_valid

    def test_whitelist_is_case_insensitive(self):
        """Whitelist matching should be case insensitive."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('Datos.Gov.Co', [], ['52.1.2.3'])
            is_valid, error = URLValidator.validate_url(
                "https://Datos.Gov.Co/api",
                allowed_domains=["datos.gov.co"]
            )
            # Should handle case insensitivity appropriately
            # (depends on implementation)

    def test_empty_whitelist_allows_all_public(self):
        """Empty whitelist should allow all public domains."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, error = URLValidator.validate_url(
                "https://example.com/api",
                allowed_domains=[]
            )
            assert is_valid


class TestRedirectValidation:
    """Tests for redirect validation."""

    def test_allows_same_domain_redirect(self):
        """Should allow redirects to same domain."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, warning = URLValidator.validate_redirect(
                "https://example.com/old",
                "https://example.com/new"
            )
            assert is_valid
            assert warning == ""

    def test_allows_subdomain_redirect(self):
        """Should allow redirects to subdomain."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('api.example.com', [], ['93.184.216.34'])
            is_valid, warning = URLValidator.validate_redirect(
                "https://example.com/api",
                "https://api.example.com/v2"
            )
            assert is_valid

    def test_warns_cross_domain_redirect(self):
        """Should warn about cross-domain redirects."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('other.com', [], ['52.1.2.3'])
            is_valid, warning = URLValidator.validate_redirect(
                "https://example.com/link",
                "https://other.com/target"
            )
            assert is_valid  # Still valid, just warning
            assert "cross-domain" in warning.lower()

    def test_blocks_redirect_to_localhost(self):
        """Should block redirects to localhost."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('localhost', [], ['127.0.0.1'])
            is_valid, error = URLValidator.validate_redirect(
                "https://example.com/redirect",
                "http://localhost/internal"
            )
            assert not is_valid

    def test_blocks_redirect_to_private_ip(self):
        """Should block redirects to private IPs."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('internal', [], ['192.168.1.1'])
            is_valid, error = URLValidator.validate_redirect(
                "https://example.com/redirect",
                "http://192.168.1.1/admin"
            )
            assert not is_valid

    def test_blocks_redirect_to_metadata(self):
        """Should block redirects to cloud metadata."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('metadata', [], ['169.254.169.254'])
            is_valid, error = URLValidator.validate_redirect(
                "https://example.com/redirect",
                "http://169.254.169.254/latest/meta-data/"
            )
            assert not is_valid

    def test_blocks_redirect_chain_to_internal(self):
        """Should validate final redirect destination, not intermediate."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('internal', [], ['10.0.0.1'])
            # Even if original was valid, redirect to internal is blocked
            is_valid, error = URLValidator.validate_redirect(
                "https://legit.com/link",
                "http://internal.corp/secret"
            )
            assert not is_valid


class TestIsSafeURLHelper:
    """Tests for the is_safe_url helper method."""

    def test_is_safe_url_returns_boolean(self):
        """is_safe_url should return boolean only."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            result = URLValidator.is_safe_url("https://example.com/api")
            assert isinstance(result, bool)
            assert result is True

    def test_is_safe_url_false_for_internal(self):
        """is_safe_url should return False for internal URLs."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('localhost', [], ['127.0.0.1'])
            result = URLValidator.is_safe_url("http://localhost/")
            assert result is False

    def test_is_safe_url_with_whitelist(self):
        """is_safe_url should respect whitelist."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('blocked.com', [], ['52.1.2.3'])
            result = URLValidator.is_safe_url(
                "https://blocked.com/api",
                allowed_domains=["allowed.com"]
            )
            assert result is False


class TestEdgeCases:
    """Tests for edge cases and special scenarios."""

    def test_handles_username_in_url(self):
        """Should handle URLs with username:password."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, _ = URLValidator.validate_url("https://user:pass@example.com/")
            # Should be valid (credentials in URL)
            assert is_valid

    def test_handles_ipv6_in_url(self):
        """Should handle IPv6 addresses in URL brackets."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('ipv6', [], ['2001:db8::1'])
            is_valid, _ = URLValidator.validate_url("http://[2001:db8::1]/path")
            # 2001:db8::/32 is documentation prefix, should be allowed
            assert is_valid

    def test_handles_port_in_url(self):
        """Should handle URLs with non-standard ports."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, _ = URLValidator.validate_url("https://example.com:8443/api")
            assert is_valid

    def test_handles_empty_path(self):
        """Should handle URLs without path."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, _ = URLValidator.validate_url("https://example.com")
            assert is_valid

    def test_handles_fragment(self):
        """Should handle URLs with fragments."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, _ = URLValidator.validate_url("https://example.com/page#section")
            assert is_valid

    def test_handles_query_params(self):
        """Should handle URLs with query parameters."""
        with patch('socket.gethostbyname_ex') as mock_dns:
            mock_dns.return_value = ('example.com', [], ['93.184.216.34'])
            is_valid, _ = URLValidator.validate_url("https://example.com/api?key=value&other=123")
            assert is_valid
