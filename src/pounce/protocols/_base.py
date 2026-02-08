"""
Protocol contracts — event types and handler interface.

Defines the structural interface that all protocol handlers (H1, H2, WS) must
conform to, and the typed events they produce. The worker layer interacts with
any protocol handler through this interface without knowing which wire protocol
is active.

Sans-I/O: protocol handlers consume bytes and produce bytes. No socket access,
no asyncio imports. The worker feeds data in and reads data out.

"""

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Protocol Events — produced by protocol handlers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RequestReceived:
    """A complete HTTP request head has been parsed.

    Attributes:
        method: HTTP method as bytes (e.g., b"GET").
        target: Request target as bytes (e.g., b"/api/users?page=1").
        headers: Header pairs as a tuple of (name, value) byte pairs.
        http_version: HTTP version string (e.g., "1.1").
    """

    method: bytes
    target: bytes
    headers: tuple[tuple[bytes, bytes], ...]
    http_version: str


@dataclass(frozen=True, slots=True)
class BodyReceived:
    """A chunk of request body data.

    Attributes:
        data: The body bytes for this chunk.
        more: True if more body chunks are expected.
    """

    data: bytes
    more: bool


@dataclass(frozen=True, slots=True)
class ConnectionClosed:
    """The connection has been closed by the client or due to an error.

    Attributes:
        reason: Human-readable reason for the closure.
    """

    reason: str


@dataclass(frozen=True, slots=True)
class Upgraded:
    """The connection has been upgraded to a different protocol.

    Attributes:
        protocol: The protocol being upgraded to (e.g., "websocket", "h2c").
    """

    protocol: str


# ---------------------------------------------------------------------------
# WebSocket Events — produced by WSProtocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WebSocketConnected:
    """WebSocket handshake completed successfully.

    Attributes:
        subprotocol: The negotiated subprotocol (if any).
    """

    subprotocol: str | None


@dataclass(frozen=True, slots=True)
class WebSocketDataReceived:
    """A WebSocket data frame has been received.

    Attributes:
        data: The message payload (bytes for binary, str for text).
    """

    data: bytes | str


@dataclass(frozen=True, slots=True)
class WebSocketDisconnected:
    """The WebSocket connection has been closed.

    Attributes:
        code: The WebSocket close status code.
        reason: Human-readable reason for the closure.
    """

    code: int
    reason: str


type ProtocolEvent = (
    RequestReceived
    | BodyReceived
    | ConnectionClosed
    | Upgraded
    | WebSocketConnected
    | WebSocketDataReceived
    | WebSocketDisconnected
)

# ---------------------------------------------------------------------------
# Protocol Handler — structural interface for all wire protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class ProtocolHandler(Protocol):
    """Sans-I/O contract for HTTP/1.1 wire protocol handlers.

    Defines the request-response cycle interface: parse inbound bytes
    via ``receive_data()``, serialize outbound responses via
    ``send_response()`` and ``send_body()``, and reset via
    ``start_new_cycle()`` for keep-alive connections.

    Implementations:
    - H1Protocol (h11) — pure Python HTTP/1.1
    - H1HttpToolsProtocol (httptools) — C-accelerated HTTP/1.1

    Note: HTTP/2 (``H2Connection``) and WebSocket (``WSProtocol``) have
    fundamentally different interfaces (stream IDs, message framing) and
    do **not** implement this Protocol.  They have their own APIs in
    ``protocols/h2.py`` and ``protocols/ws.py`` respectively.

    """

    def receive_data(self, data: bytes) -> list[ProtocolEvent]:
        """Feed raw bytes from the socket, return parsed protocol events.

        Args:
            data: Raw bytes received from the network.

        Returns:
            List of protocol events parsed from the input.
        """
        ...

    def send_response(self, status: int, headers: list[tuple[bytes, bytes]]) -> bytes:
        """Serialize a response start (status + headers) into bytes.

        Args:
            status: HTTP status code.
            headers: Response headers as (name, value) byte pairs.

        Returns:
            Serialized bytes to write to the socket.
        """
        ...

    def send_body(self, data: bytes, more: bool = False) -> bytes:
        """Serialize a response body chunk into bytes.

        Args:
            data: Body bytes to send.
            more: True if more body chunks will follow.

        Returns:
            Serialized bytes to write to the socket.
        """
        ...

    def start_new_cycle(self) -> None:
        """Reset the protocol handler for a new request on keep-alive.

        Called after a complete request-response cycle to prepare for the
        next request on the same connection.
        """
        ...
