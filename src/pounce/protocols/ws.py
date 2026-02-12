"""
WebSocket protocol handler — sans-I/O wrapper around wsproto.

Translates between raw bytes and typed WebSocket events. The worker feeds
bytes in via receive_data() and reads serialized bytes out via
send_message() and close().

Requires the ``wsproto`` optional dependency (``pip install pounce[ws]``).

Architecture: The HTTP upgrade handshake (101 Switching Protocols) is
handled by the H1 layer. WSProtocol only handles WebSocket framing
*after* the upgrade is complete. wsproto 1.x server connections start
in OPEN state — no separate accept step in wsproto itself.

All state is per-connection. No shared mutable state.

"""

import base64
import hashlib
import logging

from pounce.protocols._base import (
    ProtocolEvent,
    WebSocketDataReceived,
    WebSocketDisconnected,
)

try:
    import wsproto
    import wsproto.connection
    import wsproto.events
    import wsproto.extensions

    _HAS_WSPROTO = True
except ImportError:
    _HAS_WSPROTO = False

logger = logging.getLogger("pounce.protocols.ws")

# WebSocket magic GUID (RFC 6455 Section 4.2.2)
_WS_MAGIC = b"258EAFA5-E914-47DA-95CA-5AB5353BE70A"


def is_wsproto_available() -> bool:
    """Check if wsproto is installed."""
    return _HAS_WSPROTO


def build_ws_accept_key(ws_key: bytes) -> bytes:
    """Compute the Sec-WebSocket-Accept value for the handshake.

    Args:
        ws_key: The Sec-WebSocket-Key from the client's request.

    Returns:
        Base64-encoded accept key for the 101 response.

    """
    digest = hashlib.sha1(ws_key.strip() + _WS_MAGIC).digest()
    return base64.b64encode(digest)


def build_101_response(
    ws_key: bytes,
    *,
    subprotocol: str | None = None,
    extensions: str | None = None,
) -> bytes:
    """Build the raw HTTP 101 Switching Protocols response.

    This is the WebSocket handshake response sent by the server before
    switching to WebSocket framing. Constructed manually because the H1
    protocol handler may not support 101 responses cleanly.

    Args:
        ws_key: The Sec-WebSocket-Key from the client.
        subprotocol: Optional negotiated subprotocol.
        extensions: Optional Sec-WebSocket-Extensions value (e.g., "permessage-deflate").

    Returns:
        Raw HTTP response bytes.

    """
    accept_key = build_ws_accept_key(ws_key)
    lines = [
        b"HTTP/1.1 101 Switching Protocols",
        b"Upgrade: websocket",
        b"Connection: Upgrade",
        b"Sec-WebSocket-Accept: " + accept_key,
    ]
    if subprotocol:
        lines.append(b"Sec-WebSocket-Protocol: " + subprotocol.encode("ascii"))
    if extensions:
        lines.append(b"Sec-WebSocket-Extensions: " + extensions.encode("ascii"))
    lines.append(b"")
    lines.append(b"")
    return b"\r\n".join(lines)


class WSProtocol:
    """WebSocket protocol handler backed by wsproto.

    Sans-I/O: accepts raw bytes, produces raw bytes. The worker manages
    the actual socket I/O.

    This handler manages WebSocket framing *after* the HTTP upgrade
    handshake. Use ``build_101_response()`` to generate the handshake
    response separately.

    In wsproto 1.x the server connection starts in OPEN state — the
    HTTP handshake is not managed by wsproto.

    Args:
        subprotocol: The negotiated subprotocol (if any).
        enable_compression: Enable permessage-deflate compression (default: False).

    Raises:
        RuntimeError: If wsproto is not installed.

    """

    __slots__ = ("_closed", "_conn", "_subprotocol", "_extensions_response")

    def __init__(
        self,
        *,
        subprotocol: str | None = None,
        enable_compression: bool = False,
    ) -> None:
        if not _HAS_WSPROTO:
            raise RuntimeError(
                "WebSocket support requires wsproto. Install with: pip install pounce[ws]"
            )

        # Configure extensions if compression is enabled
        extensions_list: list[Any] = []
        if enable_compression:
            extensions_list.append(wsproto.extensions.PerMessageDeflate())

        # Server-side connection — starts in OPEN state in wsproto 1.x
        self._conn = wsproto.connection.Connection(
            wsproto.connection.ConnectionType.SERVER,
            extensions=extensions_list if extensions_list else None,
        )
        self._subprotocol = subprotocol

        # Build the Sec-WebSocket-Extensions response header value
        self._extensions_response: str | None = None
        if enable_compression:
            self._extensions_response = "permessage-deflate"

        self._closed = False

    # -- Protocol interface -------------------------------------------------

    def receive_data(self, data: bytes) -> tuple[list[ProtocolEvent], bytes]:
        """Feed raw bytes from the socket, return WebSocket events and outbound data.

        Args:
            data: Raw WebSocket frame bytes from the network.

        Returns:
            Tuple of (protocol_events, outbound_bytes). The outbound bytes
            include pong responses and other protocol-level frames that must
            be written back to the socket.

        """
        self._conn.receive_data(data)
        events: list[ProtocolEvent] = []
        outbound_parts: list[bytes] = []

        for ws_event in self._conn.events():
            if isinstance(ws_event, wsproto.events.TextMessage):
                events.append(WebSocketDataReceived(data=ws_event.data))
            elif isinstance(ws_event, wsproto.events.BytesMessage):
                events.append(WebSocketDataReceived(data=bytes(ws_event.data)))

            elif isinstance(ws_event, wsproto.events.CloseConnection):
                code = ws_event.code or 1000
                reason = ws_event.reason or ""
                self._closed = True
                events.append(WebSocketDisconnected(code=code, reason=reason))

            elif isinstance(ws_event, wsproto.events.Ping):
                # Generate and capture the pong response bytes
                pong = wsproto.events.Pong(payload=ws_event.payload)
                outbound_parts.append(self._conn.send(pong))

        outbound = b"".join(outbound_parts) if outbound_parts else b""
        return events, outbound

    # -- Send methods -------------------------------------------------------

    def send_message(self, data: bytes | str) -> bytes:
        """Serialize a WebSocket message frame.

        Args:
            data: The message payload (bytes for binary, str for text).

        Returns:
            Serialized WebSocket frame bytes.

        """
        if isinstance(data, str):
            event = wsproto.events.TextMessage(data=data)
        else:
            event = wsproto.events.BytesMessage(data=data)
        return self._conn.send(event)

    def close(self, code: int = 1000, reason: str = "") -> bytes:
        """Serialize a WebSocket close frame.

        Args:
            code: Close status code (default: 1000 Normal Closure).
            reason: Optional human-readable close reason.

        Returns:
            Serialized WebSocket close frame bytes.

        """
        self._closed = True
        event = wsproto.events.CloseConnection(code=code, reason=reason)
        return self._conn.send(event)

    # -- Properties ---------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        """True if the WebSocket connection has been closed."""
        return self._closed

    @property
    def subprotocol(self) -> str | None:
        """The negotiated subprotocol (if any)."""
        return self._subprotocol

    @property
    def extensions_response(self) -> str | None:
        """The Sec-WebSocket-Extensions header value for the 101 response (if any)."""
        return self._extensions_response
