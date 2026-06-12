"""Tests for HTTP/2 connection-level behavior."""

import asyncio
import logging
from typing import Any

import pytest

try:
    import h2.config
    import h2.connection
    import h2.events

    _HAS_H2 = True
except ImportError:
    _HAS_H2 = False

from pounce.config import ServerConfig

pytestmark = pytest.mark.skipif(not _HAS_H2, reason="h2 not installed")


class _FakeWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None


def _make_client() -> Any:
    client_config = h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
    client = h2.connection.H2Connection(config=client_config)
    client.initiate_connection()
    return client


async def _run_h2_bytes(app: Any, config: ServerConfig, data: bytes) -> bytes:
    from pounce._h2_handler import handle_h2_connection

    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    writer = _FakeWriter()

    await handle_h2_connection(
        app,
        config,
        logging.getLogger("test.h2"),
        reader,
        writer,
        ("127.0.0.1", 50000),
        ("127.0.0.1", 8443),
        "127.0.0.1:50000",
    )
    return bytes(writer.data)


def _response_statuses(client: Any, data: bytes) -> list[int]:
    return [
        int(dict(event.headers)[":status"])
        for event in client.receive_data(data)
        if isinstance(event, h2.events.ResponseReceived)
    ]


async def test_h2_content_length_over_limit_rejected_before_app_dispatch() -> None:
    app_called = False

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal app_called
        app_called = True

    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "POST"),
            (":path", "/upload"),
            (":authority", "example.test"),
            (":scheme", "https"),
            ("content-length", "200"),
        ],
    )

    config = ServerConfig(max_request_size=100, access_log=False)
    output = await _run_h2_bytes(app, config, client.data_to_send())

    assert 413 in _response_statuses(client, output)
    assert app_called is False


async def test_h2_streaming_body_over_limit_returns_413_without_body_delivery() -> None:
    app_started = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        app_started.set()
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break

    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "POST"),
            (":path", "/upload"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
    )
    client.send_data(1, b"x" * 200, end_stream=True)

    config = ServerConfig(max_request_size=100, access_log=False)
    output = await _run_h2_bytes(app, config, client.data_to_send())

    assert not app_started.is_set()
    assert 413 in _response_statuses(client, output)


def _stream_resets(client: Any, data: bytes) -> list[Any]:
    """Return StreamReset events parsed from server output bytes."""
    return [
        event for event in client.receive_data(data) if isinstance(event, h2.events.StreamReset)
    ]


async def test_h2_streaming_body_over_limit_emits_rst_stream() -> None:
    """After a 413 body-limit rejection, the peer must observe RST_STREAM (#125).

    Without the reset the inbound half stays open and the client keeps
    streaming a body the server has already abandoned.
    """

    async def app(scope: Any, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break

    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "POST"),
            (":path", "/upload"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
    )
    # Client is still uploading (more_body) when it crosses the limit — the
    # inbound half is open, so a RST_STREAM is needed to tell it to stop.
    client.send_data(1, b"x" * 200, end_stream=False)

    config = ServerConfig(max_request_size=100, access_log=False)
    output = await _run_h2_bytes(app, config, client.data_to_send())

    events = client.receive_data(output)
    statuses = [
        int(dict(event.headers)[":status"])
        for event in events
        if isinstance(event, h2.events.ResponseReceived)
    ]
    resets = [event for event in events if isinstance(event, h2.events.StreamReset)]
    assert 413 in statuses
    # The 413 must be followed by an explicit RST_STREAM telling the client
    # the upload was refused (ENHANCE_YOUR_CALM == 0xb).
    assert resets, "expected RST_STREAM after 413 body-limit rejection"
    assert resets[0].stream_id == 1
    assert int(resets[0].error_code) == 0xB


async def test_h2_content_length_over_limit_emits_rst_stream() -> None:
    """Content-Length-based 413 also resets the stream (#125)."""

    async def app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
        pass

    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "POST"),
            (":path", "/upload"),
            (":authority", "example.test"),
            (":scheme", "https"),
            ("content-length", "200"),
        ],
    )

    config = ServerConfig(max_request_size=100, access_log=False)
    output = await _run_h2_bytes(app, config, client.data_to_send())

    resets = _stream_resets(client, output)
    assert resets, "expected RST_STREAM after Content-Length 413 rejection"
    assert int(resets[0].error_code) == 0xB


async def test_h2_post_413_data_not_flow_control_acked() -> None:
    """In-flight DATA on a reset stream is not re-credited (#125).

    Once the server resets the stream, further DATA frames the client had
    already put on the wire must produce no H2BodyReceived event and must
    not trigger a stream-level WINDOW_UPDATE.  We assert by driving the
    protocol layer directly: receiving DATA on the reset stream yields no
    DataReceived event, so acknowledge_received_data is never called.
    """
    from pounce.protocols.h2 import H2Connection

    server = H2Connection()
    server.initiate_connection()
    client = _make_client()
    server.receive_data(client.data_to_send())
    client.receive_data(server.data_to_send())

    client.send_headers(
        1,
        [
            (":method", "POST"),
            (":path", "/upload"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
    )
    client.send_data(1, b"x" * 50)
    server.receive_data(client.data_to_send())

    # Server rejects: 413 + reset (mirrors _send_request_too_large).
    server.send_response_headers(1, 413, [(b"content-type", b"text/plain")], end_stream=True)
    server.reset_stream(1, error_code=0xB)

    # Build a raw in-flight DATA frame for the now-reset stream.
    from hyperframe.frame import DataFrame

    frame = DataFrame(stream_id=1)
    frame.data = b"z" * 40
    frame.flags = set()

    events = server.receive_data(frame.serialize())
    body_events = [e for e in events if getattr(e, "stream_id", None) == 1 and hasattr(e, "body")]
    assert not body_events, "DATA on a reset stream must not surface as body"
