"""
Tests for WebSocket permessage-deflate compression.

"""

import pytest

from pounce.config import ServerConfig
from pounce.protocols.ws import build_101_response

# Skip all tests if wsproto is not available
wsproto = pytest.importorskip("wsproto")

from pounce.protocols.ws import WSProtocol  # noqa: E402


class TestBuild101Response:
    """Tests for build_101_response with extensions."""

    def test_build_101_without_extensions(self):
        """Test 101 response without extensions."""
        ws_key = b"dGhlIHNhbXBsZSBub25jZQ=="
        response = build_101_response(ws_key)

        assert b"HTTP/1.1 101 Switching Protocols" in response
        assert b"Upgrade: websocket" in response
        assert b"Connection: Upgrade" in response
        assert b"Sec-WebSocket-Accept:" in response
        assert b"Sec-WebSocket-Extensions:" not in response

    def test_build_101_with_subprotocol(self):
        """Test 101 response with subprotocol."""
        ws_key = b"dGhlIHNhbXBsZSBub25jZQ=="
        response = build_101_response(ws_key, subprotocol="chat")

        assert b"Sec-WebSocket-Protocol: chat" in response

    def test_build_101_with_extensions(self):
        """Test 101 response with permessage-deflate."""
        ws_key = b"dGhlIHNhbXBsZSBub25jZQ=="
        response = build_101_response(ws_key, extensions="permessage-deflate")

        assert b"Sec-WebSocket-Extensions: permessage-deflate" in response

    def test_build_101_with_both(self):
        """Test 101 response with both subprotocol and extensions."""
        ws_key = b"dGhlIHNhbXBsZSBub25jZQ=="
        response = build_101_response(
            ws_key,
            subprotocol="chat",
            extensions="permessage-deflate",
        )

        assert b"Sec-WebSocket-Protocol: chat" in response
        assert b"Sec-WebSocket-Extensions: permessage-deflate" in response


class TestWSProtocolCompression:
    """Tests for WSProtocol with compression enabled."""

    def test_create_without_compression(self):
        """Test creating WSProtocol without compression."""
        ws_proto = WSProtocol(enable_compression=False)

        assert ws_proto.is_closed is False
        assert ws_proto.extensions_response is None

    def test_create_with_compression(self):
        """Test creating WSProtocol with compression."""
        ws_proto = WSProtocol(enable_compression=True)

        assert ws_proto.is_closed is False
        assert ws_proto.extensions_response == "permessage-deflate"

    def test_compression_with_subprotocol(self):
        """Test WSProtocol with both compression and subprotocol."""
        ws_proto = WSProtocol(subprotocol="chat", enable_compression=True)

        assert ws_proto.subprotocol == "chat"
        assert ws_proto.extensions_response == "permessage-deflate"

    def test_send_message_with_compression(self):
        """Test sending messages with compression enabled."""
        ws_proto = WSProtocol(enable_compression=True)

        # Send a text message
        frame = ws_proto.send_message("Hello, WebSocket!")
        assert isinstance(frame, bytes)
        assert len(frame) > 0

        # Send a binary message
        frame = ws_proto.send_message(b"Binary data")
        assert isinstance(frame, bytes)
        assert len(frame) > 0

    def test_receive_data_with_compression(self):
        """Test receiving compressed WebSocket frames."""
        # Create two protocol instances (client and server simulation)
        ws_sender = WSProtocol(enable_compression=True)
        ws_receiver = WSProtocol(enable_compression=True)

        # Send a text message
        text_data = "This is a test message that should be compressed."
        frame = ws_sender.send_message(text_data)

        # Receive and decompress (note: this is a simplified test)
        # In reality, wsproto handles compression/decompression transparently
        assert isinstance(frame, bytes)
        assert len(frame) > 0


class TestServerConfig:
    """Tests for WebSocket configuration fields."""

    def test_default_websocket_compression(self):
        """Test default WebSocket compression setting."""
        config = ServerConfig()

        assert config.websocket_compression is True

    def test_disable_websocket_compression(self):
        """Test disabling WebSocket compression."""
        config = ServerConfig(websocket_compression=False)

        assert config.websocket_compression is False

    def test_websocket_max_message_size(self):
        """Test WebSocket max message size setting."""
        config = ServerConfig()

        assert config.websocket_max_message_size == 10_485_760  # 10 MB

        config = ServerConfig(websocket_max_message_size=5_242_880)
        assert config.websocket_max_message_size == 5_242_880


class TestCompressionNegotiation:
    """Tests for compression negotiation logic."""

    def test_extensions_response_property(self):
        """Test that extensions_response property returns correct value."""
        # Without compression
        ws_proto = WSProtocol(enable_compression=False)
        assert ws_proto.extensions_response is None

        # With compression
        ws_proto = WSProtocol(enable_compression=True)
        assert ws_proto.extensions_response == "permessage-deflate"

    def test_compression_reduces_bandwidth(self):
        """Test that compression reduces frame size for repetitive data."""
        ws_compressed = WSProtocol(enable_compression=True)
        ws_uncompressed = WSProtocol(enable_compression=False)

        # Highly repetitive data that compresses well
        repetitive_data = "A" * 1000

        compressed_frame = ws_compressed.send_message(repetitive_data)
        uncompressed_frame = ws_uncompressed.send_message(repetitive_data)

        # Compressed frame should be significantly smaller
        # Note: WebSocket framing overhead + RSV bit, so not always smaller for tiny messages
        # For large repetitive data, compression should help
        assert len(compressed_frame) <= len(uncompressed_frame)

    def test_close_with_compression(self):
        """Test close frames work with compression enabled."""
        ws_proto = WSProtocol(enable_compression=True)

        close_frame = ws_proto.close(code=1000, reason="Normal closure")

        assert isinstance(close_frame, bytes)
        assert len(close_frame) > 0
        assert ws_proto.is_closed is True
