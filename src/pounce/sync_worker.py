"""
SyncWorker — blocking I/O worker for request-response workloads.

One request at a time per thread, no asyncio. On 3.14t, runs in a thread
with true parallelism. Handles HTTP/1.1 keep-alive in a tight recv/send loop.

When the ASGI app returns a streaming response (more_body=True) or WebSocket
upgrade, raises NeedsAsyncError — the supervisor hands off to the async pool
(Phase 2). For Phase 1, streaming requests receive 501 Not Implemented.

"""

import asyncio
import contextlib
import logging
import queue
import socket
import ssl
import threading
import time
from typing import Any, cast

from pounce._compression import Compressor, create_compressor, negotiate_encoding
from pounce._cpu_affinity import maybe_pin_worker
from pounce._fast_h1 import ParseError
from pounce._fast_h1 import parse_request as _fast_parse
from pounce._health import build_health_response
from pounce._request_pipeline import log_request, prepare_request
from pounce._response_frame import (
    _STATUS_REASONS,
    get_date_header_bytes,
    serialize_raw_response_parts,
)
from pounce._timing import elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.sync_bridge import NeedsAsyncError, call_asgi_sync
from pounce.async_pool import AsyncPool, StreamingHandoff, WebSocketHandoff
from pounce.config import ServerConfig
from pounce.lifecycle import (
    ClientDisconnected,
    ConnectionCompleted,
    ConnectionOpened,
    LifecycleCollector,
    NoopCollector,
    RequestStarted,
    ResponseCompleted,
    next_connection_id,
)
from pounce.lifecycle import monotonic_ns as lifecycle_ns
from pounce.protocols._base import RequestReceived
from pounce.sync_protocol import RawRequest, SyncApp


class _RequestMeta:
    """Pre-extracted header values from a single pass over request headers.

    All fields populated by ``_classify_request()`` — avoids redundant
    linear scans per request.  Header names from ``_fast_h1`` are already
    lowered, so no ``.lower()`` is needed on names.
    """

    __slots__ = ("accept_encoding", "is_websocket", "wants_close")

    def __init__(self) -> None:
        self.wants_close: bool = False
        self.is_websocket: bool = False
        self.accept_encoding: bytes | None = None


def _classify_request(request: RequestReceived) -> _RequestMeta:
    """Single-pass header extraction for the sync-worker hot path.

    Replaces separate calls to ``_wants_close``, ``_is_websocket_upgrade``,
    and ``get_header(accept-encoding)``.
    Header names are already lowered by ``_fast_h1.parse_request``.
    """
    meta = _RequestMeta()
    has_upgrade_conn = False
    has_ws_upgrade = False
    has_connection_header = False

    for name, value in request.headers:
        # Names pre-lowered by _fast_h1.parse_request — compare directly
        if name == b"connection":
            has_connection_header = True
            val_lower = value.lower()
            if b"close" in val_lower:
                meta.wants_close = True
            if b"upgrade" in val_lower:
                has_upgrade_conn = True
        elif name == b"upgrade" and value.lower() == b"websocket":
            has_ws_upgrade = True
        elif name == b"accept-encoding":
            meta.accept_encoding = value

    meta.is_websocket = has_upgrade_conn and has_ws_upgrade

    # HTTP/1.0 defaults to close when no Connection header is present
    if not has_connection_header and request.http_version == "1.0":
        meta.wants_close = True

    return meta


