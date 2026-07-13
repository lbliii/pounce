"""Tests for pounce.sync_worker — SyncWorker unit tests.

Covers:
- Sprint 0: Mock socket fixture + harness
- Sprint 1: Error paths (chunked, 413, SSL, HTTP/1.0, keep-alive, max requests, app exception, sendmsg)
- Sprint 2: Handoff paths (WebSocket, streaming, health check, compression)
"""

import asyncio
import queue
import socket
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.async_pool import StreamingHandoff, WebSocketHandoff
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived
from pounce.sync_protocol import RawRequest, RawResponse, SyncApp
from pounce.sync_worker import SyncWorker, _classify_request, _wants_100_continue

# ---------------------------------------------------------------------------
# Fixtures: mock socket and helpers
# ---------------------------------------------------------------------------


class _MockSocketBase:
    """Base fake socket for SyncWorker unit tests.

    Feeds pre-built bytes to recv_into and captures bytes sent via
    sendall/sendmsg.  Supports controllable errors and timeouts.
    """

    def __init__(
        self,
        recv_data: bytes = b"",
        *,
        peername: tuple[str, int] = ("127.0.0.1", 54321),
        sockname: tuple[str, int] = ("127.0.0.1", 8000),
        recv_error: type[Exception] | None = None,
        sendmsg_error: type[Exception] | None = None,
        sendall_error: type[Exception] | None = None,
    ) -> None:
        self._recv_data = recv_data
        self._recv_offset = 0
        self._peername = peername
        self._sockname = sockname
        self._recv_error = recv_error
        self._sendmsg_error = sendmsg_error
        self._sendall_error = sendall_error
        self.sent_data = bytearray()
        self.closed = False
        self.family = socket.AF_INET
        self.timeout: float | None = None

    def recv_into(self, buf: memoryview | bytearray) -> int:
        if self._recv_error:
            raise self._recv_error()
        remaining = self._recv_data[self._recv_offset :]
        if not remaining:
            return 0
        n = min(len(buf), len(remaining))
        buf[:n] = remaining[:n]
        self._recv_offset += n
        return n

    def sendall(self, data: bytes | bytearray) -> None:
        if self._sendall_error:
            raise self._sendall_error()
        self.sent_data.extend(data)

    def getpeername(self) -> tuple[str, int]:
        return self._peername

    def getsockname(self) -> tuple[str, int]:
        return self._sockname

    def settimeout(self, timeout: float | None) -> None:
        self.timeout = timeout

    def setblocking(self, flag: bool) -> None:
        pass

    def setsockopt(self, level: int, optname: int, value: int) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class MockSocket(_MockSocketBase):
    """Mock socket with sendmsg support (the common case)."""

    def sendmsg(self, buffers: list[bytes | bytearray]) -> int:
        if self._sendmsg_error:
            raise self._sendmsg_error()
        total = 0
        for buf in buffers:
            self.sent_data.extend(buf)
            total += len(buf)
        return total


class MockSocketNoSendmsg(_MockSocketBase):
    """Mock socket without sendmsg (e.g., SSL-wrapped sockets)."""


class PartialSendmsgSocket(_MockSocketBase):
    """Mock a legal partial ``sendmsg`` result in the header or body."""

    def __init__(self, recv_data: bytes, *, partial_in: str) -> None:
        super().__init__(recv_data)
        self.partial_in = partial_in
        self.sendmsg_calls = 0
        self.sendall_calls = 0

    def sendmsg(self, buffers: list[bytes | bytearray]) -> int:
        self.sendmsg_calls += 1
        head, body = buffers
        if self.partial_in == "header":
            accepted = min(7, len(head))
        else:
            accepted = len(head) + max(1, len(body) // 2)
        data = bytes(head) + bytes(body)
        self.sent_data.extend(data[:accepted])
        return accepted

    def sendall(self, data: bytes | bytearray) -> None:
        self.sendall_calls += 1
        super().sendall(data)


def _build_http_request(
    method: str = "GET",
    path: str = "/",
    http_version: str = "1.1",
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> bytes:
    """Build a raw HTTP request as bytes."""
    hdrs = headers or {}
    lines = [f"{method} {path} HTTP/{http_version}"]
    for name, value in hdrs.items():
        lines.append(f"{name}: {value}")
    if body and "content-length" not in {k.lower() for k in hdrs}:
        lines.append(f"Content-Length: {len(body)}")
    header_block = "\r\n".join(lines) + "\r\n\r\n"
    return header_block.encode("ascii") + body


def _make_config(**overrides: Any) -> ServerConfig:
    """Create a ServerConfig with test defaults."""
    defaults = {
        "host": "127.0.0.1",
        "port": 8000,
        "access_log": False,
        "date_header": False,
        "server_header": "pounce-test",
        "keep_alive_timeout": 5.0,
        "header_timeout": 10.0,
        "max_headers": 100,
        "max_requests_per_connection": 0,
    }
    defaults.update(overrides)
    return ServerConfig(**defaults)


def _make_worker(
    config: ServerConfig | None = None,
    app: ASGIApp | None = None,
    *,
    async_pool: Any = None,
    sync_app: SyncApp | None = None,
    shutdown_event: threading.Event | None = None,
) -> SyncWorker:
    """Create a SyncWorker with test defaults."""
    if config is None:
        config = _make_config()

    if app is None:
        app = _simple_asgi_app

    return SyncWorker(
        config=config,
        app=app,
        sock=None,
        worker_id=0,
        shutdown_event=shutdown_event,
        async_pool=async_pool,
        sync_app=sync_app,
    )


async def _simple_asgi_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Simple ASGI app that returns 200 OK."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok", "more_body": False})


