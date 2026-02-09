"""
Request ID generation and propagation.

Each request gets a unique ID for tracing across logs and services.
If a trusted proxy sends X-Request-ID, we honour it.  Otherwise, we
generate a new one.  The ID is injected into:

- The ASGI scope extensions (``scope["extensions"]["request_id"]``)
- The response headers (``X-Request-ID``)

Uses UUID4 for uniqueness without coordination between workers.

"""

import uuid


def generate_request_id() -> str:
    """Generate a unique request ID (UUID4 hex, no dashes)."""
    return uuid.uuid4().hex


def extract_or_generate(
    headers: tuple[tuple[bytes, bytes], ...],
    *,
    trusted: bool,
) -> str:
    """Extract X-Request-ID from headers or generate a new one.

    If the peer is trusted (via proxy headers) and sends X-Request-ID,
    we use that value.  Otherwise, we generate a new UUID4.

    Args:
        headers: Request headers as (name, value) tuples.
        trusted: Whether the direct peer is trusted (proxy).

    Returns:
        A request ID string.

    """
    if trusted:
        for name, value in headers:
            if name.lower() == b"x-request-id":
                incoming = value.decode("latin-1").strip()
                if incoming:
                    return incoming
    return generate_request_id()
