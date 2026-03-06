"""Tests for pounce.asgi.bridge — scope construction and ASGI callables.

Also tests the _create_h1_protocol() factory (worker.py) since it's a
thin factory function that selects between protocol backends.
"""

import asyncio
from unittest.mock import patch

import pytest

from pounce._compression import GzipCompressor
from pounce._timing import ServerTiming
from pounce.asgi.bridge import (
    _COALESCE_THRESHOLD,
    _DISCONNECT_MESSAGE,
    _EMPTY_BODY_MESSAGE,
    SendState,
    _sanitize_headers,
    build_scope,
    create_disconnect_receive,
    create_empty_receive,
    create_receive,
    create_receive_with_disconnect,
    create_send,
)
from pounce.config import ServerConfig
from pounce.protocols._base import BodyReceived, RequestReceived
from pounce.protocols.h1 import H1Protocol


def _request(
    method: bytes = b"GET",
    target: bytes = b"/",
    headers: tuple[tuple[bytes, bytes], ...] = ((b"host", b"localhost"),),
    http_version: str = "1.1",
) -> RequestReceived:
    return RequestReceived(
        method=method,
        target=target,
        headers=headers,
        http_version=http_version,
    )


class TestBuildScope:
    """build_scope() constructs a valid ASGI HTTP scope."""

    def test_basic_fields(self):
        scope = build_scope(
            _request(), ServerConfig(), client=("127.0.0.1", 5000), server=("0.0.0.0", 8000)
        )
        assert scope["type"] == "http"
        assert scope["method"] == "GET"
        assert scope["path"] == "/"
        assert scope["query_string"] == b""
        assert scope["http_version"] == "1.1"
        assert scope["scheme"] == "http"
        assert scope["client"] == ("127.0.0.1", 5000)
        assert scope["server"] == ("0.0.0.0", 8000)

    def test_path_and_query(self):
        scope = build_scope(
            _request(target=b"/api/users?page=2&sort=name"),
            ServerConfig(),
            client=("127.0.0.1", 5000),
            server=("0.0.0.0", 8000),
        )
        assert scope["path"] == "/api/users"
        assert scope["query_string"] == b"page=2&sort=name"

    def test_percent_encoded_path(self):
        scope = build_scope(
            _request(target=b"/hello%20world"),
            ServerConfig(),
            client=("127.0.0.1", 5000),
            server=("0.0.0.0", 8000),
        )
        assert scope["path"] == "/hello world"

    def test_root_path(self):
        config = ServerConfig(root_path="/prefix")
        scope = build_scope(
            _request(), config, client=("127.0.0.1", 5000), server=("0.0.0.0", 8000)
        )
        assert scope["root_path"] == "/prefix"

    def test_headers_as_bytes(self):
        scope = build_scope(
            _request(
                headers=(
                    (b"host", b"example.com"),
                    (b"accept", b"text/html"),
                )
            ),
            ServerConfig(),
            client=("127.0.0.1", 5000),
            server=("0.0.0.0", 8000),
        )
        assert [b"host", b"example.com"] in scope["headers"]
        assert [b"accept", b"text/html"] in scope["headers"]

    def test_asgi_version(self):
        scope = build_scope(
            _request(), ServerConfig(), client=("127.0.0.1", 5000), server=("0.0.0.0", 8000)
        )
        assert scope["asgi"]["version"] == "3.0"

    def test_post_method(self):
        scope = build_scope(
            _request(method=b"POST", target=b"/api/data"),
            ServerConfig(),
            client=("127.0.0.1", 5000),
            server=("0.0.0.0", 8000),
        )
        assert scope["method"] == "POST"

    def test_raw_path(self):
        scope = build_scope(
            _request(target=b"/hello%20world?q=1"),
            ServerConfig(),
            client=("127.0.0.1", 5000),
            server=("0.0.0.0", 8000),
        )
        assert scope["raw_path"] == b"/hello%20world"

    def test_https_scheme(self):
        config = ServerConfig(
            ssl_certfile="/path/to/cert.pem",
            ssl_keyfile="/path/to/key.pem",
        )
        scope = build_scope(
            _request(), config, client=("127.0.0.1", 5000), server=("0.0.0.0", 8000)
        )
        assert scope["scheme"] == "https"


