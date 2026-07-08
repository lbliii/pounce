"""Tests for HTTP/2 connection-level behavior."""

import asyncio
import json
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


class _FakeTransport:
    """Minimal transport stub so the H2 send path can probe the write buffer."""

    def get_write_buffer_size(self) -> int:
        return 0


class _FakeWriter:
    def __init__(self) -> None:
        self.data = bytearray()
        self.transport = _FakeTransport()

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


# ---------------------------------------------------------------------------
# Issue #160 — shared request-pipeline prelude (compressor + access-log filter)
# ---------------------------------------------------------------------------

try:
    from pounce._compression import _HAS_ZSTD
except ImportError:  # pragma: no cover - zstd is optional
    _HAS_ZSTD = False


def _make_compression_dictionary(match: str = "/api/*") -> Any:
    """Train a small zstd dictionary for dcz negotiation tests."""
    import json
    from compression import zstd

    from pounce._compression import CompressionDictionary

    samples = [
        json.dumps({"id": i, "name": f"item_{i}", "status": "active"}).encode() for i in range(200)
    ]
    trained = zstd.train_dict(samples, dict_size=8192)
    return CompressionDictionary(trained.dict_content, match)


def _response_headers(client: Any, data: bytes) -> dict[str, str]:
    """Return the decoded H2 response headers."""
    for event in client.receive_data(data):
        if isinstance(event, h2.events.ResponseReceived):
            return {
                name.decode() if isinstance(name, bytes) else name: (
                    value.decode() if isinstance(value, bytes) else value
                )
                for name, value in event.headers
            }
    return {}


async def _run_h2_request(
    app: Any,
    config: ServerConfig,
    request_bytes: bytes,
    *,
    worker_id: int | None = None,
    is_draining: Any = None,
) -> bytes:
    """Drive a single H2 request to completion through ``handle_h2_connection``.

    Unlike ``_run_h2_bytes`` (which feeds EOF immediately and is suited to the
    synchronous pre-dispatch rejection paths), this runs the handler as a task,
    feeds the request, and waits for the per-stream ASGI task to flush a full
    response before tearing the connection down — so success-path responses are
    observed deterministically.
    """
    from pounce._h2_handler import handle_h2_connection

    reader = asyncio.StreamReader()
    reader.feed_data(request_bytes)
    writer = _FakeWriter()

    task = asyncio.create_task(
        handle_h2_connection(
            app,
            config,
            logging.getLogger("test.h2"),
            reader,
            writer,
            ("127.0.0.1", 50000),
            ("127.0.0.1", 8443),
            "127.0.0.1:50000",
            worker_id=worker_id,
            is_draining=is_draining,
        )
    )
    # Let the per-stream ASGI task run to completion.  Each ASGI ``send`` awaits
    # ``writer.drain()`` (which yields), so a bounded run of event-loop turns is
    # enough for the no-I/O test app to flush its full response before we close
    # the inbound half and let the handler return.
    for _ in range(500):
        await asyncio.sleep(0)
    # Close the inbound half so the read loop exits and the handler returns.
    reader.feed_eof()
    await asyncio.wait_for(task, timeout=2.0)
    return bytes(writer.data)


def _response_body(client: Any, data: bytes) -> tuple[int, bytes]:
    status = 0
    body = bytearray()
    for event in client.receive_data(data):
        if isinstance(event, h2.events.ResponseReceived):
            status = int(dict(event.headers)[":status"])
        elif isinstance(event, h2.events.DataReceived):
            body.extend(event.data)
    return status, bytes(body)


async def test_h2_health_reports_real_worker_id_without_app_dispatch() -> None:
    app_called = False

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal app_called
        app_called = True

    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "GET"),
            (":path", "/healthz"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
        end_stream=True,
    )

    output = await _run_h2_request(
        app,
        ServerConfig(health_check_path="/healthz", access_log=False),
        client.data_to_send(),
        worker_id=7,
    )
    status, body = _response_body(client, output)

    assert status == 200
    assert json.loads(body)["worker_id"] == 7
    assert app_called is False


async def test_h2_readiness_head_matches_get_while_draining() -> None:
    app_called = False
    results: dict[str, tuple[int, dict[str, str], bytes]] = {}

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal app_called
        app_called = True

    for method in ("GET", "HEAD"):
        client = _make_client()
        client.send_headers(
            1,
            [
                (":method", method),
                (":path", "/readyz"),
                (":authority", "example.test"),
                (":scheme", "https"),
            ],
            end_stream=True,
        )
        output = await _run_h2_request(
            app,
            ServerConfig(health_check_path="/readyz", access_log=False),
            client.data_to_send(),
            worker_id=7,
            is_draining=lambda: True,
        )
        status = 0
        headers: dict[str, str] = {}
        body = bytearray()
        for event in client.receive_data(output):
            if isinstance(event, h2.events.ResponseReceived):
                decoded = dict(event.headers)
                status = int(decoded[":status"])
                headers = {str(name): str(value) for name, value in event.headers}
            elif isinstance(event, h2.events.DataReceived):
                body.extend(event.data)
        results[method] = (status, headers, bytes(body))

    get_status, get_headers, get_body = results["GET"]
    head_status, head_headers, head_body = results["HEAD"]
    assert get_status == head_status == 503
    for name in ("content-type", "content-length", "cache-control"):
        assert get_headers[name] == head_headers[name]
    assert json.loads(get_body)["status"] == "draining"
    assert head_body == b""
    assert app_called is False


