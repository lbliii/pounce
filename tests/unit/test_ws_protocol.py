"""Tests for the WebSocket protocol handler and ASGI bridge."""

import asyncio
import logging
from typing import Any, cast

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

        client_bytes = client.send(wsproto.events.TextMessage(data="hello from client"))

        events, _outbound = server.receive_data(client_bytes)
        assert len(events) == 1
        assert isinstance(events[0], WebSocketDataReceived)
        assert events[0].data == "hello from client"

    def test_roundtrip_binary_message(self) -> None:
        """Simulate a client sending a binary message."""
        client, server = _make_client_server()

        payload = b"\x00\x01\x02\x03"
        client_bytes = client.send(wsproto.events.BytesMessage(data=payload))

        events, _outbound = server.receive_data(client_bytes)
        assert len(events) == 1
        assert isinstance(events[0], WebSocketDataReceived)
        assert events[0].data == payload

    def test_client_close(self) -> None:
        """Simulate a client-initiated close."""
        client, server = _make_client_server()

        client_bytes = client.send(wsproto.events.CloseConnection(code=1000, reason="bye"))

        events, _outbound = server.receive_data(client_bytes)
        assert len(events) == 1
        assert isinstance(events[0], WebSocketDisconnected)
        assert events[0].code == 1000
        assert events[0].reason == "bye"
        assert server.is_closed

    def test_multiple_messages(self) -> None:
        """Client sends multiple messages in sequence."""
        client, server = _make_client_server()

        for i in range(5):
            client_bytes = client.send(wsproto.events.TextMessage(data=f"msg-{i}"))
            events, _outbound = server.receive_data(client_bytes)
            assert len(events) == 1
            assert isinstance(events[0], WebSocketDataReceived)
            assert events[0].data == f"msg-{i}"

    def test_ping_generates_pong(self) -> None:
        """Receiving a ping produces outbound pong bytes."""
        client, server = _make_client_server()

        client_bytes = client.send(wsproto.events.Ping(payload=b"ping-payload"))

        events, outbound = server.receive_data(client_bytes)
        assert len(events) == 0  # Pings are handled internally
        assert len(outbound) > 0  # Pong response generated


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
            request,
            config,
            client=("127.0.0.1", 54321),
            server=("127.0.0.1", 8000),
        )

        assert scope["type"] == "websocket"
        assert scope["path"] == "/ws/chat"
        assert scope["scheme"] == "ws"
        assert scope["client"] == ("127.0.0.1", 54321)
        assert scope["server"] == ("127.0.0.1", 8000)
        # ASGI WebSocket scope (spec 2.4) is HTTP-method-free (#118) — the
        # extra ``method`` key breaks Litestar WS routing.
        assert "method" not in scope

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
            request,
            config,
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
            request,
            config,
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
        config = ServerConfig(
            ssl_certfile="/path/to/cert.pem",
            ssl_keyfile="/path/to/key.pem",
        )
        scope = build_ws_scope(
            request,
            config,
            client=("127.0.0.1", 54321),
            server=("127.0.0.1", 8000),
        )

        assert scope["scheme"] == "wss"

    def test_build_ws_scope_injects_state(self) -> None:
        from pounce.asgi.ws_bridge import build_ws_scope
        from pounce.config import ServerConfig

        state = {"tenant_registry": object()}
        scope = build_ws_scope(
            _ws_upgrade_request(),
            ServerConfig(),
            client=("127.0.0.1", 54321),
            server=("127.0.0.1", 8000),
            state=state,
        )

        assert scope["state"] is state


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


# ---------------------------------------------------------------------------
# WebSocket rejection serialization — CRLF-injection guard (#114)
# ---------------------------------------------------------------------------


class _FakeWSWriter:
    """Minimal asyncio.StreamWriter stand-in capturing written bytes."""

    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, raw: bytes) -> None:
        self.data.extend(raw)

    async def drain(self) -> None:
        return None


