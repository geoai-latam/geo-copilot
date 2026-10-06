"""
URL Validator for SSRF prevention.

This module provides URL validation to prevent Server-Side Request Forgery (SSRF)
attacks by blocking requests to internal networks, localhost, and private IPs.
"""

import ipaddress
import socket
from urllib.parse import urlparse

from geo_copilot.core.logging import get_logger

logger = get_logger(__name__)


class URLValidator:
    """Validates URLs to prevent SSRF attacks."""

    BLOCKED_NETWORKS = [
        ipaddress.ip_network('127.0.0.0/8'),      # Loopback
        ipaddress.ip_network('10.0.0.0/8'),       # Private Class A
        ipaddress.ip_network('172.16.0.0/12'),    # Private Class B
        ipaddress.ip_network('192.168.0.0/16'),   # Private Class C
        ipaddress.ip_network('169.254.0.0/16'),   # Link-local
        ipaddress.ip_network('0.0.0.0/8'),        # Current network
        ipaddress.ip_network('100.64.0.0/10'),    # Carrier-grade NAT
        ipaddress.ip_network('192.0.0.0/24'),     # IETF Protocol Assignments
        ipaddress.ip_network('192.0.2.0/24'),     # TEST-NET-1
        ipaddress.ip_network('198.51.100.0/24'),  # TEST-NET-2
        ipaddress.ip_network('203.0.113.0/24'),   # TEST-NET-3
        ipaddress.ip_network('224.0.0.0/4'),      # Multicast
        ipaddress.ip_network('240.0.0.0/4'),      # Reserved
        ipaddress.ip_network('255.255.255.255/32'),  # Broadcast
        # IPv6
        ipaddress.ip_network('::1/128'),          # IPv6 loopback
        ipaddress.ip_network('fc00::/7'),         # IPv6 private (ULA)
        ipaddress.ip_network('fe80::/10'),        # IPv6 link-local
        ipaddress.ip_network('ff00::/8'),         # IPv6 multicast
    ]

    ALLOWED_SCHEMES = {'http', 'https'}

    # Known safe port ranges for HTTP/HTTPS
    SAFE_PORTS = {80, 443, 8080, 8443, 6443}

    @classmethod
    def resolve_validated_ip(
        cls, url: str, allowed_domains: list[str] | None = None,
    ) -> tuple[str, str]:
        """Resuelve el host UNA vez, valida TODAS sus IPs y devuelve (ip, hostname).

        Cierra el TOCTOU DNS del proxy: en lugar de validar y dejar que el cliente
        HTTP RE-resuelva (pudiendo obtener una IP privada — DNS rebinding), se fija
        la IP validada aquí y el cliente conecta a ESA IP con SNI/Host del hostname
        original. Lanza ``ValueError`` con la razón si algo no valida (#25)."""
        parsed = urlparse(url)
        if parsed.scheme not in cls.ALLOWED_SCHEMES:
            raise ValueError(f"Scheme not allowed: {parsed.scheme}")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("URL has no host")
        if allowed_domains and not any(
            hostname == d or hostname.endswith(f".{d}") for d in allowed_domains
        ):
            raise ValueError(f"Domain not allowed: {hostname}")
        try:
            ips = socket.gethostbyname_ex(hostname)[2]
        except (socket.gaierror, socket.herror) as exc:
            raise ValueError(f"Cannot resolve hostname: {hostname} ({exc})") from exc
        safe: str | None = None
        for ip_str in ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError as exc:
                raise ValueError(f"Invalid IP: {ip_str}") from exc
            target = ip_obj.ipv4_mapped if (
                isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped
            ) else ip_obj
            if any(target in net for net in cls.BLOCKED_NETWORKS):
                # UNA sola IP privada en el conjunto → bloquear TODO (anti-rebinding).
                raise ValueError(f"IP blocked: {ip_str} (private network)")
            if safe is None:
                safe = ip_str
        if safe is None:
            raise ValueError(f"No IP resolved for {hostname}")
        return safe, hostname

    @classmethod
    def validate_url(  # noqa: C901, PLR0912
        cls,
        url: str,
        allowed_domains: list[str] | None = None,
        allow_any_port: bool = True
    ) -> tuple[bool, str]:
        """
        Validate URL before making a request.

        Args:
            url: The URL to validate
            allowed_domains: Optional whitelist of allowed domains
            allow_any_port: If False, only allows standard HTTP/HTTPS ports

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            parsed = urlparse(url)

            # Validate scheme
            if parsed.scheme not in cls.ALLOWED_SCHEMES:
                return False, f"Scheme not allowed: {parsed.scheme}"

            # Validate host exists
            if not parsed.netloc:
                return False, "URL has no host"

            # Extract hostname without port
            hostname = parsed.hostname
            if not hostname:
                return False, "Invalid hostname"

            # Validate port if restricted
            if not allow_any_port:
                port = parsed.port or (443 if parsed.scheme == 'https' else 80)
                if port not in cls.SAFE_PORTS:
                    return False, f"Port not allowed: {port}"

            # Resolve IP and validate it's not private
            try:
                # Get all IPs for the hostname (handles DNS rebinding)
                ips = socket.gethostbyname_ex(hostname)[2]

                for ip_str in ips:
                    try:
                        ip_obj = ipaddress.ip_address(ip_str)

                        # Check for IPv6-mapped IPv4 addresses (::ffff:x.x.x.x)
                        if isinstance(ip_obj, ipaddress.IPv6Address):
                            if ip_obj.ipv4_mapped:
                                # Extract the IPv4 address and check it
                                ipv4_mapped = ip_obj.ipv4_mapped
                                for network in cls.BLOCKED_NETWORKS:
                                    if isinstance(network, ipaddress.IPv4Network):
                                        if ipv4_mapped in network:
                                            logger.warning(
                                                f"SSRF blocked: {url} uses IPv6-mapped IPv4 {ip_str}"
                                            )
                                            return False, f"IP blocked: {ip_str} (IPv6-mapped private IPv4)"

                        for network in cls.BLOCKED_NETWORKS:
                            if ip_obj in network:
                                logger.warning(
                                    f"SSRF blocked: {url} resolves to private IP {ip_str}"
                                )
                                return False, f"IP blocked: {ip_str} (private network)"
                    except ValueError:
                        # Invalid IP address format
                        return False, f"Invalid IP address: {ip_str}"

            except socket.gaierror as e:
                return False, f"Cannot resolve hostname: {hostname} ({e})"
            except socket.herror as e:
                return False, f"Host error: {hostname} ({e})"

            # Validate domain whitelist if provided
            if allowed_domains:
                domain_allowed = any(
                    hostname == d or hostname.endswith(f".{d}")
                    for d in allowed_domains
                )
                if not domain_allowed:
                    return False, f"Domain not allowed: {hostname}"

            return True, ""

        except Exception as e:  # captura amplia a propósito: frontera de seguridad: cualquier fallo inesperado FALLA CERRADO (URL no permitida)
            logger.error(f"Error validating URL {url}: {e}", exc_info=True)
            return False, f"Error validating URL: {e}"

    @classmethod
    def validate_redirect(
        cls,
        original_url: str,
        redirect_url: str,
        allowed_domains: list[str] | None = None
    ) -> tuple[bool, str]:
        """
        Validate that a redirect is safe.

        Args:
            original_url: The original URL that was requested
            redirect_url: The URL being redirected to
            allowed_domains: Optional whitelist of allowed domains

        Returns:
            Tuple of (is_valid, error_or_warning_message)
        """
        # First validate the redirect URL itself
        is_valid, error = cls.validate_url(redirect_url, allowed_domains)
        if not is_valid:
            return False, f"Redirect blocked: {error}"

        # Check if redirect is to a different domain
        try:
            original_host = urlparse(original_url).hostname
            redirect_host = urlparse(redirect_url).hostname

            if redirect_host and original_host:
                # Allow redirects to same domain or subdomain
                if not (redirect_host == original_host or
                        redirect_host.endswith(f".{original_host}") or
                        original_host.endswith(f".{redirect_host}")):
                    # Cross-domain redirect - warn but allow if URL is valid
                    logger.info(
                        f"Cross-domain redirect: {original_host} -> {redirect_host}"
                    )
                    return True, "warning:cross-domain-redirect"

            return True, ""

        except Exception as e:  # captura amplia a propósito: frontera de seguridad: cualquier fallo inesperado FALLA CERRADO (redirect bloqueado)
            logger.error(f"Error validating redirect: {e}", exc_info=True)
            return False, f"Error validating redirect: {e}"

    @classmethod
    def is_safe_url(cls, url: str, allowed_domains: list[str] | None = None) -> bool:
        """
        Simple boolean check if URL is safe.

        Args:
            url: The URL to check
            allowed_domains: Optional whitelist of allowed domains

        Returns:
            True if URL is safe, False otherwise
        """
        is_valid, _ = cls.validate_url(url, allowed_domains)
        return is_valid
