"""Tests for the WebSocket protocol handler and ASGI bridge."""

import pytest

from pounce.protocols._base import (
    RequestReceived,
    WebSocketConnected,
    WebSocketDataReceived,
    WebSocketDisconnected,
)

# Check if wsproto is available
try:
    import wsproto
    import wsproto.connection
    import wsproto.events

    _HAS_WSPROTO = True
except ImportError:
    _HAS_WSPROTO = False

pytestmark = pytest.mark.skipif(
    not _HAS_WSPROTO,
    reason="wsproto not installed",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ws_upgrade_request() -> RequestReceived:
    """Build a minimal WebSocket upgrade request."""
    return RequestReceived(
        method=b"GET",
        target=b"/ws/chat",
        headers=(
            (b"host", b"localhost:8000"),
            (b"upgrade", b"websocket"),
            (b"connection", b"Upgrade"),
            (b"sec-websocket-key", b"dGhlIHNhbXBsZSBub25jZQ=="),
            (b"sec-websocket-version", b"13"),
        ),
        http_version="1.1",
    )

def _make_client_server():
    """Create a paired client/server for roundtrip tests.

    Returns (client_conn, server_proto). Both are in OPEN state and
    ready to exchange WebSocket frames.
    """
    from pounce.protocols.ws import WSProtocol

    server = WSProtocol()

    # Create a client-side connection
    client = wsproto.connection.Connection(
        wsproto.connection.ConnectionType.CLIENT,
    )

    return client, server

# ---------------------------------------------------------------------------
# WSProtocol tests
# ---------------------------------------------------------------------------

class TestWSProtocol:
    def test_init(self) -> None:
        from pounce.protocols.ws import WSProtocol

        proto = WSProtocol()
        assert not proto.is_closed
        assert proto.subprotocol is None

    def test_init_with_subprotocol(self) -> None:
        from pounce.protocols.ws import WSProtocol

        proto = WSProtocol(subprotocol="graphql-ws")
        assert proto.subprotocol == "graphql-ws"

    def test_send_text_message(self) -> None:
        from pounce.protocols.ws import WSProtocol

        proto = WSProtocol()
        raw = proto.send_message("hello")
        assert isinstance(raw, bytes)
        assert len(raw) > 0

    def test_send_binary_message(self) -> None:
        from pounce.protocols.ws import WSProtocol

        proto = WSProtocol()
        raw = proto.send_message(b"\x00\x01\x02")
        assert isinstance(raw, bytes)
        assert len(raw) > 0

    def test_close_produces_bytes(self) -> None:
        from pounce.protocols.ws import WSProtocol

        proto = WSProtocol()
        raw = proto.close(code=1000, reason="normal")
        assert isinstance(raw, bytes)
        assert len(raw) > 0
        assert proto.is_closed

    def test_close_default_code(self) -> None:
        from pounce.protocols.ws import WSProtocol

        proto = WSProtocol()
        raw = proto.close()
        assert isinstance(raw, bytes)
        assert proto.is_closed

    def test_roundtrip_text_message(self) -> None:
        """Simulate a client sending a text message to the server."""
        client, server = _make_client_server()

        client_bytes = client.send(
            wsproto.events.TextMessage(data="hello from client")
        )

        events = server.receive_data(client_bytes)
        assert len(events) == 1
        assert isinstance(events[0], WebSocketDataReceived)
        assert events[0].data == "hello from client"

    def test_roundtrip_binary_message(self) -> None:
        """Simulate a client sending a binary message."""
        client, server = _make_client_server()

        payload = b"\x00\x01\x02\x03"
        client_bytes = client.send(
            wsproto.events.BytesMessage(data=payload)
        )

        events = server.receive_data(client_bytes)
        assert len(events) == 1
        assert isinstance(events[0], WebSocketDataReceived)
        assert events[0].data == payload

    def test_client_close(self) -> None:
        """Simulate a client-initiated close."""
        client, server = _make_client_server()

        client_bytes = client.send(
            wsproto.events.CloseConnection(code=1000, reason="bye")
        )

        events = server.receive_data(client_bytes)
        assert len(events) == 1
        assert isinstance(events[0], WebSocketDisconnected)
        assert events[0].code == 1000
        assert events[0].reason == "bye"
        assert server.is_closed

    def test_multiple_messages(self) -> None:
        """Client sends multiple messages in sequence."""
        client, server = _make_client_server()

        for i in range(5):
            client_bytes = client.send(
                wsproto.events.TextMessage(data=f"msg-{i}")
            )
            events = server.receive_data(client_bytes)
            assert len(events) == 1
            assert isinstance(events[0], WebSocketDataReceived)
            assert events[0].data == f"msg-{i}"

# ---------------------------------------------------------------------------
# Handshake helpers
# ---------------------------------------------------------------------------

class TestHandshakeHelpers:
    def test_build_ws_accept_key(self) -> None:
        from pounce.protocols.ws import build_ws_accept_key

        # Verify deterministic output for a known key
        key = build_ws_accept_key(b"dGhlIHNhbXBsZSBub25jZQ==")
        assert key == b"IWFl7jb/cQr6GRUcc1Ks8TkMANA="
        # Must be valid base64
        import base64
        base64.b64decode(key)  # Should not raise

    def test_build_101_response(self) -> None:
        from pounce.protocols.ws import build_101_response

        raw = build_101_response(b"dGhlIHNhbXBsZSBub25jZQ==")
        assert b"101 Switching Protocols" in raw
        assert b"Upgrade: websocket" in raw
        assert b"Connection: Upgrade" in raw
        assert b"Sec-WebSocket-Accept:" in raw

    def test_build_101_with_subprotocol(self) -> None:
        from pounce.protocols.ws import build_101_response

        raw = build_101_response(
            b"dGhlIHNhbXBsZSBub25jZQ==",
            subprotocol="graphql-ws",
        )
        assert b"Sec-WebSocket-Protocol: graphql-ws" in raw

class TestWSProtocolAvailability:
    def test_is_wsproto_available(self) -> None:
        from pounce.protocols.ws import is_wsproto_available

        assert is_wsproto_available() is True

# ---------------------------------------------------------------------------
# WebSocket event types
# ---------------------------------------------------------------------------

class TestWSEventTypes:
    def test_ws_connected(self) -> None:
        event = WebSocketConnected(subprotocol="graphql-ws")
        assert event.subprotocol == "graphql-ws"

    def test_ws_connected_no_subprotocol(self) -> None:
        event = WebSocketConnected(subprotocol=None)
        assert event.subprotocol is None

    def test_ws_data_text(self) -> None:
        event = WebSocketDataReceived(data="hello")
        assert event.data == "hello"

    def test_ws_data_binary(self) -> None:
        event = WebSocketDataReceived(data=b"\x00\x01")
        assert event.data == b"\x00\x01"

    def test_ws_disconnected(self) -> None:
        event = WebSocketDisconnected(code=1000, reason="normal")
        assert event.code == 1000
        assert event.reason == "normal"

# ---------------------------------------------------------------------------
# WebSocket ASGI bridge tests
# ---------------------------------------------------------------------------

class TestWSBridge:
    def test_build_ws_scope(self) -> None:
        from pounce.asgi.ws_bridge import build_ws_scope
        from pounce.config import ServerConfig

        request = _ws_upgrade_request()
        config = ServerConfig()
        scope = build_ws_scope(
            request, config,
            client=("127.0.0.1", 54321),
            server=("127.0.0.1", 8000),
        )

        assert scope["type"] == "websocket"
        assert scope["path"] == "/ws/chat"
        assert scope["scheme"] == "ws"
        assert scope["client"] == ("127.0.0.1", 54321)
        assert scope["server"] == ("127.0.0.1", 8000)

    def test_build_ws_scope_with_query(self) -> None:
        from pounce.asgi.ws_bridge import build_ws_scope
        from pounce.config import ServerConfig

        request = RequestReceived(
            method=b"GET",
            target=b"/ws/chat?room=1&token=abc",
            headers=((b"host", b"localhost:8000"),),
            http_version="1.1",
        )
        config = ServerConfig()
        scope = build_ws_scope(
            request, config,
            client=("127.0.0.1", 54321),
            server=("127.0.0.1", 8000),
        )

        assert scope["path"] == "/ws/chat"
        assert scope["query_string"] == b"room=1&token=abc"

    def test_build_ws_scope_with_subprotocols(self) -> None:
        from pounce.asgi.ws_bridge import build_ws_scope
        from pounce.config import ServerConfig

        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            headers=(
                (b"host", b"localhost:8000"),
                (b"sec-websocket-protocol", b"graphql-ws, graphql-transport-ws"),
            ),
            http_version="1.1",
        )
        config = ServerConfig()
        scope = build_ws_scope(
            request, config,
            client=("127.0.0.1", 54321),
            server=("127.0.0.1", 8000),
        )

        assert scope["subprotocols"] == ["graphql-ws", "graphql-transport-ws"]

    def test_build_ws_scope_wss_scheme(self) -> None:
        from pounce.asgi.ws_bridge import build_ws_scope
        from pounce.config import ServerConfig

        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            headers=((b"host", b"localhost:8000"),),
            http_version="1.1",
        )
        config = ServerConfig(ssl_certfile="/path/to/cert.pem")
        scope = build_ws_scope(
            request, config,
            client=("127.0.0.1", 54321),
            server=("127.0.0.1", 8000),
        )

        assert scope["scheme"] == "wss"