async def test_h2_serves_introspection_without_app_dispatch() -> None:
    app_called = False

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal app_called
        app_called = True

    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "GET"),
            (":path", "/_pounce/info"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
        end_stream=True,
    )

    output = await _run_h2_request(
        app,
        ServerConfig(introspection_enabled=True, access_log=False),
        client.data_to_send(),
        worker_id=8,
    )
    status, body = _response_body(client, output)

    assert status == 200
    assert json.loads(body)["worker"]["worker_id"] == 8
    assert app_called is False


@pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
async def test_h2_serves_compression_dictionary_without_app_dispatch() -> None:
    app_called = False

    async def app(scope: Any, receive: Any, send: Any) -> None:
        nonlocal app_called
        app_called = True

    dictionary = _make_compression_dictionary()
    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "GET"),
            (":path", f"/.well-known/compression-dictionary/{dictionary.sf_hash}"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
        end_stream=True,
    )

    output = await _run_h2_request(
        app,
        ServerConfig(compression_dictionaries=(dictionary,), access_log=False),
        client.data_to_send(),
    )
    status, body = _response_body(client, output)

    assert status == 200
    assert body == dictionary.zstd_dict.dict_content
    assert app_called is False


@pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
async def test_h2_negotiates_dcz_with_available_dictionary() -> None:
    """Available-Dictionary + zstd negotiates a dcz compressor on H2 (#160).

    This exercises the shared ``negotiate_compressor`` prelude, which restores
    the already-advertised RFC 9842 dictionary path on HTTP/2.
    """

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"x" * 1024, "more_body": False})

    cd = _make_compression_dictionary(match="/api/*")
    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "GET"),
            (":path", "/api/v1/items"),
            (":authority", "example.test"),
            (":scheme", "https"),
            ("accept-encoding", "zstd, gzip"),
            ("available-dictionary", cd.sf_hash),
        ],
        end_stream=True,
    )

    config = ServerConfig(
        compression=True,
        compression_min_size=0,
        compression_dictionaries=(cd,),
        access_log=False,
    )
    output = await _run_h2_request(app, config, client.data_to_send())

    headers = _response_headers(client, output)
    assert headers["content-encoding"] == "dcz"
    assert headers["used-dictionary"] == cd.sf_hash


async def test_h2_access_log_filter_suppresses_entry(monkeypatch: Any) -> None:
    """access_log_filter returning False suppresses the H2 access log (#160).

    The H2 handler now logs via the shared ``log_request`` helper, so the
    filter contract is exercised by patching the pipeline's ``access_log``.
    """
    import pounce._request_pipeline as pipeline

    calls: list[tuple[str, str, int]] = []
    monkeypatch.setattr(
        pipeline,
        "access_log",
        lambda method, target, status, *a, **k: calls.append((method, target, status)),
    )

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "GET"),
            (":path", "/healthz"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
        end_stream=True,
    )

    config = ServerConfig(
        access_log=True,
        access_log_filter=lambda method, path, status: path != "/healthz",
    )
    output = await _run_h2_request(app, config, client.data_to_send())

    assert 200 in _response_statuses(client, output)
    assert calls == [], "filtered request must not reach access_log"


async def test_h2_keep_alive_timeout_does_not_cancel_active_response() -> None:
    """A quiet receiving peer must not lose a response at the idle timeout (#231)."""
    from pounce._h2_handler import handle_h2_connection

    first_chunk_sent = asyncio.Event()

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": b"first-",
                "more_body": True,
            }
        )
        first_chunk_sent.set()
        await asyncio.sleep(0.04)
        await send({"type": "http.response.body", "body": b"second"})

    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "GET"),
            (":path", "/slow-response"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
        end_stream=True,
    )
    reader = asyncio.StreamReader()
    reader.feed_data(client.data_to_send())
    writer = _FakeWriter()

    task = asyncio.create_task(
        handle_h2_connection(
            app,
            ServerConfig(keep_alive_timeout=0.01, access_log=False),
            logging.getLogger("test.h2"),
            reader,
            writer,
            ("127.0.0.1", 50000),
            ("127.0.0.1", 8443),
            "127.0.0.1:50000",
        )
    )

    await asyncio.wait_for(first_chunk_sent.wait(), timeout=0.2)
    await asyncio.wait_for(task, timeout=0.3)

    status, body = _response_body(client, bytes(writer.data))
    assert status == 200
    assert body == b"first-second"


