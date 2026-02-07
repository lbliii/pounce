"""
Protocol handlers for HTTP/1.1, HTTP/2, and WebSocket.

Each protocol module translates between raw bytes on the wire and typed
protocol events. Protocol handlers are sans-I/O — they accept bytes and
return bytes, never touching sockets or asyncio directly.

Modules:
- _base: Event types and ProtocolHandler contract
- h1: HTTP/1.1 via h11 (phase 1)
- h2: HTTP/2 via h2 library (phase 3)
- ws: WebSocket via wsproto (phase 3)

"""

from pounce.protocols._base import (
    BodyReceived,
    ConnectionClosed,
    ProtocolEvent,
    ProtocolHandler,
    RequestReceived,
    Upgraded,
    WebSocketConnected,
    WebSocketDataReceived,
    WebSocketDisconnected,
)
from pounce.protocols.h1 import H1Protocol

__all__ = [
    "BodyReceived",
    "ConnectionClosed",
    "H1Protocol",
    "ProtocolEvent",
    "ProtocolHandler",
    "RequestReceived",
    "Upgraded",
    "WebSocketConnected",
    "WebSocketDataReceived",
    "WebSocketDisconnected",
]