class TestIsWebSocketUpgrade:
    """Tests for _is_websocket_upgrade() header detection."""

    def test_valid_upgrade(self):
        from pounce.worker import _is_websocket_upgrade
        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            http_version="1.1",
            headers=(
                (b"Host", b"localhost"),
                (b"Connection", b"Upgrade"),
                (b"Upgrade", b"websocket"),
                (b"Sec-WebSocket-Key", b"dGhlIHNhbXBsZSBub25jZQ=="),
            ),
        )
        assert _is_websocket_upgrade(request) is True

    def test_missing_connection_header(self):
        from pounce.worker import _is_websocket_upgrade
        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            http_version="1.1",
            headers=(
                (b"Host", b"localhost"),
                (b"Upgrade", b"websocket"),
            ),
        )
        assert _is_websocket_upgrade(request) is False

    def test_missing_upgrade_header(self):
        from pounce.worker import _is_websocket_upgrade
        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            http_version="1.1",
            headers=(
                (b"Host", b"localhost"),
                (b"Connection", b"Upgrade"),
            ),
        )
        assert _is_websocket_upgrade(request) is False

    def test_case_insensitive(self):
        from pounce.worker import _is_websocket_upgrade
        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            http_version="1.1",
            headers=(
                (b"connection", b"UPGRADE"),
                (b"UPGRADE", b"WebSocket"),
            ),
        )
        assert _is_websocket_upgrade(request) is True

    def test_normal_http_request(self):
        from pounce.worker import _is_websocket_upgrade
        request = RequestReceived(
            method=b"GET",
            target=b"/api/data",
            http_version="1.1",
            headers=(
                (b"Host", b"localhost"),
                (b"Accept", b"application/json"),
            ),
        )
        assert _is_websocket_upgrade(request) is False

    def test_upgrade_but_not_websocket(self):
        from pounce.worker import _is_websocket_upgrade
        request = RequestReceived(
            method=b"GET",
            target=b"/h2c",
            http_version="1.1",
            headers=(
                (b"Connection", b"Upgrade"),
                (b"Upgrade", b"h2c"),
            ),
        )
        assert _is_websocket_upgrade(request) is False
