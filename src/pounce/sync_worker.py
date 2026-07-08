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
from collections.abc import Sequence
from typing import Any, cast

from pounce._compression import (
    CompressionDictionary,
    Compressor,
)
from pounce._cpu_affinity import maybe_pin_worker
from pounce._dictionary_endpoint import use_as_dictionary_headers
from pounce._drain import write_drain_503_sync
from pounce._fast_h1 import ParseError
from pounce._fast_h1 import parse_request as _fast_parse
from pounce._request_pipeline import (
    BuiltinResponse,
    log_request,
    maybe_build_builtin_response,
    negotiate_compressor_from_meta,
    prepare_request,
)
from pounce._response_frame import (
    _STATUS_REASONS,
    get_date_header_bytes,
    serialize_raw_response_parts,
)
from pounce._timing import elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.bridge import is_streaming_response
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

# Interim status line written before reading a body when a client sends
# ``Expect: 100-continue``. H1-only; trailers remain unsupported on this path.
_HTTP_100_CONTINUE: bytes = b"HTTP/1.1 100 Continue\r\n\r\n"
_CRLFCRLF: bytes = b"\r\n\r\n"

# Full-shutdown late-arrival 503 window: a brief grace for connections racing in
# during teardown, capped so an idle worker exits promptly instead of spinning the
# whole ``shutdown_timeout`` (issue #100 — the sync analogue of the async worker's
# ``wait_closed()`` returning when ``active == 0``).
_DRAIN_ACCEPT_GRACE_S: float = 0.5
_DRAIN_ACCEPT_POLL_S: float = 0.1


async def _worker_lifecycle_receive() -> dict[str, Any]:
    """Return a disconnect message for worker lifecycle extension scopes."""
    return {"type": "http.disconnect"}


async def _worker_lifecycle_send(message: dict[str, Any]) -> None:
    """Ignore messages sent by worker lifecycle extension handlers."""


def _wants_100_continue(header_block: bytes) -> bool:
    """Return True if the header block declares ``Expect: 100-continue``.

    ``header_block`` is the raw bytes between the request line and the
    blank-line terminator. Each CRLF-delimited line is checked so that an
    ``expect:`` substring inside the request target or another header value
    cannot trigger a false match; full header validation still happens in
    ``_fast_h1.parse_request``.
    """
    for line in header_block.split(b"\r\n"):
        name, sep, value = line.partition(b":")
        if sep and name.strip().lower() == b"expect" and value.strip().lower() == b"100-continue":
            return True
    return False


class _RequestMeta:
    """Pre-extracted header values from a single pass over request headers.

    All fields populated by ``_classify_request()`` — avoids redundant
    linear scans per request.  Header names from ``_fast_h1`` are already
    lowered, so no ``.lower()`` is needed on names.
    """

    __slots__ = ("accept_encoding", "available_dictionary", "is_websocket", "wants_close")

    def __init__(self) -> None:
        self.wants_close: bool = False
        self.is_websocket: bool = False
        self.accept_encoding: bytes | None = None
        self.available_dictionary: bytes | None = None


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
        elif name == b"available-dictionary":
            meta.available_dictionary = value

    meta.is_websocket = has_upgrade_conn and has_ws_upgrade

    # HTTP/1.0 defaults to close when no Connection header is present
    if not has_connection_header and request.http_version == "1.0":
        meta.wants_close = True

    return meta


