"""Tests for pounce.asgi.bridge — scope construction and ASGI callables."""

import asyncio

import pytest

from pounce._compression import GzipCompressor
from pounce._timing import ServerTiming
from pounce.asgi.bridge import build_scope, create_receive, create_send
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
            _request(headers=(
                (b"host", b"example.com"),
                (b"accept", b"text/html"),
            )),
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
        config = ServerConfig(ssl_certfile="/path/to/cert.pem")
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


class _FakeTransport:
    """Fake asyncio transport that captures writes."""

    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)


class TestCreateSend:
    """create_send() writes response data to the transport."""

    @pytest.mark.asyncio
    async def test_simple_response(self):
        proto = H1Protocol()
        raw_req = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        proto.receive_data(raw_req)

        transport = _FakeTransport()
        send = create_send(proto, transport)

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain"), (b"content-length", b"5")],
        })
        await send({
            "type": "http.response.body",
            "body": b"hello",
        })

        output = bytes(transport.data)
        assert b"200" in output
        assert b"hello" in output

    @pytest.mark.asyncio
    async def test_streaming_response(self):
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport)

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"transfer-encoding", b"chunked")],
        })

        # Each chunk written immediately — streaming-first
        await send({
            "type": "http.response.body",
            "body": b"chunk1",
            "more_body": True,
        })
        after_chunk1 = len(transport.data)
        assert after_chunk1 > 0  # Written immediately

        await send({
            "type": "http.response.body",
            "body": b"chunk2",
            "more_body": False,
        })
        assert len(transport.data) > after_chunk1

    @pytest.mark.asyncio
    async def test_compression_injection(self):
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        compressor = GzipCompressor()
        send = create_send(proto, transport, compressor=compressor)

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        })
        await send({
            "type": "http.response.body",
            "body": b"hello world" * 100,
        })

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
        send = create_send(proto, transport, timing=timing)

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", b"2")],
        })
        await send({
            "type": "http.response.body",
            "body": b"ok",
        })

        output = bytes(transport.data)
        assert b"server-timing: parse;dur=0.3, app;dur=12.1" in output

    @pytest.mark.asyncio
    async def test_body_before_start_raises(self):
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport)

        with pytest.raises(RuntimeError, match="before http.response.start"):
            await send({
                "type": "http.response.body",
                "body": b"oops",
            })

    @pytest.mark.asyncio
    async def test_body_after_complete_raises(self):
        proto = H1Protocol()
        proto.receive_data(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")

        transport = _FakeTransport()
        send = create_send(proto, transport)

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", b"2")],
        })
        await send({
            "type": "http.response.body",
            "body": b"ok",
        })
        with pytest.raises(RuntimeError, match="after response is complete"):
            await send({
                "type": "http.response.body",
                "body": b"extra",
            })
