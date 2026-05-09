"""Tenant-facing scope matrix across HTTP and WebSocket builders."""

from typing import Any

import pytest

from pounce.asgi.bridge import build_scope
from pounce.asgi.h2_bridge import build_h2_scope
from pounce.asgi.h3_bridge import build_h3_scope
from pounce.asgi.ws_bridge import build_ws_scope
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived

_CLIENT = ("10.0.0.1", 5000)
_SERVER = ("127.0.0.1", 8000)


def _request(
    *,
    target: bytes = b"/forum?tab=latest",
    headers: tuple[tuple[bytes, bytes], ...] = (),
    http_version: str = "1.1",
) -> RequestReceived:
    return RequestReceived(
        method=b"GET",
        target=target,
        headers=((b"host", b"internal:8000"), *headers),
        http_version=http_version,
    )


def _h3_headers(extra: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    return [
        (b":method", b"GET"),
        (b":path", b"/forum?tab=latest"),
        (b":scheme", b"https"),
        (b":authority", b"internal:8000"),
        *extra,
    ]


def _host(scope: dict[str, Any]) -> bytes:
    return dict(scope["headers"])[b"host"]


@pytest.mark.parametrize(
    ("name", "builder", "expected_version", "expected_scheme"),
    [
        (
            "h1",
            lambda config, extra: build_scope(_request(headers=extra), config, _CLIENT, _SERVER),
            "1.1",
            "https",
        ),
        (
            "h2",
            lambda config, extra: build_h2_scope(
                _request(headers=extra, http_version="2"),
                config,
                _CLIENT,
                _SERVER,
            ),
            "2",
            "https",
        ),
        (
            "h3",
            lambda config, extra: build_h3_scope(
                _h3_headers(list(extra)),
                config,
                _CLIENT,
                _SERVER,
            ),
            "3",
            "https",
        ),
        (
            "ws",
            lambda config, extra: build_ws_scope(_request(headers=extra), config, _CLIENT, _SERVER),
            "1.1",
            "wss",
        ),
    ],
)
def test_trusted_proxy_host_scope_for_tenants(
    name: str,
    builder: Any,
    expected_version: str,
    expected_scheme: str,
) -> None:
    """Trusted forwarded authority is consistent for host-routing apps."""
    config = ServerConfig(root_path="/app", trusted_hosts=("10.0.0.1",))
    extra = (
        (b"x-forwarded-for", b"203.0.113.9"),
        (b"x-forwarded-proto", expected_scheme.encode()),
        (b"x-forwarded-host", b"tenant.example:443"),
    )

    scope = builder(config, extra)

    assert name
    assert scope["http_version"] == expected_version
    assert scope["root_path"] == "/app"
    assert scope["path"] == "/forum"
    assert scope["query_string"] == b"tab=latest"
    assert scope["scheme"] == expected_scheme
    assert scope["client"] == ("203.0.113.9", 5000)
    assert scope["server"] == ("tenant.example", 443)
    assert _host(scope) == b"tenant.example:443"


@pytest.mark.parametrize(
    "builder",
    [
        lambda config, extra: build_scope(_request(headers=extra), config, _CLIENT, _SERVER),
        lambda config, extra: build_h2_scope(
            _request(headers=extra, http_version="2"),
            config,
            _CLIENT,
            _SERVER,
        ),
        lambda config, extra: build_h3_scope(_h3_headers(list(extra)), config, _CLIENT, _SERVER),
        lambda config, extra: build_ws_scope(_request(headers=extra), config, _CLIENT, _SERVER),
    ],
)
def test_untrusted_forwarded_host_does_not_cross_tenants(builder: Any) -> None:
    """Untrusted forwarded authority is stripped before the app sees scope."""
    config = ServerConfig(trusted_hosts=("192.0.2.10",))
    extra = (
        (b"x-forwarded-for", b"203.0.113.9"),
        (b"x-forwarded-proto", b"https"),
        (b"x-forwarded-host", b"attacker.example"),
    )

    scope = builder(config, extra)

    header_names = {name for name, _ in scope["headers"]}
    assert b"x-forwarded-for" not in header_names
    assert b"x-forwarded-proto" not in header_names
    assert b"x-forwarded-host" not in header_names
    assert scope["client"] == _CLIENT
    assert scope["server"] == _SERVER
    assert _host(scope) == b"internal:8000"