def _finalize_response_headers(
    response_headers: Sequence[tuple[bytes, bytes]],
    body: bytes,
    compressor: Compressor | None,
    dictionary: CompressionDictionary | None,
    config: ServerConfig,
    *,
    apply_min_size: bool,
) -> tuple[list[tuple[bytes, bytes]], bytes]:
    """Rewrite response headers and body for the sync-worker response paths.

    Local helper shared by the SyncApp fast path and the inline ASGI path.
    Performs the single-pass content-encoding / content-length rewrite,
    applies the negotiated ``compressor`` (suppressed when the app pre-set a
    ``content-encoding`` header), re-appends ``content-length``, and — when
    ``dcz`` was negotiated — emits ``used-dictionary``.

    ``apply_min_size`` gates compression on ``config.compression_min_size``
    (the SyncApp fast path); the inline ASGI path passes ``False`` because the
    bridge already governs sub-threshold bodies, so the historical sync-ASGI
    behaviour compresses regardless of body size.

    Returns (headers_list, body_out). To preserve byte-for-byte header order,
    callers append request-scoped headers (``x-request-id``), the
    dictionary-advertisement headers (RFC 9842), and the connection header
    after this returns.
    """
    should_compress = compressor is not None
    if should_compress and apply_min_size and len(body) < config.compression_min_size:
        should_compress = False

    # Single pass: strip CL only when compressing, track presence
    has_cl = False
    content_length_header: tuple[bytes, bytes] | None = None
    headers_list: list[tuple[bytes, bytes]] = []
    for n, v in response_headers:
        nl = n.lower()
        if nl == b"content-encoding":
            # App pre-set an encoding — never double-compress.
            should_compress = False
            headers_list.append((n, v))
        elif nl == b"content-length":
            has_cl = True
            content_length_header = (n, v)
            if not should_compress:
                headers_list.append((n, v))
        else:
            headers_list.append((n, v))

    if compressor is not None and should_compress:
        body_out = compressor.compress(body) + compressor.flush()
        headers_list.append((b"content-encoding", compressor.encoding.encode("ascii")))
        headers_list.append((b"content-length", str(len(body_out)).encode("ascii")))
        if dictionary is not None:
            headers_list.append((b"used-dictionary", dictionary.sf_hash.encode("ascii")))
    else:
        body_out = body
        if not has_cl:
            headers_list.append((b"content-length", str(len(body_out)).encode("ascii")))
        elif content_length_header is not None and content_length_header not in headers_list:
            headers_list.append(content_length_header)

    return headers_list, body_out


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
        "_drain_event",
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
        self._drain_event = threading.Event()
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
        self._drain_event.set()

    def is_idle(self) -> bool:
        """True if no connection is currently being handled."""
        with self._conn_lock:
            return self._active_connections == 0

    def _active_connection_count(self) -> int:
        """Return the active connection count under its free-threading lock."""
        with self._conn_lock:
            return self._active_connections

    def run(self) -> None:
        """Accept connections until shutdown (blocking)."""
        maybe_pin_worker(self._worker_id, self._config)
        poll_interval = 0.25

        runner = asyncio.Runner()
        started = False
        try:
            startup_ok = runner.run(self._run_worker_startup_hook())
            if not startup_ok and self._config.worker_startup_failure == "shutdown":
                self._logger.error(
                    "Sync worker %d refusing to serve: pounce.worker.startup hook failed and "
                    "worker_startup_failure='shutdown' — signalling server shutdown",
                    self._worker_id,
                )
                if self._ext_shutdown is not None:
                    self._ext_shutdown.set()
                return
            started = True
            if self._conn_queue is not None:
                self._run_from_queue(poll_interval, runner)
            else:
                self._run_accept_loop(poll_interval, runner)
        finally:
            if started:
                runner.run(self._run_worker_shutdown_hook())
            runner.close()

    async def _run_worker_startup_hook(self) -> bool:
        """Run ``pounce.worker.startup`` on this sync worker's runner loop."""
        fatal = self._config.worker_startup_failure == "shutdown"
        level = logging.ERROR if fatal else logging.WARNING
        try:
            await asyncio.wait_for(
                self._app(
                    {"type": "pounce.worker.startup", "worker_id": self._worker_id},
                    _worker_lifecycle_receive,
                    _worker_lifecycle_send,
                ),
                timeout=self._config.startup_timeout,
            )
        except TimeoutError:
            self._logger.log(
                level,
                "Sync worker %d startup hook timed out after %.1fs",
                self._worker_id,
                self._config.startup_timeout,
            )
            return False
        except Exception:
            self._logger.log(
                level,
                "Sync worker startup hook raised — if this is unexpected, check your app",
                exc_info=True,
            )
            return False
        return True

    async def _run_worker_shutdown_hook(self) -> None:
        """Run ``pounce.worker.shutdown`` on this sync worker's runner loop."""
        try:
            await asyncio.wait_for(
                self._app(
                    {"type": "pounce.worker.shutdown", "worker_id": self._worker_id},
                    _worker_lifecycle_receive,
                    _worker_lifecycle_send,
                ),
                timeout=10.0,
            )
        except Exception:
            self._logger.warning(
                "Sync worker shutdown hook raised — if this is unexpected, check your app",
                exc_info=True,
            )

    def _run_from_queue(self, poll_interval: float, runner: asyncio.Runner) -> None:
        """Get connections from distributor queue (no thundering herd)."""
        assert self._conn_queue is not None
        while not self._should_stop():
            try:
                conn, addr = self._conn_queue.get(timeout=poll_interval)
            except queue.Empty:
                continue
            self._handle_connection(conn, cast("tuple[str, int]", addr), runner)
        # Drain (#104): on a FULL shutdown, any connection still sitting in the
        # shared queue was accepted by the distributor BEFORE drain began — the
        # client has already sent its request and is in-flight, so it must be
        # SERVED to completion, not reset. (The distributor 503s connections it
        # accepts AFTER drain; nothing new lands in this queue once draining.)
        # Bounded by shutdown_timeout so a slow handler cannot pin the worker.
        # On a graceful RELOAD this is a no-op — the new generation keeps serving
        # the shared queue (issue #102); see _drain_pending_queue.
        self._drain_pending_queue(runner)

    def _run_accept_loop(self, poll_interval: float, runner: asyncio.Runner) -> None:
        """Accept directly from socket (SO_REUSEPORT or single worker)."""
        assert self._sock is not None
        self._sock.setblocking(True)
        while not self._should_stop():
            self._sock.settimeout(poll_interval)
            try:
                conn, addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._should_stop():
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
        # Drain (issue #101): on a FULL shutdown, answer any client that races in
        # during the bounded grace window with an actionable 503 instead of a
        # silent drop. Bounded by shutdown_timeout so the worker thread still
        # exits. On a graceful RELOAD this is a no-op — the new generation owns
        # the listener (issue #102); see _drain_accept_window.
        self._drain_accept_window()

    def _drain_pending_queue(self, runner: asyncio.Runner) -> None:
        """Serve in-flight queued connections on a FULL shutdown; never reset them.

        Only acts on a *full shutdown* (``_ext_shutdown`` set). On a *graceful
        reload* (``_drain_event`` set, ``_ext_shutdown`` unset) the shared
        ``conn_queue`` is still owned by the AcceptDistributor, which keeps
        enqueuing for the NEW generation — its own ``drain_event`` is only set
        on full shutdown, not reload. Touching those entries here would steal
        connections the new generation is meant to serve, so leave them
        untouched and let this retiring worker simply exit (issue #102).

        Every connection left in the queue was accepted by the distributor
        *before* drain began (the distributor 503s anything it accepts after),
        so each holds an in-flight request the client is waiting on. Closing
        such a socket — even after a 503 — RSTs it because the client's unread
        request bytes are still in the receive buffer, surfacing as a
        ``ConnectionResetError`` for an in-flight request (#104). So SERVE each
        queued connection to completion instead. ``_handle_connection`` already
        forces ``Connection: close`` while draining, so each finishes one
        request and closes cleanly. The whole drain-serve loop is bounded by
        ``shutdown_timeout``; any connection still queued past the deadline gets
        a bounded 503 so the worker thread can still exit promptly.
        """
        if self._conn_queue is None:
            return
        if self._ext_shutdown is None or not self._ext_shutdown.is_set():
            # Graceful reload: leave queued connections for the new generation.
            return
        deadline = time.monotonic() + self._config.shutdown_timeout
        while True:
            try:
                conn, addr = self._conn_queue.get_nowait()
            except queue.Empty:
                break
            if time.monotonic() < deadline:
                # In-flight request accepted pre-drain — serve it to completion.
                self._handle_connection(conn, cast("tuple[str, int]", addr), runner)
            else:
                # Past the bounded window: refuse cleanly so we still exit.
                self._send_drain_503(conn)

    def _drain_accept_window(self) -> None:
        """For a bounded window, accept new connections and answer them a 503.

        Only runs once the accept loop is stopping. The window is bounded by
        ``shutdown_timeout`` so a draining worker still answers late arrivals
        with an actionable refusal without blocking process exit.

        This window ONLY runs on a *full shutdown* (SIGTERM — ``_ext_shutdown``
        set), where the server is going away and a brand-new connection should
        get a bounded clean 503 (issue #101). On a *graceful reload* (SIGHUP —
        ``_drain_event`` set but ``_ext_shutdown`` NOT set) the new generation
        owns this listener and serves new connections, so the retiring worker
        must return early: it must neither steal connections from the new gen
        nor spin for the full ``shutdown_timeout`` refusing them (issue #102).
        """
        if self._sock is None:
            return
        if self._ext_shutdown is None or not self._ext_shutdown.is_set():
            # Graceful reload (or no external shutdown): the new generation
            # serves new connections — do not accept here, just exit promptly.
            return
        # Cap the window at a short grace, not the full shutdown_timeout: an idle
        # worker must exit promptly so a clean SIGTERM is not artificially delayed
        # (issue #100). New connections racing in still get a bounded clean 503.
        deadline = time.monotonic() + min(self._config.shutdown_timeout, _DRAIN_ACCEPT_GRACE_S)
        while time.monotonic() < deadline:
            try:
                # settimeout must be inside the guard: in thread mode the
                # listener socket is shared, so a concurrent close during full
                # shutdown makes settimeout itself raise EBADF. Treat any socket
                # error (closed/torn-down listener) as "stop draining" rather
                # than letting the worker crash.
                self._sock.settimeout(_DRAIN_ACCEPT_POLL_S)
                conn, _addr = self._sock.accept()
            except TimeoutError:
                # No new connection this slice. Once this worker is idle there is
                # nothing left to drain — exit now rather than spin the window.
                if self.is_idle():
                    break
                continue
            except OSError:
                break
            conn.setblocking(True)
            self._send_drain_503(conn)

    def _send_drain_503(self, conn: socket.socket) -> None:
        """Write the shared drain 503 to *conn* and close it."""
        write_drain_503_sync(conn)
        with contextlib.suppress(OSError):
            conn.close()

    def _should_stop(self) -> bool:
        return self._drain_event.is_set() or bool(
            self._ext_shutdown and self._ext_shutdown.is_set()
        )

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
        client, server, client_str, conn_id, conn_start = self._open_connection(conn)

        request_count = 0
        max_requests = self._config.max_requests_per_connection

        keep_alive_timeout = self._config.keep_alive_timeout
        close_reason = "complete"

        try:
            while True:
                # Stop only BETWEEN requests on a shutting-down worker (#104):
                # a connection just dequeued for draining has its first request
                # already in flight and MUST be served — breaking before
                # request_count > 0 would close the socket with the client's
                # unread request bytes still buffered, RSTing an in-flight
                # request. After serving one request we exit; the drain paths
                # also force ``Connection: close`` so we never loop here again.
                if request_count > 0 and self._ext_shutdown and self._ext_shutdown.is_set():
                    break

                # First request uses header_timeout; subsequent use keep_alive_timeout
                timeout = self._config.header_timeout if request_count == 0 else keep_alive_timeout
                # Drain (issue #100): cap the keep-alive idle wait so an idle
                # keep-alive client cannot pin this worker for the full
                # keep_alive_timeout while the supervisor waits on is_idle().
                # The in-flight request (if any) still completes; we just stop
                # blocking long for the *next* one on a draining worker.
                if request_count > 0 and self._drain_event.is_set():
                    timeout = min(timeout, 0.1)
                conn.settimeout(timeout)
                request, body = self._recv_request_fast(conn)
                if request is None:
                    break

                meta = _classify_request(request)
                close_after = meta.wants_close
                # Drain (issue #100): once the supervisor signals drain via
                # _drain_event, finish this in-flight request but force the
                # connection closed so the keep-alive loop exits and is_idle()
                # can return True well before reload_timeout. All response
                # branches below derive conn_header from close_after, so this
                # single flag makes health/dictionary/ASGI/fused paths all
                # emit ``Connection: close`` and break.
                if self._drain_event.is_set():
                    close_after = True
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
                    self._send_error(
                        conn,
                        501,
                        "WebSocket requires worker_mode=async",
                        code="POUNCE_WORKER_WEBSOCKET_NEEDS_ASYNC",
                        hint="Set worker_mode='async' or enable the async handoff pool.",
                    )
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
                        # Fast path: direct response, no asyncio, bypass h11.
                        target_str = request.target.decode("ascii", errors="replace")
                        compressor, dictionary = negotiate_compressor_from_meta(
                            self._config,
                            meta.accept_encoding,
                            meta.available_dictionary,
                            request_target=target_str,
                        )
                        headers_list, body_out = _finalize_response_headers(
                            raw_resp.headers,
                            raw_resp.body,
                            compressor,
                            dictionary,
                            self._config,
                            apply_min_size=True,
                        )
                        # Advertise dictionaries for matching paths (RFC 9842)
                        if self._config.compression_dictionaries:
                            headers_list.extend(
                                use_as_dictionary_headers(
                                    self._config.compression_dictionaries,
                                    target_str,
                                )
                            )
                        headers_list.append((b"connection", b"close"))
                        self._serialize_send_and_log(
                            conn,
                            raw_resp.status,
                            headers_list,
                            body_out,
                            conn_id=conn_id,
                            request_start=request_start,
                            http_version=request.http_version,
                            record_method=request.method.decode("ascii", errors="replace"),
                            log_method=request.method.decode("ascii", errors="replace"),
                            log_path=path_bytes.decode("ascii", errors="replace"),
                            client_str=client_str,
                        )
                        break

                scope, request_id = prepare_request(
                    request, self._config, client, server, self._lifespan_state
                )
                scope.setdefault("extensions", {})["pounce.inline_sync"] = True

                builtin = maybe_build_builtin_response(
                    self._config,
                    request.method,
                    scope["path"],
                    worker_id=self._worker_id,
                    active_connections=self._active_connection_count,
                    draining=self._drain_event.is_set,
                )
                if builtin is not None:
                    self._serve_builtin(
                        conn,
                        scope["path"],
                        conn_header,
                        builtin,
                        conn_id=conn_id,
                        request_start=request_start,
                        http_version=request.http_version,
                        client_str=client_str,
                        request_id=request_id,
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
                    if self._streaming_handoff(conn, scope, body, request_id):
                        return True
                    break
                except Exception:
                    self._logger.exception("ASGI app error")
                    self._send_error(
                        conn,
                        500,
                        "Internal Server Error",
                        code="POUNCE_APP_E",
                        hint="Check worker logs for the application traceback.",
                    )
                    break
                if response.needs_async:
                    if self._streaming_handoff(conn, scope, body, request_id):
                        return True
                    break

                # Negotiate compression using pre-extracted accept-encoding.
                # apply_min_size=False: the sync-ASGI bridge already governs
                # sub-threshold bodies, so this path historically compresses
                # regardless of compression_min_size (parity preserved).
                target_str = request.target.decode("ascii", errors="replace")
                asgi_compressor, asgi_dictionary = negotiate_compressor_from_meta(
                    self._config,
                    meta.accept_encoding,
                    meta.available_dictionary,
                    request_target=target_str,
                )
                headers, body_out = _finalize_response_headers(
                    response.headers,
                    response.body,
                    asgi_compressor,
                    asgi_dictionary,
                    self._config,
                    apply_min_size=False,
                )
                if request_id:
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                # Advertise dictionaries for matching paths (RFC 9842)
                if self._config.compression_dictionaries:
                    headers.extend(
                        use_as_dictionary_headers(
                            self._config.compression_dictionaries,
                            target_str,
                        )
                    )
                headers.append((b"connection", conn_header))

                # Bypass h11 for response serialization — raw bytes are faster
                self._serialize_send_and_log(
                    conn,
                    response.status,
                    headers,
                    body_out,
                    conn_id=conn_id,
                    request_start=request_start,
                    http_version=request.http_version,
                    record_method=scope.get("method", "unknown"),
                    log_method=scope["method"],
                    log_path=scope["path"],
                    client_str=client_str,
                    request_id=request_id,
                )

                if close_after or at_limit:
                    break
        except (ConnectionError, OSError):  # fmt: skip
            close_reason = "client_disconnect"
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
                    reason=close_reason,
                    timestamp_ns=lifecycle_ns(),
                )
            )
        return False

    def _streaming_handoff(
        self,
        conn: socket.socket,
        scope: dict[str, Any],
        body: bytes,
        request_id: str | None,
    ) -> bool:
        """Hand a streaming response off to the async pool, or send 501.

        Returns True when the connection was handed off (caller should return
        True), False when no pool is configured and a 501 was sent (caller
        should break the keep-alive loop).
        """
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
            conn,
            501,
            "Streaming responses require worker_mode=async or handoff",
            code="POUNCE_WORKER_STREAMING_NEEDS_ASYNC",
            hint="Set worker_mode='async' or enable the async handoff pool.",
        )
        return False

    def _open_connection(
        self, conn: socket.socket
    ) -> tuple[tuple[str, int], tuple[str, int], str, int, int]:
        """Resolve peer/local addresses and record ConnectionOpened.

        Returns (client, server, client_str, conn_id, conn_start).
        """
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
        return client, server, client_str, conn_id, conn_start

    def _serve_builtin(
        self,
        conn: socket.socket,
        path: str,
        conn_header: bytes,
        response: BuiltinResponse,
        *,
        conn_id: int,
        request_start: int,
        http_version: str,
        client_str: str,
        request_id: str | None,
    ) -> None:
        """Serialize and send a protocol-neutral built-in response."""
        headers = [*response.headers, (b"connection", conn_header)]
        date_hdr = self._cached_date_header()
        head, body_out_bytes = serialize_raw_response_parts(
            response.status,
            tuple(headers),
            response.body,
            server_header=self._config.server_header,
            date_header=date_hdr,
        )
        conn.sendall(head + body_out_bytes)
        if response.kind != "health":
            return

        health_duration = elapsed_ms(request_start)
        self._lifecycle.record(
            ResponseCompleted(
                connection_id=conn_id,
                worker_id=self._worker_id,
                status=response.status,
                bytes_sent=len(response.body),
                duration_ms=health_duration,
                timestamp_ns=lifecycle_ns(),
                method="GET",
            )
        )
        log_request(
            self._config,
            "GET",
            path,
            response.status,
            len(response.body),
            health_duration,
            client_str,
            http_version=http_version,
            request_id=request_id,
            worker_id=self._worker_id,
        )

    def _serialize_send_and_log(
        self,
        conn: socket.socket,
        status: int,
        headers: list[tuple[bytes, bytes]],
        body_out: bytes,
        *,
        conn_id: int,
        request_start: int,
        http_version: str,
        record_method: str,
        log_method: str,
        log_path: str,
        client_str: str,
        request_id: str | None = None,
    ) -> None:
        """Serialize, send, and record/log a single buffered response.

        Local helper for the SyncApp fast path and the inline ASGI path — both
        bypass h11 and share the identical serialize -> sendmsg(/sendall
        fallback) -> ResponseCompleted -> access-log sequence. Health and
        dictionary responses keep their own plain ``sendall`` path.
        """
        date_hdr = self._cached_date_header()
        head, body_bytes = serialize_raw_response_parts(
            status,
            tuple(headers),
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
                status=status,
                bytes_sent=len(body_out),
                duration_ms=duration,
                timestamp_ns=lifecycle_ns(),
                method=record_method,
                streaming=is_streaming_response(headers),
            )
        )
        log_request(
            self._config,
            log_method,
            log_path,
            status,
            len(body_out),
            duration,
            client_str,
            http_version=http_version,
            request_id=request_id,
            worker_id=self._worker_id,
        )

    def _recv_request_fast(self, conn: socket.socket) -> tuple[RequestReceived | None, bytes]:
        """Read a complete HTTP request using the fast parser.

        Accumulates data in the recv buffer until a complete request
        (headers + body based on Content-Length) is available.
        Returns (request, body) or (None, b"") on connection close/error.
        """
        buf = self._recv_buf
        mv = memoryview(buf)
        total = self._recv_buf_len
        sent_100 = False

        while True:
            try:
                n = conn.recv_into(mv[total:])
            except OSError:
                # ConnectionError and TimeoutError are both OSError subclasses,
                # so a single base-class handler covers a dropped/timed-out
                # client without the parenless multi-type form (PEP 758) that
                # the src code-quality guard forbids.
                self._recv_buf_len = 0
                return (None, b"")

            if n <= 0:
                self._recv_buf_len = 0
                return (None, b"")

            total += n
            try:
                request, body, consumed, chunked = _fast_parse(
                    mv,
                    total,
                    max_headers=self._config.max_headers,
                    max_header_size=self._config.max_header_size,
                )
            except ParseError as exc:
                self._logger.debug("Malformed request from client: %s", exc)
                self._recv_buf_len = 0
                self._send_error(conn, 400, "Bad Request", code=exc.code, hint=exc.hint)
                return (None, b"")

            if request is not None:
                if chunked:
                    self._recv_buf_len = 0
                    self._send_error(
                        conn,
                        501,
                        "Chunked Transfer-Encoding not supported",
                        code="POUNCE_PARSE_CHUNKED_UNSUPPORTED",
                        hint="The sync worker does not decode chunked bodies; use worker_mode='async'.",
                    )
                    return (None, b"")
                # Persist any unconsumed bytes for the next call (pipelining)
                leftover = total - consumed
                if leftover > 0:
                    buf[:leftover] = buf[consumed:total]
                self._recv_buf_len = leftover
                return (request, body)

            # Headers are complete but the body has not fully arrived. If the
            # client sent ``Expect: 100-continue`` it is deliberately
            # withholding the body until it sees an interim 100 line; emit it
            # once so the client unblocks instead of stalling until timeout.
            if not sent_100:
                header_end = bytes(mv[:total]).find(_CRLFCRLF)
                if header_end != -1 and _wants_100_continue(bytes(mv[:header_end])):
                    try:
                        # ConnectionError is a subclass of OSError, so a single
                        # base-class handler also covers a dropped client.
                        conn.sendall(_HTTP_100_CONTINUE)
                    except OSError:
                        self._recv_buf_len = 0
                        return (None, b"")
                    sent_100 = True

            # Buffer full but still no complete request — reject
            if total >= len(buf):
                self._recv_buf_len = 0
                self._send_error(
                    conn,
                    413,
                    "Request Too Large",
                    code="POUNCE_LIMIT_REQUEST_TOO_LARGE",
                    hint="Increase max_header_size or buffer capacity if clients legitimately need it.",
                )
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

    def _send_error(
        self,
        conn: socket.socket,
        status: int,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        """Send a plain-text error response (raw bytes, no h11).

        When *code* is set, adds the ``X-Pounce-Error-Code`` response header.
        When ``config.debug`` is True, the code and hint are appended to the
        body to aid debugging.
        """
        if self._config.debug and code is not None:
            parts = [message, "", f"Pounce error code: {code}"]
            if hint:
                parts.append(f"Hint: {hint}")
            body = "\n".join(parts).encode("utf-8")
        else:
            body = message.encode("utf-8")
        reason = _STATUS_REASONS.get(status, b"Error")
        status_line = str(status).encode("ascii") + b" " + reason
        code_header = b"x-pounce-error-code: " + code.encode("ascii") + b"\r\n" if code else b""
        with contextlib.suppress(OSError, ConnectionError):
            conn.sendall(
                b"HTTP/1.1 " + status_line + b"\r\n"
                b"content-type: text/plain; charset=utf-8\r\n"
                b"content-length: " + str(len(body)).encode("ascii") + b"\r\n"
                b"connection: close\r\n" + code_header + b"\r\n" + body
            )
