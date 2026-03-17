"""Shared header lookup utilities.

Single-pass header extraction for ASGI headers (list or tuple of
(name, value) byte pairs). Consolidates the 7 copies of _get_header
scattered across worker, sync_worker, async_pool, and handler modules.

"""

from collections.abc import Sequence


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