async def _error_asgi_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that raises an exception."""
    raise RuntimeError("App crashed!")


async def _streaming_asgi_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that returns a streaming response (triggers NeedsAsyncError)."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"chunk1", "more_body": True})


async def _echo_body_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Reads the full request body and echoes it back (200)."""
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


# ---------------------------------------------------------------------------
# Sprint 0: _classify_request unit tests
# ---------------------------------------------------------------------------


class TestClassifyRequest:
    """Tests for the single-pass header classifier."""

    def test_basic_get_no_special_headers(self) -> None:
        req = RequestReceived(
            method=b"GET",
            target=b"/",
            headers=((b"host", b"localhost"),),
            http_version="1.1",
        )
        meta = _classify_request(req)
        assert not meta.wants_close
        assert not meta.is_websocket
        assert meta.accept_encoding is None

    def test_connection_close(self) -> None:
        req = RequestReceived(
            method=b"GET",
            target=b"/",
            headers=((b"connection", b"close"),),
            http_version="1.1",
        )
        meta = _classify_request(req)
        assert meta.wants_close

    def test_websocket_upgrade(self) -> None:
        req = RequestReceived(
            method=b"GET",
            target=b"/ws",
            headers=(
                (b"connection", b"Upgrade"),
                (b"upgrade", b"websocket"),
            ),
            http_version="1.1",
        )
        meta = _classify_request(req)
        assert meta.is_websocket

    def test_websocket_needs_both_headers(self) -> None:
        req = RequestReceived(
            method=b"GET",
            target=b"/ws",
            headers=((b"upgrade", b"websocket"),),
            http_version="1.1",
        )
        meta = _classify_request(req)
        assert not meta.is_websocket

    def test_accept_encoding_extracted(self) -> None:
        req = RequestReceived(
            method=b"GET",
            target=b"/",
            headers=((b"accept-encoding", b"gzip, zstd"),),
            http_version="1.1",
        )
        meta = _classify_request(req)
        assert meta.accept_encoding == b"gzip, zstd"

    def test_http10_defaults_to_close(self) -> None:
        req = RequestReceived(
            method=b"GET",
            target=b"/",
            headers=((b"host", b"localhost"),),
            http_version="1.0",
        )
        meta = _classify_request(req)
        assert meta.wants_close

    def test_http10_with_keepalive_does_not_close(self) -> None:
        req = RequestReceived(
            method=b"GET",
            target=b"/",
            headers=((b"connection", b"keep-alive"),),
            http_version="1.0",
        )
        meta = _classify_request(req)
        assert not meta.wants_close


# ---------------------------------------------------------------------------
# Sprint 1: Error paths
# ---------------------------------------------------------------------------


