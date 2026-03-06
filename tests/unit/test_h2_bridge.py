"""Tests for pounce.asgi.h2_bridge — HTTP/2 scope construction and send callable."""

from pounce.asgi.h2_bridge import build_h2_scope
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived


def _request(
    *,
    method: bytes = b"GET",
    target: bytes = b"/",
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> RequestReceived:
    base_headers = ((b"host", b"localhost"),)
    return RequestReceived(
        method=method,
        target=target,
        headers=base_headers + headers,
        http_version="2",
    )


class TestBuildH2Scope:
    """build_h2_scope() produces a valid ASGI HTTP scope for H2."""

    def test_http_version_is_2(self):
        scope = build_h2_scope(_request(), ServerConfig(), ("127.0.0.1", 5000), ("0.0.0.0", 8000))
        assert scope["http_version"] == "2"

    def test_scheme_https_when_tls(self):
        config = ServerConfig(ssl_certfile="cert.pem", ssl_keyfile="key.pem")
        scope = build_h2_scope(_request(), config, ("127.0.0.1", 5000), ("0.0.0.0", 8000))
        assert scope["scheme"] == "https"

    def test_scheme_http_when_no_tls(self):
        scope = build_h2_scope(_request(), ServerConfig(), ("127.0.0.1", 5000), ("0.0.0.0", 8000))
        assert scope["scheme"] == "http"

    def test_applies_proxy_headers_from_trusted_peer(self):
        """Proxy headers are applied for H2 just like H1."""
        request = _request(
            headers=(
                (b"x-forwarded-for", b"203.0.113.50"),
                (b"x-forwarded-proto", b"https"),
            ),
        )
        config = ServerConfig(trusted_hosts=("10.0.0.1",))
        scope = build_h2_scope(request, config, ("10.0.0.1", 5000), ("0.0.0.0", 8000))
        assert scope["client"] == ("203.0.113.50", 5000)
        assert scope["scheme"] == "https"

    def test_strips_proxy_headers_from_untrusted_peer(self):
        """Untrusted peers get X-Forwarded-* stripped."""
        request = _request(
            headers=((b"x-forwarded-for", b"evil"),),
        )
        config = ServerConfig(trusted_hosts=("10.0.0.1",))
        scope = build_h2_scope(request, config, ("192.168.1.1", 5000), ("0.0.0.0", 8000))
        # Client unchanged
        assert scope["client"] == ("192.168.1.1", 5000)
        # X-Forwarded-For stripped
        header_names = [h[0] for h in scope["headers"]]
        assert b"x-forwarded-for" not in header_names

    def test_no_trusted_hosts_strips_forwarded(self):
        """When trusted_hosts is empty, forwarded headers are stripped."""
        request = _request(
            headers=((b"x-forwarded-for", b"1.2.3.4"),),
        )
        scope = build_h2_scope(request, ServerConfig(), ("10.0.0.1", 5000), ("0.0.0.0", 8000))
        header_names = [h[0] for h in scope["headers"]]
        assert b"x-forwarded-for" not in header_names
