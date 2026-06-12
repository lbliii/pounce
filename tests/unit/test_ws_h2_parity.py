"""WebSocket-over-HTTP/2 (RFC 8441) parity with the HTTP/1.1 WS path (#115).

These tests exercise ``handle_h2_websocket_stream`` directly against a fake
``H2Connection`` that records the response-header / data / reset calls. The
real ``WSProtocol`` is used to frame/deframe so the assertions reflect actual
wire behaviour. They mirror the H1 guarantees:

* ``websocket_max_message_size`` is enforced (WS 1009 close + RST stream)
* ``websocket.send`` before ``websocket.accept`` raises (accept guard)
* permessage-deflate is negotiated from the Extended CONNECT headers
* the ``websocket.http.response.start`` / ``.body`` reject path works
"""

import asyncio
import logging

import pytest

# Skip everything if the optional deps are missing.
pytest.importorskip("wsproto")

import wsproto.connection
import wsproto.events

from pounce._h2_handler import handle_h2_websocket_stream
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived

logger = logging.getLogger("test.ws-h2")


class _FakeWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        return None


class _FakeH2Connection:
    """Records the response-shaping calls the WS handler makes.

    ``send_data`` payloads are the raw WebSocket frame bytes produced by the
    server-side ``WSProtocol`` (they are *not* h2-framed here — that is fine
    for asserting handler behaviour). ``data_to_send`` returns nothing because
    the fake never wraps anything in HTTP/2 frames.
    """

    def __init__(self) -> None:
        self.response_headers: list[tuple[int, list[tuple[bytes, bytes]], bool]] = []
        self.data_frames: list[tuple[bytes, bool]] = []
        self.reset_streams: list[int] = []

    def send_response_headers(
        self,
        stream_id: int,
        status: int,
        headers: list[tuple[bytes, bytes]],
        *,
        end_stream: bool = False,
    ) -> None:
        self.response_headers.append((status, list(headers), end_stream))

    def send_data(self, stream_id: int, data: bytes, *, end_stream: bool = False) -> None:
        self.data_frames.append((data, end_stream))

    def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        self.reset_streams.append(stream_id)

    def data_to_send(self) -> bytes:
        return b""

    # -- test helpers -------------------------------------------------------

    @property
    def status_codes(self) -> list[int]:
        return [status for status, _headers, _end in self.response_headers]

    def accept_header(self, name: bytes) -> bytes | None:
        for status, headers, _end in self.response_headers:
            if status != 200:
                continue
            for hname, hvalue in headers:
                if hname.lower() == name.lower():
                    return hvalue
        return None


def _connect_request(extensions: bytes | None = None) -> RequestReceived:
    headers: list[tuple[bytes, bytes]] = [
        (b"host", b"example.test"),
        (b"sec-websocket-version", b"13"),
    ]
    if extensions is not None:
        headers.append((b"sec-websocket-extensions", extensions))
    return RequestReceived(
        method=b"CONNECT",
        target=b"/ws",
        headers=tuple(headers),
        http_version="2",
    )


async def _drive(
    app,
    config: ServerConfig,
    request: RequestReceived,
    inbound_frames: list[bytes],
) -> _FakeH2Connection:
    """Run the H2 WS handler, feeding inbound WS frames as H2 body messages."""
    h2_conn = _FakeH2Connection()
    writer = _FakeWriter()
    data_queue: asyncio.Queue[dict] = asyncio.Queue()
    for frame in inbound_frames:
        data_queue.put_nowait({"type": "http.request", "body": frame, "more_body": True})

    await asyncio.wait_for(
        handle_h2_websocket_stream(
            app,
            config,
            logger,
            h2_conn,
            stream_id=1,
            request=request,
            data_queue=data_queue,
            writer=writer,  # type: ignore[arg-type]
            client=("127.0.0.1", 50000),
            server=("127.0.0.1", 8443),
            client_str="127.0.0.1:50000",
        ),
        timeout=5.0,
    )
    return h2_conn


# ---------------------------------------------------------------------------
# Size-limit enforcement (parity with _ws_handler.py:153-159)
# ---------------------------------------------------------------------------


async def test_h2_ws_oversize_message_closes_1009_and_resets_stream() -> None:
    """An inbound message over websocket_max_message_size triggers 1009 + RST."""
    accepted = asyncio.Event()

    async def app(scope, receive, send) -> None:
        assert (await receive())["type"] == "websocket.connect"
        await send({"type": "websocket.accept"})
        accepted.set()
        # Keep receiving until disconnected so the stream stays open.
        while True:
            msg = await receive()
            if msg["type"] == "websocket.disconnect":
                return

    client = wsproto.connection.Connection(wsproto.connection.ConnectionType.CLIENT)
    big = client.send(wsproto.events.BytesMessage(data=b"x" * 64))

    config = ServerConfig(
        websocket_max_message_size=16,
        websocket_compression=False,
        access_log=False,
    )
    h2_conn = await _drive(app, config, _connect_request(), [big])

    # A WS close frame was sent and the stream was RST.
    assert h2_conn.reset_streams == [1]
    # The server framed a 1009 close — decode it with a client connection.
    decoder = wsproto.connection.Connection(wsproto.connection.ConnectionType.CLIENT)
    close_events = []
    for data, _end in h2_conn.data_frames:
        decoder.receive_data(data)
        close_events.extend(decoder.events())
    codes = [e.code for e in close_events if isinstance(e, wsproto.events.CloseConnection)]
    assert 1009 in codes


