"""
Shared scope-building logic for HTTP and WebSocket ASGI bridges.

Extracts the common target parsing, path decoding, and header
conversion used by all three bridges (HTTP/1.1, HTTP/2, WebSocket).

"""

from typing import Any
from urllib.parse import unquote

from pounce.protocols._base import RequestReceived

_ASGI_VERSION: dict[str, str] = {"version": "3.0", "spec_version": "2.4"}


def build_base_scope(
    request: RequestReceived,
    *,
    scope_type: str,
    http_version: str,
    scheme: str,
    server: tuple[str, int],
    client: tuple[str, int],
    root_path: str,
    extensions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the common ASGI scope fields shared across all protocols.

    Args:
        request: Parsed HTTP request head.
        scope_type: ASGI scope type (``"http"`` or ``"websocket"``).
        http_version: Protocol version string (``"1.1"``, ``"2"``).
        scheme: URL scheme (``"http"``, ``"https"``, ``"ws"``, ``"wss"``).
        server: Local ``(host, port)`` tuple.
        client: Remote ``(host, port)`` tuple.
        root_path: ASGI root_path for reverse proxy setups.
        extensions: Optional ASGI extensions dict.

    Returns:
        ASGI scope dict ready for protocol-specific additions.

    """
    # Split bytes target once — derive both raw_path and decoded path/query
    raw_target = request.target
    qmark = raw_target.find(b"?")
    if qmark >= 0:
        raw_path = raw_target[:qmark]
        query_string = raw_target[qmark + 1 :]
        path = unquote(raw_path.decode("ascii", errors="replace"))
    else:
        raw_path = raw_target
        query_string = b""
        path = unquote(raw_target.decode("ascii", errors="replace"))

    headers = tuple((name.lower(), value) for name, value in request.headers)

    scope: dict[str, Any] = {
        "type": scope_type,
        "asgi": _ASGI_VERSION,
        "http_version": http_version,
        "method": request.method.decode("ascii"),
        "path": path,
        "raw_path": raw_path,
        "query_string": query_string,
        "root_path": root_path,
        "scheme": scheme,
        "server": server,
        "client": client,
        "headers": headers,
    }
    if extensions:
        scope["extensions"] = extensions
    return scope
