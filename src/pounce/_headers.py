"""Shared header lookup utilities.

Single-pass header extraction for ASGI headers (list or tuple of
(name, value) byte pairs). Consolidates the 7 copies of _get_header
scattered across worker, sync_worker, async_pool, and handler modules.

"""

from collections.abc import Sequence

from pounce.protocols._base import RequestReceived


def is_websocket_upgrade(request: RequestReceived) -> bool:
    """Check if the request is a WebSocket upgrade.

    Detects ``Connection: Upgrade`` + ``Upgrade: websocket`` headers.
    Works with both pre-lowered (fast parser) and raw-cased (h11) header names.

    """
    has_upgrade_connection = False
    has_websocket_upgrade = False

    for name, value in request.headers:
        name_lower = name.lower()
        if name_lower == b"connection":
            has_upgrade_connection = b"upgrade" in value.lower()
        elif name_lower == b"upgrade":
            has_websocket_upgrade = value.lower() == b"websocket"
        # Early exit when both headers found
        if has_upgrade_connection and has_websocket_upgrade:
            return True

    return has_upgrade_connection and has_websocket_upgrade


def strip_crlf(value: str) -> str:
    """Remove CR and LF characters from a header value.

    Prevents CRLF injection when incorporating external input (e.g. proxy
    headers, request IDs) into HTTP headers or ASGI scope fields.

    """
    if "\r" in value or "\n" in value:
        return value.replace("\r", "").replace("\n", "")
    return value


def get_header(
    headers: Sequence[tuple[bytes, bytes]],
    name: bytes,
) -> bytes | None:
    """Get a header value by lowercase name.

    Single linear scan — use when only one header is needed.

    Args:
        headers: ASGI headers (list or tuple of (name, value) pairs).
        name: Header name to find (compared case-insensitively).

    Returns:
        Header value as bytes, or None if not found.

    """
    name_lower = name.lower()
    for header_name, header_value in headers:
        if header_name.lower() == name_lower:
            return header_value
    return None