async def test_h2_ws_under_limit_message_delivered() -> None:
    """A message within the limit is delivered to the app (control case)."""
    received: list[bytes] = []

    async def app(scope, receive, send) -> None:
        await receive()
        await send({"type": "websocket.accept"})
        msg = await receive()
        if msg["type"] == "websocket.receive":
            received.append(msg.get("bytes") or msg.get("text"))

    client = wsproto.connection.Connection(wsproto.connection.ConnectionType.CLIENT)
    frame = client.send(wsproto.events.BytesMessage(data=b"ok"))

    config = ServerConfig(
        websocket_max_message_size=1024,
        websocket_compression=False,
        access_log=False,
    )
    h2_conn = await _drive(app, config, _connect_request(), [frame])
    assert received == [b"ok"]
    assert h2_conn.reset_streams == []


# ---------------------------------------------------------------------------
# Accept guard (parity with ws_bridge.py:144-147)
# ---------------------------------------------------------------------------


async def test_h2_ws_send_before_accept_raises() -> None:
    """websocket.send before websocket.accept must raise RuntimeError."""
    captured: list[BaseException] = []

    async def app(scope, receive, send) -> None:
        await receive()
        try:
            await send({"type": "websocket.send", "text": "early"})
        except RuntimeError as exc:  # pragma: no branch
            captured.append(exc)
            # Accept then close so the handler unwinds cleanly.
            await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})

    config = ServerConfig(websocket_compression=False, access_log=False)
    await _drive(app, config, _connect_request(), [])

    assert len(captured) == 1
    assert "before websocket.accept" in str(captured[0])


async def test_h2_ws_send_after_close_raises() -> None:
    """websocket.send after websocket.close must raise RuntimeError."""
    captured: list[BaseException] = []

    async def app(scope, receive, send) -> None:
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 1000})
        try:
            await send({"type": "websocket.send", "text": "late"})
        except RuntimeError as exc:  # pragma: no branch
            captured.append(exc)

    config = ServerConfig(websocket_compression=False, access_log=False)
    await _drive(app, config, _connect_request(), [])

    assert len(captured) == 1
    assert "after websocket.close" in str(captured[0])


# ---------------------------------------------------------------------------
# Compression negotiation echoed in the 200 acceptance headers
# ---------------------------------------------------------------------------


async def test_h2_ws_compression_negotiated_and_echoed() -> None:
    """A permessage-deflate offer is echoed in the 200 acceptance headers."""

    async def app(scope, receive, send) -> None:
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 1000})

    config = ServerConfig(websocket_compression=True, access_log=False)
    request = _connect_request(b"permessage-deflate; client_max_window_bits=10")
    h2_conn = await _drive(app, config, request, [])

    assert 200 in h2_conn.status_codes
    echoed = h2_conn.accept_header(b"sec-websocket-extensions")
    assert echoed == b"permessage-deflate; client_max_window_bits=10"


async def test_h2_ws_compression_disabled_when_config_off() -> None:
    """No extensions header is echoed when websocket_compression is off."""

    async def app(scope, receive, send) -> None:
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 1000})

    config = ServerConfig(websocket_compression=False, access_log=False)
    request = _connect_request(b"permessage-deflate")
    h2_conn = await _drive(app, config, request, [])

    assert 200 in h2_conn.status_codes
    assert h2_conn.accept_header(b"sec-websocket-extensions") is None


async def test_h2_ws_no_offer_no_extensions_header() -> None:
    """When the client makes no offer, no extensions header is echoed."""

    async def app(scope, receive, send) -> None:
        await receive()
        await send({"type": "websocket.accept"})
        await send({"type": "websocket.close", "code": 1000})

    config = ServerConfig(websocket_compression=True, access_log=False)
    h2_conn = await _drive(app, config, _connect_request(), [])

    assert 200 in h2_conn.status_codes
    assert h2_conn.accept_header(b"sec-websocket-extensions") is None


# ---------------------------------------------------------------------------
# Reject path (parity with ws_bridge.py:180-221)
# ---------------------------------------------------------------------------


async def test_h2_ws_reject_via_http_response() -> None:
    """websocket.http.response.start/.body sends a non-200 HTTP response."""

    async def app(scope, receive, send) -> None:
        await receive()
        await send(
            {
                "type": "websocket.http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send(
            {
                "type": "websocket.http.response.body",
                "body": b"nope",
                "more_body": False,
            }
        )

    config = ServerConfig(websocket_compression=False, access_log=False)
    h2_conn = await _drive(app, config, _connect_request(), [])

    # The acceptance 200 must NOT have been sent; a 401 was sent instead.
    assert 200 not in h2_conn.status_codes
    assert 401 in h2_conn.status_codes
    # Body was delivered and the stream ended.
    assert (b"nope", True) in h2_conn.data_frames


async def test_h2_ws_reject_via_close_before_accept_sends_403() -> None:
    """websocket.close before accept rejects with 403 (no 200 acceptance)."""

    async def app(scope, receive, send) -> None:
        await receive()
        await send({"type": "websocket.close", "code": 1000})

    config = ServerConfig(websocket_compression=False, access_log=False)
    h2_conn = await _drive(app, config, _connect_request(), [])

    assert 200 not in h2_conn.status_codes
    assert 403 in h2_conn.status_codes


async def test_h2_ws_reject_non_int_status_falls_back_to_403() -> None:
    """A non-int rejection status cannot inject and falls back to 403."""

    async def app(scope, receive, send) -> None:
        await receive()
        await send(
            {
                "type": "websocket.http.response.start",
                "status": "401\r\nX-Injected: 1",
                "headers": [],
            }
        )
        await send({"type": "websocket.http.response.body", "body": b"", "more_body": False})

    config = ServerConfig(websocket_compression=False, access_log=False)
    h2_conn = await _drive(app, config, _connect_request(), [])

    assert 403 in h2_conn.status_codes
    assert 200 not in h2_conn.status_codes
