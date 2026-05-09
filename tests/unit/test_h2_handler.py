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
