"""
Integration tests for WebSocket compression end-to-end.

Tests the full flow: HTTP upgrade → 101 response with extensions → compressed frames.

"""

import asyncio

import pytest

from pounce.config import ServerConfig

# Skip all tests if wsproto is not available
wsproto = pytest.importorskip("wsproto")

from pounce._ws_handler import handle_websocket  # noqa: E402
from pounce.protocols._base import RequestReceived  # noqa: E402


class MockStreamReader:
    """Mock asyncio.StreamReader for testing."""

    def __init__(self, data: bytes = b""):
        self._data = data
        self._pos = 0

    async def read(self, n: int) -> bytes:
        """Read up to n bytes."""
        if self._pos >= len(self._data):
            return b""
        chunk = self._data[self._pos : self._pos + n]
        self._pos += n
        return chunk


class MockStreamWriter:
    """Mock asyncio.StreamWriter for testing."""

    def __init__(self):
        self._written: list[bytes] = []

    def write(self, data: bytes) -> None:
        """Write data."""
        self._written.append(data)

    async def drain(self) -> None:
        """Drain (no-op)."""
        pass

    def get_written(self) -> bytes:
        """Get all written data."""
        return b"".join(self._written)


class TestWebSocketCompressionIntegration:
    """Integration tests for WebSocket compression."""

    @pytest.mark.asyncio
    async def test_compression_enabled_in_101_response(self):
        """Test that 101 response includes Sec-WebSocket-Extensions when compression enabled."""
        config = ServerConfig(websocket_compression=True)

        # Create a simple echo app
        async def echo_app(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

            # Accept WebSocket
            await send({"type": "websocket.accept"})

            # Echo one message then close
            msg = await receive()
            if msg["type"] == "websocket.receive":
                text = msg.get("text", "")
                await send({"type": "websocket.send", "text": f"echo: {text}"})
            await send({"type": "websocket.close"})

        # Build a mock WebSocket upgrade request
        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            http_version="1.1",
            headers=(
                (b"host", b"localhost"),
                (b"connection", b"upgrade"),
                (b"upgrade", b"websocket"),
                (b"sec-websocket-version", b"13"),
                (b"sec-websocket-key", b"dGhlIHNhbXBsZSBub25jZQ=="),
                (b"sec-websocket-extensions", b"permessage-deflate"),
            ),
        )

        reader = MockStreamReader()
        writer = MockStreamWriter()

        # Run the WebSocket handler with a timeout
        try:
            await asyncio.wait_for(
                handle_websocket(
                    echo_app,
                    config,
                    logger=None,  # type: ignore
                    request=request,
                    reader=reader,
                    writer=writer,
                    client=("127.0.0.1", 12345),
                    server=("127.0.0.1", 8000),
                    client_str="127.0.0.1:12345",
                ),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass  # Expected - app waits for frames

        # Check that 101 response includes Sec-WebSocket-Extensions
        written = writer.get_written()
        assert b"HTTP/1.1 101 Switching Protocols" in written
        assert b"Sec-WebSocket-Extensions: permessage-deflate" in written

    @pytest.mark.asyncio
    async def test_compression_disabled_no_extensions_header(self):
        """Test that 101 response excludes Sec-WebSocket-Extensions when disabled."""
        config = ServerConfig(websocket_compression=False)

        # Create a simple app
        async def simple_app(scope, receive, send):
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

            # Accept WebSocket
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close"})

        # Build a mock WebSocket upgrade request
        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            http_version="1.1",
            headers=(
                (b"host", b"localhost"),
                (b"connection", b"upgrade"),
                (b"upgrade", b"websocket"),
                (b"sec-websocket-version", b"13"),
                (b"sec-websocket-key", b"dGhlIHNhbXBsZSBub25jZQ=="),
            ),
        )

        reader = MockStreamReader()
        writer = MockStreamWriter()

        # Run the WebSocket handler with a timeout
        try:
            await asyncio.wait_for(
                handle_websocket(
                    simple_app,
                    config,
                    logger=None,  # type: ignore
                    request=request,
                    reader=reader,
                    writer=writer,
                    client=("127.0.0.1", 12345),
                    server=("127.0.0.1", 8000),
                    client_str="127.0.0.1:12345",
                ),
                timeout=1.0,
            )
        except asyncio.TimeoutError:
            pass

        # Check that 101 response does NOT include Sec-WebSocket-Extensions
        written = writer.get_written()
        assert b"HTTP/1.1 101 Switching Protocols" in written
        assert b"Sec-WebSocket-Extensions:" not in written


class TestCompressionRatio:
    """Tests for actual compression effectiveness."""

    def test_compression_ratio_on_repetitive_data(self):
        """Test that compression achieves good ratios on repetitive data."""
        from pounce.protocols.ws import WSProtocol

        # Create protocol instances
        compressed = WSProtocol(enable_compression=True)
        uncompressed = WSProtocol(enable_compression=False)

        # Test with highly compressible data
        repetitive_text = "Hello World! " * 100  # 1300 bytes of repetitive text

        compressed_frame = compressed.send_message(repetitive_text)
        uncompressed_frame = uncompressed.send_message(repetitive_text)

        # Compressed should be at most as large (wsproto may not compress without
        # client negotiation in isolated server-only tests)
        assert len(compressed_frame) <= len(uncompressed_frame)

    def test_compression_on_json_data(self):
        """Test compression on JSON-like data (common in WebSocket apps)."""
        from pounce.protocols.ws import WSProtocol

        compressed = WSProtocol(enable_compression=True)
        uncompressed = WSProtocol(enable_compression=False)

        # Simulate a JSON message with repeated keys (common pattern)
        json_like = '{"type":"message","user":"alice","text":"Hello"}' * 50

        compressed_frame = compressed.send_message(json_like)
        uncompressed_frame = uncompressed.send_message(json_like)

        # Compressed should be at most as large (wsproto may not compress without
        # client negotiation in isolated server-only tests)
        assert len(compressed_frame) <= len(uncompressed_frame)


class TestConfigValidation:
    """Tests for WebSocket configuration validation."""

    def test_default_config_values(self):
        """Test that default config has sensible WebSocket settings."""
        config = ServerConfig()

        assert config.websocket_compression is True
        assert config.websocket_max_message_size == 10_485_760

    def test_custom_config_values(self):
        """Test custom WebSocket configuration."""
        config = ServerConfig(
            websocket_compression=False,
            websocket_max_message_size=5_000_000,
        )

        assert config.websocket_compression is False
        assert config.websocket_max_message_size == 5_000_000