class TestCreateReceive:
    """create_receive() yields http.request messages from body events."""

    @pytest.mark.asyncio
    async def test_single_body(self):
        queue: asyncio.Queue[BodyReceived] = asyncio.Queue()
        receive = create_receive(queue)

        await queue.put(BodyReceived(data=b"hello", more=False))
        msg = await receive()

        assert msg["type"] == "http.request"
        assert msg["body"] == b"hello"
        assert msg["more_body"] is False

    @pytest.mark.asyncio
    async def test_chunked_body(self):
        queue: asyncio.Queue[BodyReceived] = asyncio.Queue()
        receive = create_receive(queue)

        await queue.put(BodyReceived(data=b"chunk1", more=True))
        await queue.put(BodyReceived(data=b"chunk2", more=False))

        msg1 = await receive()
        assert msg1["body"] == b"chunk1"
        assert msg1["more_body"] is True

        msg2 = await receive()
        assert msg2["body"] == b"chunk2"
        assert msg2["more_body"] is False

    @pytest.mark.asyncio
    async def test_empty_body(self):
        queue: asyncio.Queue[BodyReceived] = asyncio.Queue()
        receive = create_receive(queue)

        await queue.put(BodyReceived(data=b"", more=False))
        msg = await receive()
        assert msg["body"] == b""
        assert msg["more_body"] is False


class _FakeInnerTransport:
    """Fake asyncio.Transport with write buffer size tracking."""

    def get_write_buffer_size(self) -> int:
        return 0


class _FakeTransport:
    """Fake asyncio.StreamWriter that captures writes.

    Mimics the subset of ``asyncio.StreamWriter`` used by
    ``create_send``: ``.write()``, ``.transport``, ``.is_closing()``,
    and ``.drain()``.
    """

    def __init__(self) -> None:
        self.data = bytearray()
        self.write_count = 0
        self.transport = _FakeInnerTransport()

    def write(self, data: bytes) -> None:
        self.data.extend(data)
        self.write_count += 1

    def is_closing(self) -> bool:
        return False

    async def drain(self) -> None:
        """No-op drain for tests."""


class TestCreateSend:
    """create_send() writes response data to the transport."""

    @pytest.mark.asyncio
    async def test_simple_response(self):
        proto = H1Protocol()
        raw_req = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        proto.receive_data(raw_req)

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"5")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"hello",
            }
        )

        output = bytes(transport.data)
        assert b"200" in output
        assert b"hello" in output

    @pytest.mark.asyncio
    async def test_streaming_response(self):
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"transfer-encoding", b"chunked")],
            }
        )

        # Each chunk written immediately — streaming-first
        await send(
            {
                "type": "http.response.body",
                "body": b"chunk1",
                "more_body": True,
            }
        )
        after_chunk1 = len(transport.data)
        assert after_chunk1 > 0  # Written immediately

        await send(
            {
                "type": "http.response.body",
                "body": b"chunk2",
                "more_body": False,
            }
        )
        assert len(transport.data) > after_chunk1

    @pytest.mark.asyncio
    async def test_compression_injection(self):
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        compressor = GzipCompressor()
        send = create_send(proto, transport, SendState(), compressor=compressor)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"hello world" * 100,
            }
        )

        output = bytes(transport.data)
        assert b"content-encoding: gzip" in output
        # Content-length should have been removed
        assert b"content-length" not in output.lower().replace(b"content-encoding", b"")

    @pytest.mark.asyncio
    async def test_server_timing_injection(self):
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        timing = ServerTiming()
        timing.add("parse", 0.3)
        timing.add("app", 12.1)
        send = create_send(proto, transport, SendState(), timing=timing)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"2")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"ok",
            }
        )

        output = bytes(transport.data)
        assert b"server-timing: parse;dur=0.3, app;dur=12.1" in output

    @pytest.mark.asyncio
    async def test_body_before_start_raises(self):
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        with pytest.raises(RuntimeError, match=r"before http\.response\.start"):
            await send(
                {
                    "type": "http.response.body",
                    "body": b"oops",
                }
            )

    @pytest.mark.asyncio
    async def test_body_after_complete_raises(self):
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"2")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"ok",
            }
        )
        with pytest.raises(RuntimeError, match="after response is complete"):
            await send(
                {
                    "type": "http.response.body",
                    "body": b"extra",
                }
            )

    @pytest.mark.asyncio
    async def test_sse_content_type_disables_compression(self):
        """text/event-stream responses skip compression entirely."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        compressor = GzipCompressor()
        send = create_send(proto, transport, SendState(), compressor=compressor)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream; charset=utf-8"),
                    (b"cache-control", b"no-cache"),
                ],
            }
        )

        sse_data = b'event: heartbeat\ndata: {"tick": 1}\n\n'
        await send(
            {
                "type": "http.response.body",
                "body": sse_data,
                "more_body": True,
            }
        )

        output = bytes(transport.data)
        # Compression headers must not be present
        assert b"content-encoding" not in output.lower()
        # Raw SSE text must be visible (not compressed)
        assert b"event: heartbeat" in output

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [204, 304])
    async def test_bodyless_status_disables_compression(self, status):
        """Responses with 204/304 skip compression (RFC 9110 §6.4.1).

        Without this, compressor.flush() produces gzip trailer bytes that
        h11 rejects as 'Too much data for declared Content-Length'.
        """
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        compressor = GzipCompressor()
        send = create_send(proto, transport, SendState(), compressor=compressor)

        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"0")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"",
            }
        )

        output = bytes(transport.data)
        # No compression headers — compressor was disabled
        assert b"content-encoding" not in output.lower()

    @pytest.mark.asyncio
    async def test_streaming_compression_produces_output_per_chunk(self):
        """Each streaming chunk with compression produces output immediately."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        compressor = GzipCompressor()
        send = create_send(proto, transport, SendState(), compressor=compressor)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )

        # First streaming chunk
        await send(
            {
                "type": "http.response.body",
                "body": b"chunk one data here",
                "more_body": True,
            }
        )
        after_chunk1 = len(transport.data)
        assert after_chunk1 > 0  # Data written immediately (not buffered)

        # Second streaming chunk — transport should grow
        await send(
            {
                "type": "http.response.body",
                "body": b"chunk two data here",
                "more_body": True,
            }
        )
        after_chunk2 = len(transport.data)
        assert after_chunk2 > after_chunk1  # sync_flush emitted data