class SyncWorker:
    """Blocking I/O worker — one request at a time, no event loop.

    On 3.14t, runs in a thread with true parallelism. Handles HTTP/1.1
    keep-alive in a tight recv/send loop. Streaming and WebSocket require
    handoff to the async pool (Phase 2).

    """

    __slots__ = (
        "_active_connections",
        "_app",
        "_async_pool",
        "_config",
        "_conn_lock",
        "_conn_queue",
        "_date_cache_sec",
        "_date_header_bytes",
        "_ext_shutdown",
        "_lifecycle",
        "_lifespan_state",
        "_logger",
        "_recv_buf",
        "_recv_buf_len",
        "_sock",
        "_ssl_context",
        "_sync_app",
        "_worker_id",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        sock: socket.socket | None,
        *,
        worker_id: int = 0,
        shutdown_event: threading.Event | None = None,
        ssl_context: ssl.SSLContext | None = None,
        lifecycle_collector: LifecycleCollector | None = None,
        async_pool: AsyncPool | None = None,
        conn_queue: queue.Queue[tuple[socket.socket, object]] | None = None,
        sync_app: SyncApp | None = None,
    ) -> None:
        self._config = config
        self._async_pool = async_pool
        self._conn_queue = conn_queue
        self._sync_app = sync_app
        self._app = app
        self._sock = sock
        self._worker_id = worker_id
        self._ext_shutdown = shutdown_event
        self._ssl_context = ssl_context
        self._lifecycle: LifecycleCollector = lifecycle_collector or NoopCollector()
        self._lifespan_state: dict[str, Any] = {}
        self._logger = logging.getLogger(f"pounce.sync_worker.{worker_id}")
        self._active_connections = 0
        self._conn_lock = threading.Lock()
        self._date_cache_sec = -1
        self._date_header_bytes = b""
        self._recv_buf = bytearray(65536)
        self._recv_buf_len = 0

    def set_lifespan_state(self, state: dict[str, Any]) -> None:
        """Set the lifespan state dict shared with all requests."""
        self._lifespan_state = state

    def start_draining(self) -> None:
        """Mark this worker as draining (stop accepting new connections)."""
        if self._ext_shutdown:
            self._ext_shutdown.set()

    def is_idle(self) -> bool:
        """True if no connection is currently being handled."""
        with self._conn_lock:
            return self._active_connections == 0

    def run(self) -> None:
        """Accept connections until shutdown (blocking)."""
        maybe_pin_worker(self._worker_id, self._config)
        poll_interval = 0.25

        runner = asyncio.Runner()
        try:
            if self._conn_queue is not None:
                self._run_from_queue(poll_interval, runner)
            else:
                self._run_accept_loop(poll_interval, runner)
        finally:
            runner.close()

    def _run_from_queue(self, poll_interval: float, runner: asyncio.Runner) -> None:
        """Get connections from distributor queue (no thundering herd)."""
        assert self._conn_queue is not None
        while not (self._ext_shutdown and self._ext_shutdown.is_set()):
            try:
                conn, addr = self._conn_queue.get(timeout=poll_interval)
            except queue.Empty:
                continue
            self._handle_connection(conn, cast("tuple[str, int]", addr), runner)

    def _run_accept_loop(self, poll_interval: float, runner: asyncio.Runner) -> None:
        """Accept directly from socket (SO_REUSEPORT or single worker)."""
        assert self._sock is not None
        self._sock.setblocking(True)
        while not (self._ext_shutdown and self._ext_shutdown.is_set()):
            self._sock.settimeout(poll_interval)
            try:
                conn, addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._ext_shutdown and self._ext_shutdown.is_set():
                    break
                raise
            conn.setblocking(True)
            if conn.family in (socket.AF_INET, socket.AF_INET6):
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self._ssl_context:
                try:
                    conn = self._ssl_context.wrap_socket(conn, server_side=True)
                except ssl.SSLError:
                    conn.close()
                    continue
            self._handle_connection(conn, addr, runner)

    def _handle_connection(
        self, conn: socket.socket, addr: tuple[str, int], runner: asyncio.Runner
    ) -> None:
        """Handle a single TCP connection through request-response cycles."""
        with self._conn_lock:
            self._active_connections += 1
        handed_off = False
        try:
            handed_off = self._handle_connection_impl(conn, addr, runner)
        finally:
            with self._conn_lock:
                self._active_connections -= 1
            if not handed_off:
                with contextlib.suppress(OSError, ConnectionError):
                    conn.close()

    def _handle_connection_impl(
        self, conn: socket.socket, addr: tuple[str, int], runner: asyncio.Runner
    ) -> bool:
        """Inner connection handling. Returns True if handed off to async pool."""
        self._recv_buf_len = 0  # Reset buffer state for new connection
        conn.settimeout(self._config.header_timeout)
        try:
            peername = conn.getpeername()
            client = (str(peername[0]), int(peername[1])) if len(peername) >= 2 else ("unix", 0)
        except OSError:
            client = ("unknown", 0)
        client_str = f"{client[0]}:{client[1]}"
        try:
            sockname = conn.getsockname()
            server = (
                (str(sockname[0]), int(sockname[1]))
                if len(sockname) >= 2
                else (self._config.host, self._config.port)
            )
        except OSError:
            server = (self._config.host, self._config.port)
        if self._config.uds:
            server = (self._config.uds, 0)
        conn_id = next_connection_id()
        conn_start = lifecycle_ns()

        self._lifecycle.record(
            ConnectionOpened(
                connection_id=conn_id,
                worker_id=self._worker_id,
                client_addr=client[0],
                client_port=client[1],
                server_addr=server[0],
                server_port=server[1],
                protocol="h1",
                timestamp_ns=conn_start,
            )
        )

        request_count = 0
        max_requests = self._config.max_requests_per_connection

        keep_alive_timeout = self._config.keep_alive_timeout

        try:
            while True:
                if self._ext_shutdown and self._ext_shutdown.is_set():
                    break

                # First request uses header_timeout; subsequent use keep_alive_timeout
                timeout = self._config.header_timeout if request_count == 0 else keep_alive_timeout
                conn.settimeout(timeout)
                request, body = self._recv_request_fast(conn)
                if request is None:
                    break

                meta = _classify_request(request)
                close_after = meta.wants_close
                request_count += 1
                request_start = monotonic_ns()
                self._lifecycle.record(
                    RequestStarted(
                        connection_id=conn_id,
                        worker_id=self._worker_id,
                        method=request.method.decode("ascii", errors="replace"),
                        path=request.target.decode("ascii", errors="replace"),
                        http_version=request.http_version,
                        timestamp_ns=request_start,
                    )
                )

                if meta.is_websocket:
                    if self._async_pool:
                        self._async_pool.accept_handoff(
                            WebSocketHandoff(
                                conn=conn,
                                request=request,
                                client=client,
                                server=server,
                                scope={},
                            )
                        )
                        return True
                    self._send_error(conn, 501, "WebSocket requires worker_mode=async")
                    break

                # Connection header: close only when client asks or max_requests hit
                at_limit = max_requests > 0 and request_count >= max_requests
                conn_header = b"close" if (close_after or at_limit) else b"keep-alive"

                # Fused sync path: try SyncApp.handle_sync() before ASGI.
                # This bypasses h11 entirely (raw serialization), so we always
                # close the connection — h11 state can't be recycled.
                if self._sync_app is not None:
                    target = request.target
                    parts = target.split(b"?", 1)
                    path_bytes = parts[0]
                    query_bytes = parts[1] if len(parts) > 1 else b""
                    raw_req = RawRequest(
                        method=request.method,
                        path=path_bytes,
                        query_string=query_bytes,
                        headers=request.headers,
                        body=body,
                        client=client,
                        server=server,
                        http_version=request.http_version,
                    )
                    raw_resp = self._sync_app.handle_sync(raw_req)
                    if raw_resp is not None:
                        # Fast path: direct response, no asyncio, bypass h11
                        compressor: Compressor | None = None
                        if self._config.compression and meta.accept_encoding:
                            enc = negotiate_encoding(meta.accept_encoding)
                            if enc:
                                compressor = create_compressor(enc)

                        # Single pass: strip CL only when compressing, track presence
                        has_cl = False
                        headers_list: list[tuple[bytes, bytes]] = []
                        for n, v in raw_resp.headers:
                            if n.lower() == b"content-length":
                                has_cl = True
                                if not compressor:
                                    headers_list.append((n, v))
                            else:
                                headers_list.append((n, v))

                        if compressor and len(raw_resp.body) >= self._config.compression_min_size:
                            body_out = compressor.compress(raw_resp.body) + compressor.flush()
                            headers_list.append(
                                (b"content-encoding", compressor.encoding.encode("ascii"))
                            )
                            headers_list.append(
                                (b"content-length", str(len(body_out)).encode("ascii"))
                            )
                        else:
                            body_out = raw_resp.body
                            if not has_cl:
                                headers_list.append(
                                    (b"content-length", str(len(body_out)).encode("ascii"))
                                )
                        headers_list.append((b"connection", b"close"))
                        date_hdr = self._cached_date_header()
                        head, body_bytes = serialize_raw_response_parts(
                            raw_resp.status,
                            tuple(headers_list),
                            body_out,
                            server_header=self._config.server_header,
                            date_header=date_hdr,
                        )
                        try:
                            if hasattr(conn, "sendmsg"):
                                conn.sendmsg([head, body_bytes])
                            else:
                                conn.sendall(head + body_bytes)
                        except OSError:
                            conn.sendall(head + body_bytes)
                        duration = elapsed_ms(request_start)
                        self._lifecycle.record(
                            ResponseCompleted(
                                connection_id=conn_id,
                                worker_id=self._worker_id,
                                status=raw_resp.status,
                                bytes_sent=len(body_out),
                                duration_ms=duration,
                                timestamp_ns=lifecycle_ns(),
                            )
                        )
                        log_request(
                            self._config,
                            request.method.decode("ascii", errors="replace"),
                            path_bytes.decode("ascii", errors="replace"),
                            raw_resp.status,
                            len(body_out),
                            duration,
                            client_str,
                            http_version=request.http_version,
                            worker_id=self._worker_id,
                        )
                        break

                scope, request_id = prepare_request(
                    request, self._config, client, server, self._lifespan_state
                )
                scope.setdefault("extensions", {})["pounce.inline_sync"] = True

                if (
                    self._config.health_check_path
                    and scope["path"] == self._config.health_check_path
                    and request.method == b"GET"
                ):
                    status, health_headers, body_bytes = build_health_response(
                        worker_id=self._worker_id,
                        active_connections=1,
                    )
                    health_headers = [*list(health_headers), (b"connection", conn_header)]
                    date_hdr = self._cached_date_header()
                    head, body_out_bytes = serialize_raw_response_parts(
                        status,
                        tuple(health_headers),
                        body_bytes,
                        server_header=self._config.server_header,
                        date_header=date_hdr,
                    )
                    conn.sendall(head + body_out_bytes)
                    health_duration = elapsed_ms(request_start)
                    self._lifecycle.record(
                        ResponseCompleted(
                            connection_id=conn_id,
                            worker_id=self._worker_id,
                            status=status,
                            bytes_sent=len(body_bytes),
                            duration_ms=health_duration,
                            timestamp_ns=lifecycle_ns(),
                        )
                    )
                    log_request(
                        self._config,
                        "GET",
                        scope["path"],
                        status,
                        len(body_bytes),
                        health_duration,
                        client_str,
                        http_version=request.http_version,
                        request_id=request_id,
                        worker_id=self._worker_id,
                    )
                    if close_after or at_limit:
                        break
                    continue

                try:
                    response = call_asgi_sync(
                        cast("ASGIApp", self._app),
                        scope,
                        body,
                        runner=runner,
                    )
                except NeedsAsyncError:
                    if self._async_pool:
                        self._async_pool.accept_handoff(
                            StreamingHandoff(
                                conn=conn,
                                scope=scope,
                                body=body,
                                request_id=request_id,
                            )
                        )
                        return True
                    self._send_error(
                        conn, 501, "Streaming responses require worker_mode=async or handoff"
                    )
                    break
                except Exception:
                    self._logger.exception("ASGI app error")
                    self._send_error(conn, 500, "Internal Server Error")
                    break
                if response.needs_async:
                    if self._async_pool:
                        self._async_pool.accept_handoff(
                            StreamingHandoff(
                                conn=conn,
                                scope=scope,
                                body=body,
                                request_id=request_id,
                            )
                        )
                        return True
                    self._send_error(
                        conn, 501, "Streaming responses require worker_mode=async or handoff"
                    )
                    break

                # Negotiate compression using pre-extracted accept-encoding
                asgi_compressor: Compressor | None = None
                if self._config.compression and meta.accept_encoding:
                    enc = negotiate_encoding(meta.accept_encoding)
                    if enc:
                        asgi_compressor = create_compressor(enc)

                # Single pass: strip CL only when compressing, track presence
                has_cl = False
                headers: list[tuple[bytes, bytes]] = []
                for n, v in response.headers:
                    if n.lower() == b"content-length":
                        has_cl = True
                        if not asgi_compressor:
                            headers.append((n, v))
                    else:
                        headers.append((n, v))

                if asgi_compressor:
                    body_out = asgi_compressor.compress(response.body) + asgi_compressor.flush()
                    headers.append((b"content-encoding", asgi_compressor.encoding.encode("ascii")))
                    headers.append((b"content-length", str(len(body_out)).encode("ascii")))
                else:
                    body_out = response.body
                    if not has_cl:
                        headers.append((b"content-length", str(len(body_out)).encode("ascii")))
                if request_id:
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                headers.append((b"connection", conn_header))

                date_hdr = self._cached_date_header()

                # Bypass h11 for response serialization — raw bytes are faster
                head, body_bytes_out = serialize_raw_response_parts(
                    response.status,
                    tuple(headers),
                    body_out,
                    server_header=self._config.server_header,
                    date_header=date_hdr,
                )
                try:
                    if hasattr(conn, "sendmsg"):
                        conn.sendmsg([head, body_bytes_out])
                    else:
                        conn.sendall(head + body_bytes_out)
                except OSError:
                    conn.sendall(head + body_bytes_out)

                asgi_duration = elapsed_ms(request_start)
                self._lifecycle.record(
                    ResponseCompleted(
                        connection_id=conn_id,
                        worker_id=self._worker_id,
                        status=response.status,
                        bytes_sent=len(body_out),
                        duration_ms=asgi_duration,
                        timestamp_ns=lifecycle_ns(),
                    )
                )
                log_request(
                    self._config,
                    scope["method"],
                    scope["path"],
                    response.status,
                    len(body_out),
                    asgi_duration,
                    client_str,
                    http_version=request.http_version,
                    request_id=request_id,
                    worker_id=self._worker_id,
                )

                if close_after or at_limit:
                    break
        except ConnectionError, OSError:
            self._lifecycle.record(
                ClientDisconnected(
                    connection_id=conn_id,
                    worker_id=self._worker_id,
                    during_streaming=False,
                    timestamp_ns=lifecycle_ns(),
                )
            )
        finally:
            self._lifecycle.record(
                ConnectionCompleted(
                    connection_id=conn_id,
                    worker_id=self._worker_id,
                    requests_served=request_count,
                    total_bytes_sent=0,
                    duration_ms=round((lifecycle_ns() - conn_start) / 1_000_000, 1),
                    reason="complete",
                    timestamp_ns=lifecycle_ns(),
                )
            )
        return False

    def _recv_request_fast(self, conn: socket.socket) -> tuple[RequestReceived | None, bytes]:
        """Read a complete HTTP request using the fast parser.

        Accumulates data in the recv buffer until a complete request
        (headers + body based on Content-Length) is available.
        Returns (request, body) or (None, b"") on connection close/error.
        """
        buf = self._recv_buf
        mv = memoryview(buf)
        total = self._recv_buf_len

        while True:
            try:
                n = conn.recv_into(mv[total:])
            except ConnectionError, OSError, TimeoutError:
                self._recv_buf_len = 0
                return (None, b"")

            if n <= 0:
                self._recv_buf_len = 0
                return (None, b"")

            total += n
            try:
                request, body, consumed, chunked = _fast_parse(mv, total)
            except ParseError:
                self._recv_buf_len = 0
                self._send_error(conn, 400, "Bad Request")
                return (None, b"")

            if request is not None:
                if chunked:
                    self._recv_buf_len = 0
                    self._send_error(conn, 501, "Chunked Transfer-Encoding not supported")
                    return (None, b"")
                # Persist any unconsumed bytes for the next call (pipelining)
                leftover = total - consumed
                if leftover > 0:
                    buf[:leftover] = buf[consumed:total]
                self._recv_buf_len = leftover
                return (request, body)

            # Buffer full but still no complete request — reject
            if total >= len(buf):
                self._recv_buf_len = 0
                self._send_error(conn, 413, "Request Too Large")
                return (None, b"")

    def _cached_date_header(self) -> bytes | None:
        """Return cached Date header bytes, refreshing at most once per second."""
        if not self._config.date_header:
            return None
        now_sec = int(time.time())
        if now_sec != self._date_cache_sec:
            self._date_cache_sec = now_sec
            self._date_header_bytes = get_date_header_bytes()
        return self._date_header_bytes

    def _send_error(self, conn: socket.socket, status: int, message: str) -> None:
        """Send a plain-text error response (raw bytes, no h11)."""
        body = message.encode("utf-8")
        reason = _STATUS_REASONS.get(status, b"Error")
        status_line = str(status).encode("ascii") + b" " + reason
        with contextlib.suppress(OSError, ConnectionError):
            conn.sendall(
                b"HTTP/1.1 " + status_line + b"\r\n"
                b"content-type: text/plain; charset=utf-8\r\n"
                b"content-length: " + str(len(body)).encode("ascii") + b"\r\n"
                b"connection: close\r\n"
                b"\r\n" + body
            )