class TestSyncWorkerErrorPaths:
    """Tests for error/edge-case code paths in SyncWorker."""

    def test_chunked_encoding_returns_501(self) -> None:
        """Transfer-Encoding: chunked gets 501 and connection closes."""
        # Build a request with Transfer-Encoding: chunked — the fast parser
        # returns chunked=True, and SyncWorker should send 501.
        request_bytes = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"5\r\nhello\r\n0\r\n\r\n"
        )
        mock_sock = MockSocket(request_bytes)
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"501" in response
        assert b"Chunked" in response or b"chunked" in response.lower()

    def test_oversized_headers_returns_400(self) -> None:
        """Headers exceeding config max_header_size get 400 Bad Request."""
        # Use a small max_header_size to test the parser limit specifically
        config = _make_config(max_header_size=16384)
        huge_header = b"GET / HTTP/1.1\r\nHost: localhost\r\n" + b"X-Pad: " + b"A" * 20000
        mock_sock = MockSocket(huge_header)
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"400" in response

    def test_recv_timeout_closes_cleanly(self) -> None:
        """Connection close on recv timeout returns without crash."""
        mock_sock = MockSocket(b"", recv_error=TimeoutError)
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()
        # Should not raise, connection just closes
        assert mock_sock.closed

    def test_recv_connection_error_closes_cleanly(self) -> None:
        """Connection close on ConnectionError returns without crash."""
        mock_sock = MockSocket(b"", recv_error=ConnectionResetError)
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()
        assert mock_sock.closed

    def test_empty_recv_closes_connection(self) -> None:
        """recv returning 0 bytes (client closed) exits cleanly."""
        mock_sock = MockSocket(b"")
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()
        assert mock_sock.closed

    def test_asgi_app_exception_returns_500(self) -> None:
        """ASGI app that raises gets 500 error response."""
        request_bytes = _build_http_request()
        mock_sock = MockSocket(request_bytes)
        worker = _make_worker(app=_error_asgi_app)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"500" in response
        assert b"Internal Server Error" in response

    def test_max_requests_closes_connection(self) -> None:
        """Connection closes after max_requests_per_connection is reached."""
        # Two requests back-to-back, limit of 1
        two_requests = _build_http_request() + _build_http_request()
        mock_sock = MockSocket(two_requests)
        config = _make_config(max_requests_per_connection=1)
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        # Should only have one response (connection closed after first)
        assert response.count(b"HTTP/1.1 200") == 1
        # Connection header should say close
        assert b"connection: close" in response.lower()

    def test_keep_alive_timeout_differentiation(self) -> None:
        """First request uses header_timeout, subsequent use keep_alive_timeout."""
        # Use connection: close so we get exactly one request
        request_bytes = _build_http_request(headers={"Connection": "close"})
        mock_sock = MockSocket(request_bytes)
        config = _make_config(header_timeout=10.0, keep_alive_timeout=5.0)
        worker = _make_worker(config=config)
        runner = asyncio.Runner()

        timeouts_seen: list[float | None] = []
        orig_settimeout = mock_sock.settimeout

        def tracking_settimeout(t: float | None) -> None:
            timeouts_seen.append(t)
            orig_settimeout(t)

        mock_sock.settimeout = tracking_settimeout  # type: ignore[assignment]

        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        # First timeout set in _handle_connection_impl is header_timeout
        assert 10.0 in timeouts_seen

    def test_sendmsg_fallback_to_sendall(self) -> None:
        """When sendmsg raises OSError, falls back to sendall."""
        request_bytes = _build_http_request()
        mock_sock = MockSocket(request_bytes, sendmsg_error=OSError)
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"HTTP/1.1 200" in response

    def test_no_sendmsg_uses_sendall(self) -> None:
        """Socket without sendmsg attribute uses sendall directly."""
        request_bytes = _build_http_request()
        mock_sock = MockSocketNoSendmsg(request_bytes)
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"HTTP/1.1 200" in response

    @staticmethod
    def _assert_partial_sendmsg_is_completed(partial_in: str) -> None:
        request_bytes = _build_http_request(headers={"Connection": "close"})
        mock_sock = PartialSendmsgSocket(request_bytes, partial_in=partial_in)
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        head, separator, body = bytes(mock_sock.sent_data).partition(b"\r\n\r\n")
        assert separator == b"\r\n\r\n"
        assert b"HTTP/1.1 200" in head
        assert b"content-length: 2" in head.lower()
        assert body == b"ok"
        assert mock_sock.sendmsg_calls == 1
        assert mock_sock.sendall_calls >= 1

    @pytest.mark.issue(312)
    def test_partial_sendmsg_in_header_is_completed(self) -> None:
        """A partial header write cannot drop the header suffix or body."""
        self._assert_partial_sendmsg_is_completed("header")

    @pytest.mark.issue(312)
    def test_partial_sendmsg_in_body_is_completed(self) -> None:
        """A partial body write resumes at the exact unsent body offset."""
        self._assert_partial_sendmsg_is_completed("body")

    def test_parse_error_returns_400(self) -> None:
        """Malformed request gets 400 Bad Request."""
        # Invalid HTTP (no version)
        bad_request = b"GET /\r\nHost: localhost\r\n\r\n"
        mock_sock = MockSocket(bad_request)
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"400" in response

    def test_duplicate_host_returns_400(self) -> None:
        """Duplicate Host header gets 400 (smuggling / routing-desync parity, #119)."""
        bad_request = b"GET / HTTP/1.1\r\nHost: a.example\r\nHost: b.example\r\n\r\n"
        mock_sock = MockSocket(bad_request)
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"400" in response
        # Surfaces the semantic diagnostic code via the error header.
        assert b"POUNCE_PARSE_DUPLICATE_HOST" in response

    def test_crlf_header_sanitized_not_dropped(self) -> None:
        """App-supplied CRLF in a response header is sanitized, not a dropped connection (#120)."""

        async def _crlf_header_app(scope: Scope, receive: Receive, send: Send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"x-evil", b"value\r\nInjected: yes"),
                        (b"x-ok", b"fine"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        request_bytes = _build_http_request(headers={"Host": "localhost"})
        mock_sock = MockSocket(request_bytes)
        worker = _make_worker(app=_crlf_header_app)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        # Response is delivered normally (no abrupt drop / unhandled error).
        assert b"200" in response
        assert b"ok" in response
        # The CRLF was stripped, so the injected text is NOT on its own
        # header line — it is collapsed into the x-evil value instead.
        assert b"\r\nInjected: yes" not in response
        assert b"\r\ninjected: yes" not in response.lower()
        assert b"x-evil: valueinjected: yes" in response.lower()


class _StagedExpectSocket(_MockSocketBase):
    """Socket that withholds the body until ``100 Continue`` is observed (#122).

    Mimics a compliant ``Expect: 100-continue`` client: ``recv_into`` first
    delivers only the request headers. The body is *not* released until the
    server has written the interim ``100 Continue`` line, proving the server
    sends it before reading the withheld body. Send/recv events are recorded
    in order so the test can assert the interim status precedes body delivery.
    """

    def __init__(self, headers: bytes, body: bytes) -> None:
        super().__init__(b"")
        self._headers = headers
        self._body = body
        self._headers_sent = False
        self._continue_seen = False
        self._body_sent = False
        # Ordered log of ("send100", ...) / ("recv_body", ...) events.
        self.events: list[str] = []

    def _note_send(self, data: bytes | bytearray) -> None:
        self.sent_data.extend(data)
        if not self._continue_seen and b"100 Continue" in bytes(self.sent_data):
            self._continue_seen = True
            self.events.append("send100")

    def sendall(self, data: bytes | bytearray) -> None:
        self._note_send(data)

    def sendmsg(self, buffers: list[bytes | bytearray]) -> int:
        total = 0
        for buf in buffers:
            self._note_send(buf)
            total += len(buf)
        return total

    def recv_into(self, buf: memoryview | bytearray) -> int:
        if not self._headers_sent:
            self._headers_sent = True
            n = len(self._headers)
            buf[:n] = self._headers
            return n
        # Body is withheld until the server emits 100 Continue.
        if self._continue_seen and not self._body_sent:
            self._body_sent = True
            self.events.append("recv_body")
            n = len(self._body)
            buf[:n] = self._body
            return n
        # No more data (request fully delivered).
        return 0


class TestSyncWorkerExpect100Continue:
    """Sync worker emits 100 Continue before reading a withheld body (#122)."""

    def test_sends_100_continue_before_body(self) -> None:
        payload = b"hello-body"
        headers = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Expect: 100-continue\r\n"
            b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        )
        mock_sock = _StagedExpectSocket(headers, payload)
        worker = _make_worker(app=_echo_body_app)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        # The interim 100 Continue line was written...
        assert b"HTTP/1.1 100 Continue\r\n\r\n" in response
        # ...and the final response (with the echoed body) followed.
        assert b"HTTP/1.1 200" in response
        assert payload in response
        # Crucially: 100 Continue was emitted BEFORE the body was read off the
        # socket. Without the fix the body recv blocks first (or never sends
        # 100 at all), so this ordering would fail.
        assert mock_sock.events == ["send100", "recv_body"]

    def test_wants_100_continue_detection(self) -> None:
        """Line-anchored Expect detection: real header yes, stray substring no."""
        # Real Expect header (request line + header, no trailing blank line).
        block = b"POST / HTTP/1.1\r\nHost: x\r\nExpect: 100-continue"
        assert _wants_100_continue(block) is True
        # Case-insensitive.
        assert _wants_100_continue(b"POST / HTTP/1.1\r\nexpect:  100-CONTINUE") is True
        # No Expect header.
        assert _wants_100_continue(b"POST / HTTP/1.1\r\nHost: x") is False
        # ``expect:`` appearing inside another header value must NOT match.
        stray = b"POST / HTTP/1.1\r\nX-Note: please expect: 100-continue soon"
        assert _wants_100_continue(stray) is False

    def test_no_100_continue_without_expect_header(self) -> None:
        """A normal body request (no Expect) gets no interim 100 line."""
        payload = b"plain"
        request = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n" + payload
        )
        mock_sock = MockSocket(request)
        worker = _make_worker(app=_echo_body_app)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"100 Continue" not in response
        assert b"HTTP/1.1 200" in response
        assert payload in response