class TestCreateEmptyReceive:
    """create_empty_receive() fast-path for bodyless requests."""

    @pytest.mark.asyncio
    async def test_first_call_returns_empty_body(self):
        """First call returns the pre-built empty body message."""
        receive = create_empty_receive()
        msg = await receive()
        assert msg["type"] == "http.request"
        assert msg["body"] == b""
        assert msg["more_body"] is False

    @pytest.mark.asyncio
    async def test_returns_shared_constant(self):
        """The returned message equals _EMPTY_BODY_MESSAGE (dict copy for type safety)."""
        receive = create_empty_receive()
        msg = await receive()
        assert msg == _EMPTY_BODY_MESSAGE

    @pytest.mark.asyncio
    async def test_second_call_blocks(self):
        """Second call blocks indefinitely (app should never call twice)."""
        receive = create_empty_receive()
        await receive()  # first call — returns immediately

        # Second call should block; verify with a short timeout
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(receive(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_independent_instances(self):
        """Each create_empty_receive() call returns an independent callable."""
        recv1 = create_empty_receive()
        recv2 = create_empty_receive()

        msg1 = await recv1()
        msg2 = await recv2()

        # Both should succeed independently
        assert msg1["more_body"] is False
        assert msg2["more_body"] is False


class TestWriteCoalescing:
    """Write coalescing: head + body combined into single write for small responses."""

    @pytest.mark.asyncio
    async def test_small_response_single_write(self):
        """Small response (< threshold) uses one write call for head + body."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"5")],
            }
        )
        # Head is buffered — no writes yet
        assert transport.write_count == 0

        await send(
            {
                "type": "http.response.body",
                "body": b"hello",
            }
        )
        # Head + body coalesced into a single write
        assert transport.write_count == 1
        output = bytes(transport.data)
        assert b"200" in output
        assert b"hello" in output

    @pytest.mark.asyncio
    async def test_large_response_two_writes(self):
        """Large response (> threshold) flushes head and body separately."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        # Body larger than _COALESCE_THRESHOLD (16 KB)
        large_body = b"x" * (_COALESCE_THRESHOLD + 1)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"content-length", str(len(large_body)).encode()),
                ],
            }
        )
        assert transport.write_count == 0

        await send(
            {
                "type": "http.response.body",
                "body": large_body,
            }
        )
        # Head and body written separately
        assert transport.write_count == 2

    @pytest.mark.asyncio
    async def test_streaming_head_coalesced_with_first_chunk(self):
        """In streaming mode, head is coalesced with the first small chunk."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"transfer-encoding", b"chunked")],
            }
        )
        assert transport.write_count == 0

        # First small chunk — coalesced with head
        await send(
            {
                "type": "http.response.body",
                "body": b"first",
                "more_body": True,
            }
        )
        assert transport.write_count == 1  # head + first chunk

        # Second chunk — standalone write
        await send(
            {
                "type": "http.response.body",
                "body": b"second",
                "more_body": False,
            }
        )
        assert transport.write_count == 2  # second chunk separate


class TestAutoChunkedEncoding:
    """Auto-inject Transfer-Encoding: chunked when no Content-Length.

    Without either Content-Length or chunked TE, HTTP/1.1 keep-alive
    connections have no way to delimit response boundaries — the
    browser hangs waiting for more data.
    """

    @pytest.mark.asyncio
    async def test_auto_chunked_when_no_content_length(self):
        """Responses without Content-Length get automatic chunked encoding."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/html")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"<h1>hello</h1>",
                "more_body": True,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"",
                "more_body": False,
            }
        )

        output = bytes(transport.data)
        assert b"chunked" in output.lower()
        assert b"transfer-encoding" in output.lower()
        # Chunked framing: body wrapped in hex-size lines
        assert b"e\r\n<h1>hello</h1>\r\n" in output
        # Terminator present
        assert b"0\r\n\r\n" in output

    @pytest.mark.asyncio
    async def test_no_auto_chunked_when_content_length_present(self):
        """Responses WITH Content-Length do not get chunked injected."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"content-length", b"5"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"hello",
            }
        )

        output = bytes(transport.data)
        assert b"content-length" in output.lower()
        assert b"transfer-encoding" not in output.lower()

    @pytest.mark.asyncio
    async def test_no_duplicate_when_app_provides_chunked(self):
        """If the app already sets Transfer-Encoding: chunked, don't duplicate."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html"),
                    (b"transfer-encoding", b"chunked"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"ok",
                "more_body": False,
            }
        )

        output = bytes(transport.data)
        # Should appear exactly once
        count = output.lower().count(b"transfer-encoding")
        assert count == 1

    @pytest.mark.asyncio
    async def test_streaming_without_content_length_is_chunked(self):
        """Multi-chunk streaming response without CL gets proper chunked framing."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        state = SendState()
        send = create_send(proto, transport, state)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/html; charset=utf-8")],
            }
        )

        chunks = [b"<html>", b"<body>hello</body>", b"</html>"]
        for i, chunk in enumerate(chunks):
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": i < len(chunks) - 1,
                }
            )

        output = bytes(transport.data)
        # All original data present within chunked frames
        assert b"<html>" in output
        assert b"<body>hello</body>" in output
        assert b"</html>" in output
        # Properly terminated
        assert b"0\r\n\r\n" in output
        assert state.bytes_sent == sum(len(c) for c in chunks)


class TestCreateH1Protocol:
    """_create_h1_protocol() selects the best available HTTP/1.1 backend."""

    def test_fallback_to_h11(self):
        """Without httptools, returns H1Protocol (h11)."""
        from pounce.worker import _create_h1_protocol

        with patch("pounce.worker._use_httptools", False):
            proto = _create_h1_protocol()

        assert type(proto).__name__ == "H1Protocol"

    def test_httptools_when_available(self):
        """With httptools available, returns H1HttpToolsProtocol."""
        httptools = pytest.importorskip("httptools")  # noqa: F841

        from pounce.worker import _create_h1_protocol

        with patch("pounce.worker._use_httptools", True):
            proto = _create_h1_protocol()

        assert type(proto).__name__ == "H1HttpToolsProtocol"

    def test_passes_max_incomplete_event_size(self):
        """max_incomplete_event_size is forwarded to the h11 backend."""
        from pounce.worker import _create_h1_protocol

        with patch("pounce.worker._use_httptools", False):
            proto = _create_h1_protocol(max_incomplete_event_size=8192)

        # h11 stores this on its Connection object
        assert proto._conn._max_incomplete_event_size == 8192


class TestCreateDisconnectReceive:
    """create_disconnect_receive() delivers empty body then http.disconnect."""

    @pytest.mark.asyncio
    async def test_first_call_returns_empty_body(self):
        """First call returns the pre-built empty body message."""
        disconnect = asyncio.Event()
        receive = create_disconnect_receive(disconnect)
        msg = await receive()
        assert msg["type"] == "http.request"
        assert msg["body"] == b""
        assert msg["more_body"] is False
        assert msg == _EMPTY_BODY_MESSAGE

    @pytest.mark.asyncio
    async def test_second_call_blocks_until_disconnect(self):
        """Second call blocks until the disconnect event fires."""
        disconnect = asyncio.Event()
        receive = create_disconnect_receive(disconnect)
        await receive()  # first call — returns immediately

        # Second call should block; verify with a short timeout
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(receive(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_returns_disconnect_after_event(self):
        """After disconnect event fires, returns http.disconnect."""
        disconnect = asyncio.Event()
        receive = create_disconnect_receive(disconnect)
        await receive()  # consume empty body

        # Set disconnect and verify the message
        disconnect.set()
        msg = await receive()
        assert msg["type"] == "http.disconnect"
        assert msg == _DISCONNECT_MESSAGE

    @pytest.mark.asyncio
    async def test_disconnect_set_before_second_call(self):
        """If disconnect fires before second call, returns immediately."""
        disconnect = asyncio.Event()
        receive = create_disconnect_receive(disconnect)
        await receive()

        disconnect.set()
        msg = await asyncio.wait_for(receive(), timeout=0.1)
        assert msg["type"] == "http.disconnect"

    @pytest.mark.asyncio
    async def test_independent_instances(self):
        """Each create_disconnect_receive() returns an independent callable."""
        d1 = asyncio.Event()
        d2 = asyncio.Event()
        recv1 = create_disconnect_receive(d1)
        recv2 = create_disconnect_receive(d2)

        msg1 = await recv1()
        msg2 = await recv2()
        assert msg1["more_body"] is False
        assert msg2["more_body"] is False

        # Setting d1 should not affect recv2
        d1.set()
        result = await recv1()
        assert result["type"] == "http.disconnect"

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(recv2(), timeout=0.05)


class TestCreateReceiveWithDisconnect:
    """create_receive_with_disconnect() delivers body events then http.disconnect."""

    @pytest.mark.asyncio
    async def test_body_events_flow_through(self):
        """Body events are returned as http.request messages."""
        disconnect = asyncio.Event()
        queue: asyncio.Queue[BodyReceived] = asyncio.Queue()
        receive = create_receive_with_disconnect(queue, disconnect)

        await queue.put(BodyReceived(data=b"chunk1", more=True))
        await queue.put(BodyReceived(data=b"chunk2", more=False))

        msg1 = await receive()
        assert msg1["type"] == "http.request"
        assert msg1["body"] == b"chunk1"
        assert msg1["more_body"] is True

        msg2 = await receive()
        assert msg2["type"] == "http.request"
        assert msg2["body"] == b"chunk2"
        assert msg2["more_body"] is False

    @pytest.mark.asyncio
    async def test_disconnect_after_body_complete(self):
        """After body complete, waits for disconnect then returns http.disconnect."""
        disconnect = asyncio.Event()
        queue: asyncio.Queue[BodyReceived] = asyncio.Queue()
        receive = create_receive_with_disconnect(queue, disconnect)

        await queue.put(BodyReceived(data=b"all", more=False))
        await receive()  # consume body

        # Should block until disconnect
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(receive(), timeout=0.05)

        # Now signal disconnect
        disconnect.set()
        msg = await receive()
        assert msg["type"] == "http.disconnect"
        assert msg == _DISCONNECT_MESSAGE

    @pytest.mark.asyncio
    async def test_single_chunk_body(self):
        """Single-chunk body works correctly."""
        disconnect = asyncio.Event()
        queue: asyncio.Queue[BodyReceived] = asyncio.Queue()
        receive = create_receive_with_disconnect(queue, disconnect)

        await queue.put(BodyReceived(data=b"hello", more=False))
        msg = await receive()
        assert msg["body"] == b"hello"
        assert msg["more_body"] is False

        disconnect.set()
        msg = await receive()
        assert msg["type"] == "http.disconnect"


class _ClosingFakeInnerTransport:
    """Fake transport that reports as closing."""

    def get_write_buffer_size(self) -> int:
        return 0


class _ClosingFakeTransport:
    """Fake StreamWriter that reports is_closing() = True."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.write_count = 0
        self.transport = _ClosingFakeInnerTransport()

    def write(self, data: bytes) -> None:
        self.data.extend(data)
        self.write_count += 1

    def is_closing(self) -> bool:
        return True

    async def drain(self) -> None:
        """No-op drain for tests."""