class TestWSRejectionSanitization:
    """websocket.http.response.start must not allow CRLF/status injection."""

    async def _reject(self, message: dict) -> bytes:
        from pounce.asgi.ws_bridge import create_ws_send
        from pounce.protocols.ws import WSProtocol

        writer = _FakeWSWriter()
        send = create_ws_send(
            writer,  # type: ignore[arg-type]
            WSProtocol(),
            b"dGhlIHNhbXBsZSBub25jZQ==",
            accept_event=asyncio.Event(),
            close_event=asyncio.Event(),
        )
        await send(message)
        return bytes(writer.data)

    async def test_crlf_in_header_value_not_injected(self) -> None:
        """A CRLF-laced rejection header value cannot split the response."""
        raw = await self._reject(
            {
                "type": "websocket.http.response.start",
                "status": 403,
                "headers": [
                    (b"x-tenant", b"acme\r\nX-Injected: 1"),
                ],
            }
        )
        # The injected pseudo-header must not appear as its own line: the
        # CRLF is stripped so it collapses into the x-tenant value.
        assert b"\r\nX-Injected: 1" not in raw
        assert b"x-tenant: acmeX-Injected: 1\r\n" in raw

    async def test_crlf_in_header_name_not_injected(self) -> None:
        """A CRLF-laced rejection header name is stripped."""
        raw = await self._reject(
            {
                "type": "websocket.http.response.start",
                "status": 403,
                "headers": [
                    (b"x-evil\r\nX-Injected", b"1"),
                ],
            }
        )
        assert b"\r\nX-Injected: 1\r\n" not in raw
        assert b"x-evilX-Injected: 1\r\n" in raw

    async def test_non_int_status_cannot_inject(self) -> None:
        """A non-int status cannot inject into the status line."""
        raw = await self._reject(
            {
                "type": "websocket.http.response.start",
                "status": "403\r\nX-Injected: 1",
                "headers": [],
            }
        )
        # Falls back to 403 and never emits the injected line.
        assert b"X-Injected: 1" not in raw
        assert raw.startswith(b"HTTP/1.1 403 Rejected\r\n")

    async def test_string_headers_are_sanitized(self) -> None:
        """str header values are encoded and still CRLF-stripped."""
        raw = await self._reject(
            {
                "type": "websocket.http.response.start",
                "status": 401,
                "headers": [
                    ("x-origin", "evil\r\nSet-Cookie: pwned=1"),
                ],
            }
        )
        # No standalone injected Set-Cookie line — CRLF was stripped.
        assert b"\r\nSet-Cookie: pwned=1" not in raw
        assert b"x-origin: evilSet-Cookie: pwned=1\r\n" in raw
        assert raw.startswith(b"HTTP/1.1 401 Rejected\r\n")


# ---------------------------------------------------------------------------
# Exactly-one websocket.disconnect on clean client close (#117)
# ---------------------------------------------------------------------------