async def test_h2_keep_alive_timeout_still_reaps_idle_connection() -> None:
    """A connection with no active streams still closes at the idle timeout."""
    from pounce._h2_handler import handle_h2_connection

    async def app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
        raise AssertionError("idle connection must not dispatch the app")

    client = _make_client()
    reader = asyncio.StreamReader()
    reader.feed_data(client.data_to_send())
    writer = _FakeWriter()

    await asyncio.wait_for(
        handle_h2_connection(
            app,
            ServerConfig(keep_alive_timeout=0.01, access_log=False),
            logging.getLogger("test.h2"),
            reader,
            writer,
            ("127.0.0.1", 50000),
            ("127.0.0.1", 8443),
            "127.0.0.1:50000",
        ),
        timeout=0.2,
    )


async def test_h2_large_response_advances_on_window_updates() -> None:
    """Large responses resume promptly as the peer replenishes flow control (#232)."""
    from h2.settings import SettingCodes

    from pounce._h2_handler import handle_h2_connection

    payload = b"x" * (2 * 1024 * 1024)

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(payload)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    client = _make_client()
    # Force many flow-control stalls.  The pre-fix send path spun in
    # writer.drain() here and starved the read loop that processes the peer's
    # WINDOW_UPDATE frames.
    client.update_settings({SettingCodes.INITIAL_WINDOW_SIZE: 8192})
    client.send_headers(
        1,
        [
            (":method", "GET"),
            (":path", "/large"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
        end_stream=True,
    )

    reader = asyncio.StreamReader()
    reader.feed_data(client.data_to_send())
    writer = _FakeWriter()
    task = asyncio.create_task(
        handle_h2_connection(
            app,
            ServerConfig(keep_alive_timeout=0.1, access_log=False),
            logging.getLogger("test.h2"),
            reader,
            writer,
            ("127.0.0.1", 50000),
            ("127.0.0.1", 8443),
            "127.0.0.1:50000",
        )
    )

    received = bytearray()
    consumed = 0
    stream_ended = False
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + 2.0
    while not stream_ended and loop.time() < deadline:
        await asyncio.sleep(0)
        output = bytes(writer.data[consumed:])
        consumed += len(output)
        if not output:
            continue
        for event in client.receive_data(output):
            if isinstance(event, h2.events.DataReceived):
                received.extend(event.data)
                client.acknowledge_received_data(
                    event.flow_controlled_length,
                    event.stream_id,
                )
            elif isinstance(event, h2.events.StreamEnded):
                stream_ended = True
        feedback = client.data_to_send()
        if feedback:
            reader.feed_data(feedback)

    elapsed = loop.time() - started
    reader.feed_eof()
    await asyncio.wait_for(task, timeout=0.5)

    assert stream_ended, "large response did not complete within the throughput floor"
    assert bytes(received) == payload
    assert elapsed < 2.0  # >1 MiB/s; regression was approximately 7 KiB/s


@pytest.mark.timeout(5)
async def test_h2_slow_peer_large_body_integrity_and_throughput() -> None:
    """Slow WINDOW_UPDATE cadence preserves bytes past idle timeouts (#241)."""
    from pounce._h2_handler import handle_h2_connection

    payload = bytes(range(256)) * 8192  # 2 MiB with a truncation-visible pattern

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(payload)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": payload})

    client = _make_client()
    client.send_headers(
        1,
        [
            (":method", "GET"),
            (":path", "/slow-peer"),
            (":authority", "example.test"),
            (":scheme", "https"),
        ],
        end_stream=True,
    )
    reader = asyncio.StreamReader()
    reader.feed_data(client.data_to_send())
    writer = _FakeWriter()
    task = asyncio.create_task(
        handle_h2_connection(
            app,
            ServerConfig(keep_alive_timeout=0.01, access_log=False),
            logging.getLogger("test.h2"),
            reader,
            writer,
            ("127.0.0.1", 50000),
            ("127.0.0.1", 8443),
            "127.0.0.1:50000",
        )
    )

    received = bytearray()
    consumed = 0
    stream_ended = False
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + 2.0
    while not stream_ended and loop.time() < deadline:
        # Deliberately exceed keep_alive_timeout while the peer neither reads
        # nor replenishes flow control.  A 2 MiB body exhausts the default
        # 65,535-byte windows many times, so both regressions are exercised.
        await asyncio.sleep(0.02)
        output = bytes(writer.data[consumed:])
        consumed += len(output)
        for event in client.receive_data(output):
            if isinstance(event, h2.events.DataReceived):
                received.extend(event.data)
                client.acknowledge_received_data(
                    event.flow_controlled_length,
                    event.stream_id,
                )
            elif isinstance(event, h2.events.StreamEnded):
                stream_ended = True
        feedback = client.data_to_send()
        if feedback:
            reader.feed_data(feedback)

    elapsed = loop.time() - started
    reader.feed_eof()
    await asyncio.wait_for(task, timeout=0.5)

    assert stream_ended, "slow peer did not receive the complete H2 stream"
    assert bytes(received) == payload
    assert elapsed < 2.0  # >1 MiB/s despite the deliberately delayed peer cadence
