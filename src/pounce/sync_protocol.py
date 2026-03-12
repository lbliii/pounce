"""SyncApp protocol — fused sync request-response path for Pounce.

When a SyncApp is provided, the sync worker calls handle_sync() first.
If it returns a RawResponse, the request is served without asyncio,
ASGI scope, or Request/Response object construction. If it returns None,
the request falls through to the full ASGI path (streaming, WebSocket, etc.).

"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class RawRequest:
    """Raw HTTP request data for the fused sync path.

    Attributes:
        method: HTTP method as bytes (e.g., b"GET").
        path: Raw path bytes (no unquote).
        query_string: Query string as bytes (e.g., b"page=1").
        headers: Header pairs as a tuple of (name, value) byte pairs.
        body: Request body bytes.
        client: Client (host, port) tuple.
        server: Server (host, port) tuple.
        http_version: HTTP version string (e.g., "1.1").
    """

    method: bytes
    path: bytes
    query_string: bytes
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes
    client: tuple[str, int]
    server: tuple[str, int]
    http_version: str


@dataclass(frozen=True, slots=True)
class RawResponse:
    """Raw HTTP response for the fused sync path.

    Attributes:
        status: HTTP status code.
        headers: Header pairs as a tuple of (name, value) byte pairs.
        body: Response body bytes.
    """

    status: int
    headers: tuple[tuple[bytes, bytes], ...]
    body: bytes


@runtime_checkable
class SyncApp(Protocol):
    """Protocol for sync request-response handling.

    When Pounce calls handle_sync(), returns RawResponse for sync handling, or
    None to fall through to the full ASGI path (streaming, WebSocket, etc.).
    """

    def handle_sync(self, request: RawRequest) -> RawResponse | None:
        """Handle a single sync request.

        Returns RawResponse for sync handling, or None to fall through to ASGI.
        """
        ...