# ---------------------------------------------------------------------------
# Sprint 2: Handoff paths
# ---------------------------------------------------------------------------


class TestSyncWorkerHandoffs:
    """Tests for async pool handoff code paths."""

    def test_websocket_handoff_with_pool(self) -> None:
        """WebSocket upgrade hands off to async pool when available."""
        request_bytes = _build_http_request(
            headers={
                "Connection": "Upgrade",
                "Upgrade": "websocket",
            }
        )
        mock_sock = MockSocket(request_bytes)
        mock_pool = MagicMock()
        worker = _make_worker(async_pool=mock_pool)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        mock_pool.accept_handoff.assert_called_once()
        handoff = mock_pool.accept_handoff.call_args[0][0]
        assert isinstance(handoff, WebSocketHandoff)
        # Connection should NOT be closed (handed off)
        assert not mock_sock.closed

    def test_websocket_without_pool_returns_501(self) -> None:
        """WebSocket upgrade without async pool returns 501."""
        request_bytes = _build_http_request(
            headers={
                "Connection": "Upgrade",
                "Upgrade": "websocket",
            }
        )
        mock_sock = MockSocket(request_bytes)
        worker = _make_worker(async_pool=None)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"501" in response
        assert b"WebSocket" in response

    def test_streaming_needs_async_handoff_with_pool(self) -> None:
        """NeedsAsyncError from call_asgi_sync hands off to async pool."""
        request_bytes = _build_http_request()
        mock_sock = MockSocket(request_bytes)
        mock_pool = MagicMock()
        worker = _make_worker(app=_streaming_asgi_app, async_pool=mock_pool)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        mock_pool.accept_handoff.assert_called_once()
        handoff = mock_pool.accept_handoff.call_args[0][0]
        assert isinstance(handoff, StreamingHandoff)
        assert not mock_sock.closed

    def test_streaming_without_pool_returns_501(self) -> None:
        """NeedsAsyncError without async pool returns 501."""
        request_bytes = _build_http_request()
        mock_sock = MockSocket(request_bytes)
        worker = _make_worker(app=_streaming_asgi_app, async_pool=None)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"501" in response

    def test_needs_async_response_flag_handoff(self) -> None:
        """Response with needs_async=True hands off to async pool."""

        async def app_that_sets_needs_async(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"chunk", "more_body": True})

        request_bytes = _build_http_request()
        mock_sock = MockSocket(request_bytes)
        mock_pool = MagicMock()
        worker = _make_worker(app=app_that_sets_needs_async, async_pool=mock_pool)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        mock_pool.accept_handoff.assert_called_once()

    def test_health_check_returns_200_json(self) -> None:
        """Configured health check path returns JSON health response."""
        request_bytes = _build_http_request(path="/healthz")
        mock_sock = MockSocket(request_bytes)
        config = _make_config(health_check_path="/healthz")
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"200" in response
        assert b"application/json" in response
        assert b'"status"' in response

    def test_health_check_only_matches_get(self) -> None:
        """Health check path rejects POST, which falls through to ASGI."""
        request_bytes = _build_http_request(method="POST", path="/healthz", body=b"x")
        mock_sock = MockSocket(request_bytes)
        config = _make_config(health_check_path="/healthz")
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        # Should get ASGI response (200 "ok"), not health check JSON
        assert b"200" in response

    def test_health_check_head_preserves_get_headers_without_body(self) -> None:
        request_bytes = _build_http_request(method="HEAD", path="/readyz")
        mock_sock = MockSocket(request_bytes)
        config = _make_config(health_check_path="/readyz")
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        head, separator, body = bytes(mock_sock.sent_data).partition(b"\r\n\r\n")
        assert separator
        assert b"HTTP/1.1 200" in head
        assert b"content-type: application/json" in head.lower()
        assert b"content-length:" in head.lower()
        assert body == b""

    def test_health_check_non_matching_path_falls_through(self) -> None:
        """Non-matching path goes to ASGI even when health_check_path is set."""
        request_bytes = _build_http_request(path="/api/data")
        mock_sock = MockSocket(request_bytes)
        config = _make_config(health_check_path="/healthz")
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"200" in response

    def test_health_check_response_keeps_alive(self) -> None:
        """Health check response uses keep-alive (doesn't force close)."""
        request_bytes = _build_http_request(path="/healthz")
        mock_sock = MockSocket(request_bytes)
        config = _make_config(health_check_path="/healthz")
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"200" in response
        assert b"connection: keep-alive" in response.lower()

    def test_introspection_bypasses_asgi_and_reports_worker(self) -> None:
        """The shared built-in dispatcher serves introspection in sync mode."""
        request_bytes = _build_http_request(path="/_pounce/info")
        mock_sock = MockSocket(request_bytes)
        config = _make_config(introspection_enabled=True)
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"200" in response
        assert b"application/json" in response
        assert b'"worker_id": 0' in response
        assert b'"config"' in response


