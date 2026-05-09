"""
Proxy header validation — extract real client info from trusted reverse proxies.

When ``trusted_hosts`` is configured, X-Forwarded-For/Proto/Host headers are
honoured **only** if the direct peer IP is in the trusted set.  When no trusted
hosts are configured, forwarded headers are stripped to prevent spoofing.

RFC 7239 defines a formal ``Forwarded`` header, but the ``X-Forwarded-*``
family remains the de-facto standard used by nginx, Caddy, AWS ALB, Cloudflare,
and virtually every reverse proxy in production.

"""

from typing import Any

from pounce._headers import strip_crlf

_FORWARDED_PREFIXES = (b"x-forwarded-",)


def apply_proxy_headers(
    scope: dict[str, Any],
    *,
    trusted_hosts: frozenset[str],
) -> dict[str, Any]:
    """Rewrite ASGI scope fields using proxy headers from a trusted peer.

    When the direct peer is trusted:
    - ``client`` is overwritten with the leftmost IP from ``X-Forwarded-For``
    - ``scheme`` is overwritten from ``X-Forwarded-Proto``
    - ``server`` host is overwritten from ``X-Forwarded-Host``
    - ``Host`` is rewritten from ``X-Forwarded-Host`` for downstream routing

    When the direct peer is *not* trusted (or ``trusted_hosts`` is empty),
    all ``X-Forwarded-*`` headers are stripped from the scope to prevent
    downstream apps from trusting spoofed values.

    Args:
        scope: Mutable ASGI scope dict (modified in place and returned).
        trusted_hosts: Tuple of trusted peer IPs/hostnames.  The wildcard
            ``"*"`` trusts all peers (use only behind a known proxy layer).

    Returns:
        The same scope dict, modified.

    """
    if not trusted_hosts:
        _strip_forwarded_headers(scope)
        return scope

    client_host = scope.get("client", ("", 0))[0]
    is_trusted = "*" in trusted_hosts or client_host in trusted_hosts

    if not is_trusted:
        _strip_forwarded_headers(scope)
        return scope

    headers: list[list[bytes]] = scope.get("headers", [])
    forwarded_for: bytes | None = None
    forwarded_proto: bytes | None = None
    forwarded_host: bytes | None = None

    for pair in headers:
        # scope headers are pre-lowered by build_base_scope
        name = pair[0]
        if name == b"x-forwarded-for":
            forwarded_for = pair[1]
        elif name == b"x-forwarded-proto":
            forwarded_proto = pair[1]
        elif name == b"x-forwarded-host":
            forwarded_host = pair[1]

    # X-Forwarded-For: client, proxy1, proxy2 → leftmost is the real client
    if forwarded_for is not None:
        real_ip = strip_crlf(forwarded_for.split(b",")[0].strip().decode("latin-1"))
        if real_ip:
            original_port = scope.get("client", ("", 0))[1]
            scope["client"] = (real_ip, original_port)

    if forwarded_proto is not None:
        proto = forwarded_proto.strip().decode("latin-1").lower()
        if proto in {"http", "https", "ws", "wss"}:
            scope["scheme"] = proto

    if forwarded_host is not None:
        host = strip_crlf(forwarded_host.strip().decode("latin-1"))
        if host:
            original_port = scope.get("server", ("", 0))[1]
            server_host, server_port = _split_host_port(host, original_port)
            scope["server"] = (server_host, server_port)
            scope["headers"] = _replace_header(headers, b"host", host.encode("latin-1"))

    return scope


def _split_host_port(host: str, default_port: int) -> tuple[str, int]:
    """Split a Host-style value into an ASGI server tuple."""
    if host.startswith("["):
        end = host.find("]")
        if end > 0:
            address = host[1:end]
            remainder = host[end + 1 :]
            if remainder.startswith(":") and remainder[1:].isdigit():
                return address, int(remainder[1:])
            return address, default_port

    if host.count(":") == 1:
        name, port = host.rsplit(":", 1)
        if name and port.isdigit():
            return name, int(port)

    return host, default_port


def _replace_header(
    headers: list[list[bytes]],
    name: bytes,
    value: bytes,
) -> list[tuple[bytes, bytes] | list[bytes]]:
    """Replace the first header named *name*, or append it if missing."""
    replaced = False
    updated: list[tuple[bytes, bytes] | list[bytes]] = []
    for pair in headers:
        if pair[0] == name:
            if not replaced:
                updated.append((name, value))
                replaced = True
            continue
        updated.append(pair)
    if not replaced:
        updated.append((name, value))
    return updated


def _strip_forwarded_headers(scope: dict[str, Any]) -> None:
    """Remove all X-Forwarded-* headers from an ASGI scope.

    Prevents untrusted clients from injecting proxy headers that
    downstream ASGI apps might naively trust.

    """
    headers: list[list[bytes]] = scope.get("headers", [])
    # scope headers are pre-lowered by build_base_scope
    scope["headers"] = [pair for pair in headers if not pair[0].startswith(_FORWARDED_PREFIXES)]