class TestSendGuardClosedWriter:
    """send() silently returns when the writer is closing."""

    @pytest.mark.asyncio
    async def test_body_skipped_when_writer_closing(self):
        """http.response.body is silently discarded when writer.is_closing()."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        # Use a regular transport for the start message, then swap to closing
        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )

        # Now test with a closing writer — create a new send
        closing_transport = _ClosingFakeTransport()
        state = SendState()
        proto2 = H1Protocol()
        proto2.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
        send2 = create_send(proto2, closing_transport, state)

        await send2(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        # Body should be silently skipped — no writes, no errors
        await send2(
            {
                "type": "http.response.body",
                "body": b"should not be written",
                "more_body": True,
            }
        )
        assert closing_transport.write_count == 0
        assert len(closing_transport.data) == 0

    @pytest.mark.asyncio
    async def test_body_not_skipped_when_writer_open(self):
        """http.response.body proceeds normally when writer is not closing."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"5")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"hello",
            }
        )
        assert transport.write_count > 0
        assert b"hello" in bytes(transport.data)


class TestSanitizeHeaders:
    """_sanitize_headers() strips CRLF from response header values."""

    def test_clean_headers_unchanged(self):
        """Headers without CRLF pass through unchanged."""
        headers = [(b"content-type", b"text/html"), (b"x-custom", b"value")]
        assert _sanitize_headers(headers) == headers

    def test_crlf_in_value_stripped(self):
        """CRLF in header value is stripped to prevent injection."""
        headers = [(b"x-evil", b"value\r\nInjected: header")]
        result = _sanitize_headers(headers)
        assert result == [(b"x-evil", b"valueInjected: header")]

    def test_cr_only_stripped(self):
        """Bare CR in header value is stripped."""
        headers = [(b"x-bad", b"before\rafter")]
        result = _sanitize_headers(headers)
        assert result == [(b"x-bad", b"beforeafter")]

    def test_lf_only_stripped(self):
        """Bare LF in header value is stripped."""
        headers = [(b"x-bad", b"before\nafter")]
        result = _sanitize_headers(headers)
        assert result == [(b"x-bad", b"beforeafter")]

    def test_crlf_in_name_stripped(self):
        """CRLF in header name is stripped."""
        headers = [(b"x-\r\nbad", b"value")]
        result = _sanitize_headers(headers)
        assert result == [(b"x-bad", b"value")]

    def test_empty_name_after_strip_dropped(self):
        """Header with name that becomes empty after stripping is dropped."""
        headers = [(b"\r\n", b"value"), (b"good", b"value")]
        result = _sanitize_headers(headers)
        assert result == [(b"good", b"value")]

    @pytest.mark.asyncio
    async def test_integration_in_send(self):
        """CRLF headers are sanitized during http.response.start."""
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport, SendState())

        # This would be an injection attempt: the value contains CRLF
        # that could split into a second header
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-length", b"2"),
                    (b"x-safe", b"clean\r\nX-Injected: evil"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"ok",
            }
        )
        output = bytes(transport.data)
        # The injected header must not appear as a separate header line.
        # After sanitization, "X-Injected: evil" is concatenated into
        # the x-safe value — not on its own line preceded by \r\n.
        assert b"\r\nX-Injected:" not in output
        assert b"\r\nx-injected:" not in output.lower()


class TestHeadCompressionGuard:
    """HEAD responses must not be compressed (Content-Length mismatch)."""

    @pytest.mark.asyncio
    async def test_head_request_disables_compression(self):
        """Compression is disabled for HEAD requests to preserve Content-Length."""
        proto = H1Protocol()
        proto.receive_data(b"HEAD / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        compressor = GzipCompressor()
        send = create_send(
            proto,
            transport,
            SendState(),
            compressor=compressor,
            request_method=b"HEAD",
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"content-length", b"1000"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"",  # HEAD: no body on wire
            }
        )

        output = bytes(transport.data)
        # No compression headers — compressor was disabled
        assert b"content-encoding" not in output.lower()
        # Content-Length is preserved (not stripped for compression)
        assert b"content-length: 1000" in output.lower()