# ---------------------------------------------------------------------------
# Sprint 2: Compression negotiation
# ---------------------------------------------------------------------------


class TestSyncWorkerCompression:
    """Tests for compression negotiation in the ASGI response path."""

    def test_gzip_compression_applied(self) -> None:
        """Accept-Encoding: gzip produces compressed response."""
        request_bytes = _build_http_request(
            headers={"Accept-Encoding": "gzip", "Connection": "close"}
        )
        mock_sock = MockSocket(request_bytes)
        config = _make_config(compression=True, compression_min_size=1)
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"content-encoding: gzip" in response.lower()

    def test_no_compression_when_disabled(self) -> None:
        """compression=False skips encoding even with Accept-Encoding."""
        request_bytes = _build_http_request(
            headers={"Accept-Encoding": "gzip", "Connection": "close"}
        )
        mock_sock = MockSocket(request_bytes)
        config = _make_config(compression=False)
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"content-encoding" not in response.lower()

    def test_no_compression_without_accept_encoding(self) -> None:
        """No Accept-Encoding header means no compression."""
        request_bytes = _build_http_request(headers={"Connection": "close"})
        mock_sock = MockSocket(request_bytes)
        config = _make_config(compression=True, compression_min_size=1)
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"content-encoding" not in response.lower()

    def test_identity_encoding_no_compression(self) -> None:
        """Accept-Encoding: identity results in no compression."""
        request_bytes = _build_http_request(
            headers={"Accept-Encoding": "identity", "Connection": "close"}
        )
        mock_sock = MockSocket(request_bytes)
        config = _make_config(compression=True, compression_min_size=1)
        worker = _make_worker(config=config)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"content-encoding" not in response.lower()

    def test_already_encoded_asgi_response_not_compressed(self) -> None:
        """ASGI responses with Content-Encoding are not double-compressed."""

        async def encoded_app(scope: Scope, receive: Receive, send: Send) -> None:
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-encoding", b"br"),
                        (b"content-length", b"5"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"hello", "more_body": False})

        request_bytes = _build_http_request(
            headers={"Accept-Encoding": "gzip", "Connection": "close"}
        )
        mock_sock = MockSocket(request_bytes)
        config = _make_config(compression=True, compression_min_size=1)
        worker = _make_worker(config=config, app=encoded_app)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"content-encoding: br" in response.lower()
        assert b"content-encoding: gzip" not in response.lower()
        assert response.endswith(b"\r\n\r\nhello")