class _FakeReader:
    """Yields queued chunks, then EOF (b"")."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)

    async def read(self, _n: int) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class TestWSCleanCloseDisconnect:
    """A clean client close yields exactly one disconnect with its code."""

    async def test_single_disconnect_on_clean_close(self) -> None:
        import wsproto.connection
        import wsproto.events

        from pounce._ws_handler import handle_websocket
        from pounce.config import ServerConfig

        # Build a client close frame (code 1000) the server will read.
        client = wsproto.connection.Connection(
            wsproto.connection.ConnectionType.CLIENT,
        )
        close_bytes = client.send(wsproto.events.CloseConnection(code=1000, reason="bye"))

        received: list[dict] = []

        async def app(scope: dict, receive, send) -> None:
            # Connect → accept → keep draining the receive queue. We do NOT
            # return on the first disconnect: a buggy server enqueues a SECOND
            # spurious 1006 after a clean close, so we drain until the queue is
            # idle (short timeout) to observe every delivered message.
            assert (await receive())["type"] == "websocket.connect"
            await send({"type": "websocket.accept"})
            while True:
                try:
                    msg = await asyncio.wait_for(receive(), timeout=0.25)
                except TimeoutError:
                    return
                received.append(msg)

        request = _ws_upgrade_request()
        writer = _FakeWSWriter()
        reader = _FakeReader([close_bytes])

        await asyncio.wait_for(
            handle_websocket(
                cast(Any, app),
                ServerConfig(access_log=False),
                logging.getLogger("test-ws"),
                request,
                reader,  # type: ignore[arg-type]
                writer,  # type: ignore[arg-type]
                client=("127.0.0.1", 54321),
                server=("127.0.0.1", 8000),
                client_str="127.0.0.1:54321",
            ),
            timeout=5.0,
        )

        disconnects = [m for m in received if m["type"] == "websocket.disconnect"]
        assert len(disconnects) == 1
        assert disconnects[0]["code"] == 1000

    @pytest.mark.issue(242)
    async def test_established_websocket_ignores_http_keep_alive_timeout(self) -> None:
        """A quiet accepted WebSocket remains active until app/peer close."""
        from pounce._ws_handler import handle_websocket
        from pounce.config import ServerConfig

        class BlockingReader:
            async def read(self, _n: int) -> bytes:
                await asyncio.Event().wait()
                return b""

        async def app(scope: dict, receive, send) -> None:
            assert (await receive())["type"] == "websocket.connect"
            await send({"type": "websocket.accept"})
            await asyncio.sleep(0.03)
            await send({"type": "websocket.close", "code": 1000})

        writer = _FakeWSWriter()
        await asyncio.wait_for(
            handle_websocket(
                cast(Any, app),
                ServerConfig(
                    access_log=False,
                    keep_alive_timeout=0.01,
                ),
                logging.getLogger("test-ws"),
                _ws_upgrade_request(),
                cast(Any, BlockingReader()),
                cast(Any, writer),
                client=("127.0.0.1", 54321),
                server=("127.0.0.1", 8000),
                client_str="127.0.0.1:54321",
            ),
            timeout=0.2,
        )

        head, frames = bytes(writer.data).split(b"\r\n\r\n", 1)
        assert b"101 Switching Protocols" in head
        assert frames, "app close frame was lost to HTTP keep-alive reaping"


# ---------------------------------------------------------------------------
# permessage-deflate offer extraction + H1 negotiation threading (#116)
# ---------------------------------------------------------------------------


class TestPermessageDeflateOffer:
    """_permessage_deflate_offer returns the offered extension segment."""

    def test_no_offer_returns_none(self) -> None:
        from pounce._ws_handler import _permessage_deflate_offer

        assert _permessage_deflate_offer(((b"host", b"x"),)) is None

    def test_bare_offer(self) -> None:
        from pounce._ws_handler import _permessage_deflate_offer

        offer = _permessage_deflate_offer(((b"sec-websocket-extensions", b"permessage-deflate"),))
        assert offer == "permessage-deflate"

    def test_offer_with_window_bits(self) -> None:
        from pounce._ws_handler import _permessage_deflate_offer

        offer = _permessage_deflate_offer(
            ((b"sec-websocket-extensions", b"permessage-deflate; client_max_window_bits=10"),)
        )
        assert offer == "permessage-deflate; client_max_window_bits=10"

    def test_offer_case_insensitive_token(self) -> None:
        from pounce._ws_handler import _permessage_deflate_offer

        offer = _permessage_deflate_offer(
            ((b"Sec-WebSocket-Extensions", b"PerMessage-Deflate; client_max_window_bits=12"),)
        )
        assert offer == "permessage-deflate; client_max_window_bits=12"

    def test_offer_picked_from_comma_list(self) -> None:
        from pounce._ws_handler import _permessage_deflate_offer

        offer = _permessage_deflate_offer(
            (
                (
                    b"sec-websocket-extensions",
                    b"x-other; q=1, permessage-deflate; server_max_window_bits=11",
                ),
            )
        )
        assert offer == "permessage-deflate; server_max_window_bits=11"

    def test_unsupported_extension_returns_none(self) -> None:
        from pounce._ws_handler import _permessage_deflate_offer

        assert (
            _permessage_deflate_offer(((b"sec-websocket-extensions", b"x-webkit-deflate-frame"),))
            is None
        )


class TestH1CompressionNegotiation:
    """The H1 handler threads the offer into WSProtocol and echoes 101 params."""

    async def test_h1_window_bits_echoed_in_101(self) -> None:
        import wsproto.connection

        from pounce._ws_handler import handle_websocket
        from pounce.config import ServerConfig

        # Client close frame so the reader loop terminates after accept.
        client = wsproto.connection.Connection(
            wsproto.connection.ConnectionType.CLIENT,
        )
        close_bytes = client.send(wsproto.events.CloseConnection(code=1000, reason="bye"))

        async def app(scope: dict, receive, send) -> None:
            assert (await receive())["type"] == "websocket.connect"
            await send({"type": "websocket.accept"})
            while True:
                msg = await asyncio.wait_for(receive(), timeout=0.25)
                if msg["type"] == "websocket.disconnect":
                    return

        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            headers=(
                (b"host", b"localhost:8000"),
                (b"upgrade", b"websocket"),
                (b"connection", b"Upgrade"),
                (b"sec-websocket-key", b"dGhlIHNhbXBsZSBub25jZQ=="),
                (b"sec-websocket-version", b"13"),
                (b"sec-websocket-extensions", b"permessage-deflate; client_max_window_bits=10"),
            ),
            http_version="1.1",
        )
        writer = _FakeWSWriter()
        reader = _FakeReader([close_bytes])

        await asyncio.wait_for(
            handle_websocket(
                app,  # type: ignore[arg-type]
                ServerConfig(access_log=False, websocket_compression=True),
                logging.getLogger("test-ws"),
                request,
                reader,  # type: ignore[arg-type]
                writer,  # type: ignore[arg-type]
                client=("127.0.0.1", 54321),
                server=("127.0.0.1", 8000),
                client_str="127.0.0.1:54321",
            ),
            timeout=5.0,
        )

        raw = bytes(writer.data)
        assert b"101 Switching Protocols" in raw
        assert b"Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits=10" in raw

    async def test_h1_no_offer_no_extensions_header(self) -> None:
        import wsproto.connection

        from pounce._ws_handler import handle_websocket
        from pounce.config import ServerConfig

        client = wsproto.connection.Connection(
            wsproto.connection.ConnectionType.CLIENT,
        )
        close_bytes = client.send(wsproto.events.CloseConnection(code=1000, reason="bye"))

        async def app(scope: dict, receive, send) -> None:
            await receive()
            await send({"type": "websocket.accept"})
            while True:
                msg = await asyncio.wait_for(receive(), timeout=0.25)
                if msg["type"] == "websocket.disconnect":
                    return

        request = _ws_upgrade_request()
        writer = _FakeWSWriter()
        reader = _FakeReader([close_bytes])

        await asyncio.wait_for(
            handle_websocket(
                app,  # type: ignore[arg-type]
                ServerConfig(access_log=False, websocket_compression=True),
                logging.getLogger("test-ws"),
                request,
                reader,  # type: ignore[arg-type]
                writer,  # type: ignore[arg-type]
                client=("127.0.0.1", 54321),
                server=("127.0.0.1", 8000),
                client_str="127.0.0.1:54321",
            ),
            timeout=5.0,
        )

        raw = bytes(writer.data)
        assert b"101 Switching Protocols" in raw
        assert b"Sec-WebSocket-Extensions:" not in raw
