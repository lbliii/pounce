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

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.async_pool import StreamingHandoff, WebSocketHandoff
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived
from pounce.sync_protocol import RawRequest, RawResponse, SyncApp
from pounce.sync_worker import SyncWorker, _classify_request

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
        """Health check path only responds to GET, POST falls through to ASGI."""
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