# ---------------------------------------------------------------------------
# Sprint 2: SyncApp fast path
# ---------------------------------------------------------------------------


class _StubSyncApp:
    """Test SyncApp that returns a fixed response."""

    def __init__(self, response: RawResponse | None = None) -> None:
        self._response = response
        self.requests: list[RawRequest] = []

    def handle_sync(self, request: RawRequest) -> RawResponse | None:
        self.requests.append(request)
        return self._response


class TestSyncAppPath:
    """Tests for the fused SyncApp fast path."""

    def test_sync_app_returns_response(self) -> None:
        """SyncApp.handle_sync() response bypasses ASGI."""
        raw_resp = RawResponse(
            status=200,
            headers=((b"content-type", b"text/plain"),),
            body=b"fast path",
        )
        sync_app = _StubSyncApp(raw_resp)
        request_bytes = _build_http_request()
        mock_sock = MockSocket(request_bytes)
        worker = _make_worker(sync_app=sync_app)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"200" in response
        assert b"fast path" in response
        assert len(sync_app.requests) == 1

    def test_sync_app_returns_none_falls_through(self) -> None:
        """SyncApp returning None falls through to ASGI path."""
        sync_app = _StubSyncApp(None)
        request_bytes = _build_http_request(headers={"Connection": "close"})
        mock_sock = MockSocket(request_bytes)
        worker = _make_worker(sync_app=sync_app)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"200" in response
        assert b"ok" in response
        assert len(sync_app.requests) == 1

    def test_sync_app_compression(self) -> None:
        """SyncApp path applies compression when configured."""
        raw_resp = RawResponse(
            status=200,
            headers=((b"content-type", b"text/plain"),),
            body=b"A" * 200,  # Must exceed min size
        )
        sync_app = _StubSyncApp(raw_resp)
        request_bytes = _build_http_request(
            headers={"Accept-Encoding": "gzip", "Connection": "close"}
        )
        mock_sock = MockSocket(request_bytes)
        config = _make_config(compression=True, compression_min_size=10)
        worker = _make_worker(config=config, sync_app=sync_app)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"content-encoding: gzip" in response.lower()

    def test_sync_app_already_encoded_response_not_compressed(self) -> None:
        """SyncApp responses with Content-Encoding are not double-compressed."""
        raw_resp = RawResponse(
            status=200,
            headers=((b"content-encoding", b"br"), (b"content-length", b"5")),
            body=b"hello",
        )
        sync_app = _StubSyncApp(raw_resp)
        request_bytes = _build_http_request(
            headers={"Accept-Encoding": "gzip", "Connection": "close"}
        )
        mock_sock = MockSocket(request_bytes)
        config = _make_config(compression=True, compression_min_size=1)
        worker = _make_worker(config=config, sync_app=sync_app)
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"content-encoding: br" in response.lower()
        assert b"content-encoding: gzip" not in response.lower()
        assert response.endswith(b"\r\n\r\nhello")


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnectionLifecycle:
    """Tests for connection-level behavior."""

    def test_active_connections_tracking(self) -> None:
        """Active connection count increments/decrements around handling."""
        request_bytes = _build_http_request(headers={"Connection": "close"})
        mock_sock = MockSocket(request_bytes)
        worker = _make_worker()
        assert worker.is_idle()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()
        assert worker.is_idle()

    def test_keep_alive_response_header(self) -> None:
        """Keep-alive request gets connection: keep-alive in response."""
        request_bytes = _build_http_request(path="/a")
        mock_sock = MockSocket(request_bytes)
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        response = bytes(mock_sock.sent_data)
        assert b"HTTP/1.1 200" in response
        assert b"connection: keep-alive" in response.lower()

    def test_shutdown_event_stops_loop(self) -> None:
        """Setting shutdown event breaks the keep-alive loop."""
        # Two requests, but shutdown fires after first
        req1 = _build_http_request(path="/a")
        req2 = _build_http_request(path="/b")
        mock_sock = MockSocket(req1 + req2)
        shutdown = threading.Event()
        worker = _make_worker(shutdown_event=shutdown)

        runner = asyncio.Runner()

        # Set shutdown after first recv completes
        original_recv = mock_sock.recv_into
        call_count = 0

        def counting_recv(buf: memoryview | bytearray) -> int:
            nonlocal call_count
            call_count += 1
            result = original_recv(buf)
            if call_count == 1:
                shutdown.set()
            return result

        mock_sock.recv_into = counting_recv  # type: ignore[assignment]

        try:
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

        # Should serve first request but stop before second
        response = bytes(mock_sock.sent_data)
        assert response.count(b"HTTP/1.1 200") == 1

    def test_connection_close_on_connection_error(self) -> None:
        """ConnectionError during request processing records disconnect."""
        # Provide a valid request but make sendall fail
        request_bytes = _build_http_request(headers={"Connection": "close"})
        mock_sock = MockSocket(request_bytes, sendall_error=ConnectionResetError)
        # Also need sendmsg to fail to test the full path
        mock_sock._sendmsg_error = OSError
        worker = _make_worker()
        runner = asyncio.Runner()
        try:
            # Should not raise — connection errors are caught
            worker._handle_connection(mock_sock, ("127.0.0.1", 54321), runner)
        finally:
            runner.close()

    def test_send_error_helper(self) -> None:
        """_send_error sends well-formed HTTP error response."""
        mock_sock = MockSocket(b"")
        worker = _make_worker()
        worker._send_error(mock_sock, 404, "Not Found")

        response = bytes(mock_sock.sent_data)
        assert b"HTTP/1.1 404" in response
        assert b"Not Found" in response
        assert b"connection: close" in response
        assert b"content-type: text/plain" in response

    def test_send_error_suppresses_os_error(self) -> None:
        """_send_error doesn't raise on sendall failure."""
        mock_sock = MockSocket(b"", sendall_error=OSError)
        worker = _make_worker()
        # Should not raise
        worker._send_error(mock_sock, 500, "Internal Server Error")


