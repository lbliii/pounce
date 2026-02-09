"""Tests for pounce._proxy — proxy header validation and rewriting."""

import pytest

from pounce._proxy import apply_proxy_headers


def _scope(
    *,
    client: tuple[str, int] = ("10.0.0.1", 5000),
    server: tuple[str, int] = ("0.0.0.0", 8000),
    scheme: str = "http",
    headers: list[list[bytes]] | None = None,
) -> dict:
    return {
        "type": "http",
        "client": client,
        "server": server,
        "scheme": scheme,
        "headers": headers if headers is not None else [],
    }


class TestApplyProxyHeaders:
    """apply_proxy_headers() rewrites scope fields from trusted proxies."""

    def test_no_trusted_hosts_strips_forwarded(self):
        """When trusted_hosts is empty, X-Forwarded-* headers are stripped."""
        scope = _scope(headers=[
            [b"host", b"example.com"],
            [b"x-forwarded-for", b"1.2.3.4"],
            [b"x-forwarded-proto", b"https"],
            [b"x-forwarded-host", b"public.example.com"],
        ])
        result = apply_proxy_headers(scope, trusted_hosts=())

        header_names = [h[0] for h in result["headers"]]
        assert b"host" in header_names
        assert b"x-forwarded-for" not in header_names
        assert b"x-forwarded-proto" not in header_names
        assert b"x-forwarded-host" not in header_names
        # Client/scheme/server unchanged
        assert result["client"] == ("10.0.0.1", 5000)
        assert result["scheme"] == "http"

    def test_untrusted_peer_strips_forwarded(self):
        """When the peer IP is not in trusted_hosts, headers are stripped."""
        scope = _scope(
            client=("192.168.1.50", 5000),
            headers=[
                [b"x-forwarded-for", b"1.2.3.4"],
                [b"x-forwarded-proto", b"https"],
            ],
        )
        result = apply_proxy_headers(scope, trusted_hosts=("10.0.0.1",))

        assert result["client"] == ("192.168.1.50", 5000)
        assert result["scheme"] == "http"
        header_names = [h[0] for h in result["headers"]]
        assert b"x-forwarded-for" not in header_names

    def test_trusted_peer_rewrites_client_from_xff(self):
        """Trusted peer: X-Forwarded-For rewrites client IP."""
        scope = _scope(
            client=("10.0.0.1", 5000),
            headers=[[b"x-forwarded-for", b"203.0.113.50, 10.0.0.1"]],
        )
        result = apply_proxy_headers(scope, trusted_hosts=("10.0.0.1",))

        assert result["client"] == ("203.0.113.50", 5000)

    def test_trusted_peer_rewrites_scheme_from_proto(self):
        """Trusted peer: X-Forwarded-Proto rewrites scheme."""
        scope = _scope(
            client=("10.0.0.1", 5000),
            headers=[[b"x-forwarded-proto", b"https"]],
        )
        result = apply_proxy_headers(scope, trusted_hosts=("10.0.0.1",))

        assert result["scheme"] == "https"

    def test_trusted_peer_rewrites_host(self):
        """Trusted peer: X-Forwarded-Host rewrites server host."""
        scope = _scope(
            client=("10.0.0.1", 5000),
            headers=[[b"x-forwarded-host", b"public.example.com"]],
        )
        result = apply_proxy_headers(scope, trusted_hosts=("10.0.0.1",))

        assert result["server"] == ("public.example.com", 8000)

    def test_wildcard_trusts_all_peers(self):
        """trusted_hosts=("*",) trusts any peer."""
        scope = _scope(
            client=("192.168.99.99", 5000),
            headers=[
                [b"x-forwarded-for", b"1.1.1.1"],
                [b"x-forwarded-proto", b"https"],
            ],
        )
        result = apply_proxy_headers(scope, trusted_hosts=("*",))

        assert result["client"] == ("1.1.1.1", 5000)
        assert result["scheme"] == "https"

    def test_invalid_proto_ignored(self):
        """Only http/https/ws/wss are accepted as scheme values."""
        scope = _scope(
            client=("10.0.0.1", 5000),
            headers=[[b"x-forwarded-proto", b"ftp"]],
        )
        result = apply_proxy_headers(scope, trusted_hosts=("10.0.0.1",))

        assert result["scheme"] == "http"  # unchanged

    def test_empty_xff_preserves_client(self):
        """Empty X-Forwarded-For doesn't corrupt the client tuple."""
        scope = _scope(
            client=("10.0.0.1", 5000),
            headers=[[b"x-forwarded-for", b""]],
        )
        result = apply_proxy_headers(scope, trusted_hosts=("10.0.0.1",))

        assert result["client"] == ("10.0.0.1", 5000)

    def test_multiple_trusted_hosts(self):
        """Multiple IPs can be trusted."""
        scope = _scope(
            client=("10.0.0.2", 5000),
            headers=[[b"x-forwarded-for", b"1.2.3.4"]],
        )
        result = apply_proxy_headers(
            scope, trusted_hosts=("10.0.0.1", "10.0.0.2", "10.0.0.3")
        )

        assert result["client"] == ("1.2.3.4", 5000)

    def test_xff_single_ip(self):
        """X-Forwarded-For with a single IP (no proxy chain)."""
        scope = _scope(
            client=("10.0.0.1", 5000),
            headers=[[b"x-forwarded-for", b"203.0.113.1"]],
        )
        result = apply_proxy_headers(scope, trusted_hosts=("10.0.0.1",))

        assert result["client"] == ("203.0.113.1", 5000)

    def test_preserves_non_forwarded_headers(self):
        """Non-forwarded headers are never touched."""
        scope = _scope(
            client=("10.0.0.1", 5000),
            headers=[
                [b"host", b"example.com"],
                [b"accept", b"text/html"],
                [b"x-forwarded-for", b"1.2.3.4"],
            ],
        )
        result = apply_proxy_headers(scope, trusted_hosts=("10.0.0.1",))

        header_names = [h[0] for h in result["headers"]]
        assert b"host" in header_names
        assert b"accept" in header_names