# ---------------------------------------------------------------------------
# Queue-based connection handling
# ---------------------------------------------------------------------------


class TestQueueBasedWorker:
    """Tests for the conn_queue path (_run_from_queue)."""

    def test_run_from_queue_handles_connection(self) -> None:
        """Worker processes connections from queue until shutdown."""
        request_bytes = _build_http_request(headers={"Connection": "close"})
        mock_sock = MockSocket(request_bytes)
        conn_queue: queue.Queue[tuple[socket.socket, object]] = queue.Queue()
        conn_queue.put((mock_sock, ("127.0.0.1", 54321)))  # type: ignore[arg-type]
        shutdown = threading.Event()

        worker = _make_worker(shutdown_event=shutdown, config=_make_config())
        worker._conn_queue = conn_queue

        # Run in a thread, shut down after a moment
        def run_and_stop() -> None:
            import time

            time.sleep(0.1)
            shutdown.set()

        stopper = threading.Thread(target=run_and_stop, daemon=True)
        stopper.start()

        runner = asyncio.Runner()
        try:
            worker._run_from_queue(0.05, runner)
        finally:
            runner.close()
        stopper.join(timeout=2)

        response = bytes(mock_sock.sent_data)
        assert b"HTTP/1.1 200" in response


# ---------------------------------------------------------------------------
# Issue #162: shared compression negotiation + _finalize_response_headers
# ---------------------------------------------------------------------------


class TestFinalizeResponseHeaders:
    """Unit tests for the local _finalize_response_headers helper.

    Behaviour must mirror the previously-inline SyncApp / ASGI rewrite blocks.
    """

    def _config(self, **overrides: Any) -> ServerConfig:
        return _make_config(**overrides)

    def test_no_compressor_appends_content_length(self) -> None:
        from pounce.sync_worker import _finalize_response_headers

        headers, body = _finalize_response_headers(
            [(b"content-type", b"text/plain")],
            b"hello",
            None,
            None,
            self._config(),
            apply_min_size=True,
        )
        assert body == b"hello"
        assert (b"content-length", b"5") in headers
        assert not any(n.lower() == b"content-encoding" for n, _ in headers)

    def test_existing_content_length_reappended_uncompressed(self) -> None:
        from pounce.sync_worker import _finalize_response_headers

        headers, body = _finalize_response_headers(
            [(b"content-type", b"text/plain"), (b"content-length", b"5")],
            b"hello",
            None,
            None,
            self._config(),
            apply_min_size=True,
        )
        assert body == b"hello"
        # The pre-set content-length is preserved exactly once.
        cls = [v for n, v in headers if n.lower() == b"content-length"]
        assert cls == [b"5"]

    def test_compression_applied_above_threshold(self) -> None:
        from pounce._compression import create_compressor
        from pounce.sync_worker import _finalize_response_headers

        body_in = b"A" * 200
        compressor = create_compressor("gzip")
        assert compressor is not None
        headers, body = _finalize_response_headers(
            [(b"content-type", b"text/plain")],
            body_in,
            compressor,
            None,
            self._config(compression=True, compression_min_size=10),
            apply_min_size=True,
        )
        assert body != body_in  # compressed
        assert (b"content-encoding", b"gzip") in headers
        assert (b"content-length", str(len(body)).encode()) in headers

    def test_sub_threshold_not_compressed_when_min_size_applies(self) -> None:
        from pounce._compression import create_compressor
        from pounce.sync_worker import _finalize_response_headers

        body_in = b"tiny"
        compressor = create_compressor("gzip")
        assert compressor is not None
        headers, body = _finalize_response_headers(
            [(b"content-type", b"text/plain")],
            body_in,
            compressor,
            None,
            self._config(compression=True, compression_min_size=500),
            apply_min_size=True,
        )
        # Below compression_min_size: SyncApp-style path leaves it uncompressed.
        assert body == body_in
        assert not any(n.lower() == b"content-encoding" for n, _ in headers)
        assert (b"content-length", b"4") in headers

    def test_sub_threshold_compressed_when_min_size_not_applied(self) -> None:
        from pounce._compression import create_compressor
        from pounce.sync_worker import _finalize_response_headers

        body_in = b"tiny"
        compressor = create_compressor("gzip")
        assert compressor is not None
        headers, body = _finalize_response_headers(
            [(b"content-type", b"text/plain")],
            body_in,
            compressor,
            None,
            self._config(compression=True, compression_min_size=500),
            apply_min_size=False,
        )
        # ASGI-style path ignores min_size (bridge already governed it).
        assert body != body_in
        assert (b"content-encoding", b"gzip") in headers

    def test_preset_content_encoding_suppresses_compression(self) -> None:
        from pounce._compression import create_compressor
        from pounce.sync_worker import _finalize_response_headers

        body_in = b"A" * 200
        compressor = create_compressor("gzip")
        assert compressor is not None
        headers, body = _finalize_response_headers(
            [(b"content-encoding", b"br"), (b"content-length", b"200")],
            body_in,
            compressor,
            None,
            self._config(compression=True, compression_min_size=10),
            apply_min_size=True,
        )
        assert body == body_in  # not re-compressed
        encodings = [v for n, v in headers if n.lower() == b"content-encoding"]
        assert encodings == [b"br"]
        cls = [v for n, v in headers if n.lower() == b"content-length"]
        assert cls == [b"200"]


class TestFinalizeResponseHeadersDictionary:
    """DCZ used-dictionary emission via _finalize_response_headers."""

    def test_used_dictionary_header_emitted_on_dcz(self) -> None:
        import json

        from pounce._compression import _HAS_ZSTD, CompressionDictionary, create_compressor

        if not _HAS_ZSTD:
            import pytest

            pytest.skip("zstd not available")

        from compression import zstd

        from pounce.sync_worker import _finalize_response_headers

        samples = [json.dumps({"id": i, "name": f"item_{i}"}).encode() for i in range(200)]
        trained = zstd.train_dict(samples, dict_size=8192)
        cd = CompressionDictionary(trained.dict_content, "/api/*")
        compressor = create_compressor("dcz", dictionary=cd)
        assert compressor is not None

        headers, _body = _finalize_response_headers(
            [(b"content-type", b"application/json")],
            b"B" * 200,
            compressor,
            cd,
            _make_config(compression=True, compression_min_size=10),
            apply_min_size=True,
        )
        assert (b"content-encoding", b"dcz") in headers
        assert (b"used-dictionary", cd.sf_hash.encode("ascii")) in headers
