"""
Worker — the heart of pounce's request handling.

Runs a single asyncio event loop that accepts connections on a socket and
processes requests through the protocol → bridge → ASGI pipeline.

Each worker is self-contained: it owns its event loop, its set of active
connections, and its per-worker metrics.  The supervisor spawns workers as
threads (nogil) or processes (GIL) — the worker does not know which.

Connection flow (HTTP/1.1):
    socket.accept() → H1Protocol.receive_data() → build_scope()
    → negotiate_compression() → app(scope, receive, send) → access_log()

HTTP/2 and WebSocket connections are delegated to dedicated handler
modules (``_h2_handler`` and ``_ws_handler``) to keep this file focused
on core lifecycle and HTTP/1.1 handling.

"""

import asyncio
import contextlib
import logging
import os
import socket
import ssl
import sys
import threading
import weakref
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

import h11

from pounce._concurrency import cancel_and_drain, wait_first_completed
from pounce._cpu_affinity import maybe_pin_worker
from pounce._dictionary_endpoint import use_as_dictionary_headers
from pounce._drain import write_drain_503_async
from pounce._errors import ParseError, RequestTimeoutError
from pounce._h2_handler import handle_h2_connection
from pounce._headers import is_websocket_upgrade as _is_websocket_upgrade
from pounce._profile import ProfileCollector, RequestProfile
from pounce._request_pipeline import (
    log_request,
    maybe_build_builtin_response,
    negotiate_compressor,
    prepare_request,
)
from pounce._sendfile import can_use_sendfile, create_sendfile_callable
from pounce._timeouts import drain_with_timeout
from pounce._timing import ServerTiming, elapsed_ms, monotonic_ns
from pounce._types import ASGIApp, Receive, Send
from pounce._ws_handler import handle_websocket
from pounce.asgi.bridge import (
    SendState,
    create_disconnect_receive,
    create_receive_with_disconnect,
    create_send,
)
from pounce.config import ServerConfig
from pounce.lifecycle import (
    ClientDisconnected,
    ConnectionCompleted,
    ConnectionOpened,
    LifecycleCollector,
    NoopCollector,
    RequestStarted,
    ResponseCompleted,
    StreamClosed,
    StreamOpened,
    next_connection_id,
)
from pounce.lifecycle import (
    monotonic_ns as lifecycle_ns,
)
from pounce.protocols._base import (
    BodyReceived,
    ConnectionClosed,
    RequestReceived,
)
from pounce.protocols.h1 import H1Protocol

_DRAIN_HOOK_TIMEOUT = 1.0


def _make_asyncio_server_wakeup_idempotent(server: asyncio.Server) -> None:
    """Guard CPython's server wakeup against a late second transport detach.

    CPython 3.14 tracks transports in a ``WeakSet``.  An abandoned transport
    can disappear from that set before its destructor calls ``_detach()``, so
    ``close()`` and the delayed detach can both call ``_wakeup()``.  The first
    call sets ``_waiters`` to ``None``; the unguarded second call tries to
    iterate it.  Patch only the affected standard-library implementation, not
    third-party event-loop server objects.

    Remove this compatibility guard after python/cpython#130141 is resolved.
    """
    if type(server) is not asyncio.Server:
        return

    server_internal = cast("Any", server)
    server_ref = weakref.ref(server_internal)
    wakeup = type(server_internal)._wakeup
    wakeup_lock = threading.Lock()

    def wakeup_once() -> None:
        with wakeup_lock:
            current = server_ref()
            if current is None or current._waiters is None:
                return
            wakeup(current)

    server_internal._wakeup = wakeup_once


def _create_h1_protocol(
    *,
    max_incomplete_event_size: int | None = None,
) -> H1Protocol:
    """Create an HTTP/1.1 protocol handler."""
    return H1Protocol(max_incomplete_event_size=max_incomplete_event_size)


def _declared_content_length(headers: Sequence[tuple[bytes, bytes]]) -> int | None:
    """Return the declared Content-Length, if present and parseable."""
    for name, value in headers:
        if name.lower() != b"content-length":
            continue
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _request_body_expected(headers: Sequence[tuple[bytes, bytes]]) -> bool:
    """Return True when headers indicate a request body may still arrive."""
    content_length = _declared_content_length(headers)
    if content_length is not None:
        return content_length > 0

    for name, value in headers:
        if name.lower() == b"transfer-encoding" and value.strip().lower() != b"identity":
            return True
    return False


class Worker:
    """Single-threaded async worker that serves HTTP requests.

    Accepts connections from the provided socket and handles them using
    asyncio streams. Each connection is processed in its own task.

    Args:
        config: Server configuration.
        app: The ASGI application.
        sock: A bound, listening socket.
        worker_id: Numeric identifier for log differentiation.
        shutdown_event: Optional external ``threading.Event`` set by the
            supervisor to coordinate cross-thread shutdown.  When ``None``
            the worker manages its own shutdown lifecycle.
        max_connections: Per-worker connection limit for backpressure.
            ``0`` means no limit.

    """

    __slots__ = (
        "_active_connections",
        "_app",
        "_async_shutdown",
        "_config",
        "_conn_lock",
        "_connection_tasks",
        "_draining",
        "_ext_shutdown",
        "_generation",
        "_lifecycle",
        "_lifespan_state",
        "_logger",
        "_loop",
        "_max_connections",
        "_otel_span_manager",
        "_profile",
        "_sock",
        "_ssl_context",
        "_startup_status_queue",
        "_worker_id",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        sock: socket.socket,
        *,
        worker_id: int = 0,
        generation: int = 0,
        shutdown_event: threading.Event | None = None,
        max_connections: int = 0,
        ssl_context: ssl.SSLContext | None = None,
        lifecycle_collector: LifecycleCollector | None = None,
        startup_status_queue: Any | None = None,
    ) -> None:
        self._config = config
        self._app = app

        self._sock = sock
        self._worker_id = worker_id
        self._generation = generation
        self._ext_shutdown = shutdown_event
        self._async_shutdown: asyncio.Event | None = None  # created inside event loop
        self._loop: asyncio.AbstractEventLoop | None = None  # set in _serve
        self._active_connections = 0
        # asyncio.Server tracks clients weakly. Retain each accepted handler
        # task until its writer has detached so loop teardown never owns it.
        self._connection_tasks: set[asyncio.Task[None]] = set()
        self._conn_lock = threading.Lock()
        self._max_connections = max_connections
        self._ssl_context = ssl_context
        self._startup_status_queue = startup_status_queue
        self._logger = logging.getLogger(f"pounce.worker.{worker_id}")
        self._lifecycle: LifecycleCollector = lifecycle_collector or NoopCollector()
        self._lifespan_state: dict[str, Any] = {}  # Populated after lifespan startup
        self._draining = False  # Set to True during graceful reload

        self._profile = ProfileCollector(worker_id=worker_id)

        # Initialize OpenTelemetry span manager if configured
        if config.otel_endpoint:
            from pounce._otel import RequestSpanManager

            self._otel_span_manager: RequestSpanManager | None = RequestSpanManager(
                service_name=config.otel_service_name,
                enabled=True,
            )
        else:
            self._otel_span_manager = None

    def set_lifespan_state(self, state: dict[str, Any]) -> None:
        """Set the lifespan state dict to be shared with all requests.

        Args:
            state: The state dict populated during lifespan startup.

        """
        self._lifespan_state = state

    def start_draining(self) -> None:
        """Mark this worker as draining.

        When draining, the worker finishes its existing connections but no
        longer serves *new* work. Used for both graceful reload (zero-downtime
        rolling restart) and full shutdown (SIGTERM).

        Setting ``_draining`` makes every brand-new connection that is still
        accepted take the bounded clean 503 path at the top of
        ``_handle_connection`` instead of being served. On a FULL shutdown the
        accept loop is deliberately kept open during the bounded drain window
        (see ``_serve``) so those new connections get that 503 rather than
        hanging unanswered in the listen backlog (#104). Waking ``_serve`` via
        ``_async_shutdown`` begins that window; the actual ``server.close()`` is
        deferred until the worker is idle or the window elapses.
        """
        self._draining = True
        if self._loop and not self._loop.is_closed() and self._async_shutdown is not None:
            # Begin the shutdown/drain sequence in the worker's own loop.
            self._loop.call_soon_threadsafe(self._async_shutdown.set)

    def _active_connection_count(self) -> int:
        """Return the active connection count under its free-threading lock."""
        with self._conn_lock:
            return self._active_connections

    async def _await_drain_idle(self, timeout: float) -> None:
        """Keep accepting (503ing new conns) until idle or *timeout* elapses.

        Runs only on a FULL shutdown while ``_draining`` is set. The asyncio
        server stays open so in-flight connections finish AND brand-new
        connections still land on ``_handle_connection`` and receive the bounded
        clean 503 (#104). Returns as soon as the worker is idle (no active
        connections) so a worker with nothing in flight exits promptly, or once
        the bounded window elapses — after which ``_serve`` closes the server
        and aborts any stragglers.
        """
        deadline = monotonic_ns() + int(timeout * 1_000_000_000)
        while monotonic_ns() < deadline:
            with self._conn_lock:
                active = self._active_connections
            if active == 0:
                return
            await asyncio.sleep(0.02)

    def is_idle(self) -> bool:
        """Check if worker has finished all connections and is idle.

        Returns:
            True if the worker has no active connections.

        """
        with self._conn_lock:
            return self._active_connections == 0

    def run(self) -> None:
        """Start the worker's event loop (blocking)."""
        maybe_pin_worker(self._worker_id, self._config)
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        """Accept connections until shutdown is signaled."""
        self._loop = asyncio.get_running_loop()
        self._async_shutdown = asyncio.Event()

        # Per-worker ThreadPoolExecutor — prevents all workers from sharing
        # the process-wide default executor (critical in thread mode / 3.14t
        # where all workers live in one process).
        pool_size = self._config.executor_threads_per_worker
        if pool_size == 0:
            pool_size = min(32, (os.cpu_count() or 1) + 4)
        executor = ThreadPoolExecutor(
            max_workers=pool_size,
            thread_name_prefix=f"pounce-exec-{self._worker_id}",
        )
        self._loop.set_default_executor(executor)

        # Per-worker startup hook — runs on this worker's event loop so
        # any async resources (httpx clients, DB pools) bind to the
        # correct loop.
        #
        # The timeout catches apps that don't recognise the scope and
        # accidentally block (e.g. their HTTP handler calls receive()).
        # The _worker_lifecycle_receive helper returns http.disconnect
        # to unblock most handlers quickly; the timeout is a safety net.
        #
        # If the app raises, the default policy logs and proceeds — most
        # ASGI apps don't know about pounce.worker.startup and will
        # crash on it (e.g. KeyError on scope['method']).  This is
        # normal and should not prevent the worker from starting.
        #
        # Frameworks that intentionally use the hook can opt into
        # ``worker_startup_failure='shutdown'`` to fail loudly: the worker
        # refuses to serve and signals the supervisor to stop (issue #65).
        startup_ok = await self._run_worker_startup_hook()
        if not startup_ok and self._config.worker_startup_failure == "shutdown":
            self._report_startup_status(
                "error",
                "pounce.worker.startup hook raised or timed out",
            )
            self._logger.error(
                "Worker %d refusing to serve: pounce.worker.startup hook failed and "
                "worker_startup_failure='shutdown' — signalling server shutdown",
                self._worker_id,
            )
            # Never start accepting connections.  Tell the supervisor (and
            # peer workers, via the shared event) to stop so the process
            # exits instead of serving with uninitialised worker state.
            if self._ext_shutdown is not None:
                self._ext_shutdown.set()
            self._async_shutdown.set()
            # Tear down the per-worker executor we created above (the normal
            # cleanup in the serving ``finally`` is skipped by this early
            # return).  cancel_futures drops anything the failed startup hook
            # queued; wait=False keeps the abort prompt.  The
            # ``pounce.worker.shutdown`` hook is intentionally NOT run — the
            # worker never started, so there is no initialised state to tear
            # down, and the app's failing startup hook owns its own cleanup.
            executor.shutdown(wait=False, cancel_futures=True)
            return

        server = await asyncio.start_server(
            self._start_connection,
            sock=self._sock,
            ssl=self._ssl_context,
        )
        _make_asyncio_server_wakeup_idempotent(server)
        self._report_startup_status("serving")

        self._logger.debug("Worker %d started, accepting connections", self._worker_id)

        # If an external threading.Event was provided (multi-worker mode),
        # bridge it into the asyncio event loop so the supervisor can
        # trigger shutdown from its own thread.
        bridge_task: asyncio.Task[None] | None = None
        if self._ext_shutdown is not None:
            bridge_task = asyncio.create_task(self._bridge_shutdown(self._ext_shutdown))

        try:
            await self._async_shutdown.wait()
        finally:
            if bridge_task is not None:
                bridge_task.cancel()

            # Log connection draining status
            with self._conn_lock:
                active = self._active_connections
            if active > 0:
                self._logger.info(
                    "Worker %d draining %d active connection(s)...",
                    self._worker_id,
                    active,
                )
            else:
                self._logger.debug(
                    "Worker %d shutting down (no active connections)", self._worker_id
                )

            # Bounded drain-accept window (#104): on a FULL shutdown (SIGTERM)
            # keep the asyncio server ACCEPTING while in-flight connections
            # finish, so a brand-new connection lands on ``_handle_connection``
            # and gets the bounded clean 503 at the top of that method instead
            # of sitting unanswered in the listen backlog (a silent drop / HUNG).
            # On a GRACEFUL RELOAD the new generation owns the listener, so the
            # old worker must stop accepting immediately (close below) and merely
            # finish its existing connections; it must not 503 traffic the new
            # generation is meant to serve.
            timeout = self._config.shutdown_timeout
            drain_deadline = asyncio.get_running_loop().time() + timeout
            full_shutdown = self._ext_shutdown is not None and self._ext_shutdown.is_set()
            drain_reason = "shutdown" if full_shutdown or not self._draining else "reload"
            await self._run_worker_draining_hook(drain_reason, timeout)
            remaining = max(0.0, drain_deadline - asyncio.get_running_loop().time())
            if full_shutdown and self._draining:
                await self._await_drain_idle(remaining)

            # Guard against shared-fd sockets: on macOS all workers share
            # the same socket fd.  When the first worker closes the asyncio
            # server it unregisters the fd from the selector.  The second
            # worker then tries to unregister the same (now-invalid) fd,
            # raising ``ValueError: Invalid file descriptor: -1``.
            try:
                server.close()  # Stop accepting new connections

                # Grace period: wait for in-flight connections to complete.
                # Without a timeout, keep-alive or long-lived connections
                # (WebSocket, SSE) block wait_closed() indefinitely, preventing
                # the worker thread from ever exiting.
                try:
                    await asyncio.wait_for(server.wait_closed(), timeout=remaining)
                except TimeoutError:
                    self._logger.warning(
                        "Worker %d: %d connection(s) still open after %.1fs "
                        "— aborting remaining transports",
                        self._worker_id,
                        self._active_connections,
                        timeout,
                    )
                    # Force-close lingering transports so the worker can exit
                    server.abort_clients()
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(server.wait_closed(), timeout=2.0)
            except (ValueError, OSError):  # fmt: skip
                pass  # fd already closed by another worker sharing the socket

            # ``wait_closed`` proves transport detach. Cancel only application
            # cleanup still running after that point, then retain the tasks
            # through cancellation so asyncio.run() has nothing left to reap.
            connection_tasks = tuple(self._connection_tasks)
            for task in connection_tasks:
                if not task.done():
                    task.cancel()
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)

            # Per-worker shutdown hook — runs on this worker's event loop
            # for proper async resource cleanup.  Errors are logged but
            # do not prevent worker exit.
            try:
                await asyncio.wait_for(
                    self._app(
                        {
                            "type": "pounce.worker.shutdown",
                            "worker_id": self._worker_id,
                            "generation": self._generation,
                        },
                        _worker_lifecycle_receive,
                        _worker_lifecycle_send,
                    ),
                    timeout=10.0,
                )
            except Exception:
                self._logger.warning(
                    "Worker shutdown hook raised — if this is unexpected, check your app",
                    exc_info=True,
                )

            # Run executor shutdown on a dedicated pool — ``asyncio.to_thread`` /
            # ``run_in_executor(None, ...)`` would use this worker's default executor
            # (the same ``ThreadPoolExecutor`` we are closing).
            def _shutdown_sync() -> None:
                executor.shutdown(wait=True, cancel_futures=True)

            loop = asyncio.get_running_loop()
            shutdown_helper = ThreadPoolExecutor(max_workers=1)
            try:
                await asyncio.wait_for(
                    loop.run_in_executor(shutdown_helper, _shutdown_sync),
                    timeout=self._config.shutdown_timeout,
                )
            except TimeoutError:
                self._logger.warning(
                    "Worker %d: executor thread pool did not shut down within %.1fs — "
                    "aborting wait (stuck sync handlers may keep non-daemon threads alive)",
                    self._worker_id,
                    self._config.shutdown_timeout,
                )
                executor.shutdown(wait=False, cancel_futures=True)
            finally:
                shutdown_helper.shutdown(wait=False)

            # Flush any spans still queued in the BatchSpanProcessor so they are
            # exported rather than dropped when the worker process exits (#133).
            if self._otel_span_manager is not None:
                from pounce._otel import flush_otel

                flush_otel()

            self._logger.debug("Worker %d stopped", self._worker_id)

    def _report_startup_status(self, status: str, detail: str | None = None) -> None:
        """Report initial startup state to the owning supervisor."""
        if self._startup_status_queue is not None:
            self._startup_status_queue.put((status, self._worker_id, detail))

    async def _run_worker_startup_hook(self) -> bool:
        """Run the ``pounce.worker.startup`` hook. Return True on success.

        Generic ASGI apps that don't recognise the scope typically raise
        (e.g. ``KeyError`` on ``scope['method']``) — that is normal and
        returns ``False`` without being fatal unless
        ``worker_startup_failure='shutdown'`` (see :meth:`_serve`).  A
        timeout is also treated as a failure.
        """
        fatal = self._config.worker_startup_failure == "shutdown"
        level = logging.ERROR if fatal else logging.WARNING
        try:
            await asyncio.wait_for(
                self._app(
                    {
                        "type": "pounce.worker.startup",
                        "worker_id": self._worker_id,
                        "generation": self._generation,
                    },
                    _worker_lifecycle_receive,
                    _worker_lifecycle_send,
                ),
                timeout=self._config.startup_timeout,
            )
        except TimeoutError:
            self._logger.log(
                level,
                "Worker %d startup hook timed out after %.1fs",
                self._worker_id,
                self._config.startup_timeout,
            )
            return False
        except Exception:
            self._logger.log(
                level,
                "Worker startup hook raised — if this is unexpected, check your app",
                exc_info=True,
            )
            return False
        return True

    async def _run_worker_draining_hook(self, reason: str, timeout: float) -> None:
        """Notify the app that active streams should finish promptly."""
        try:
            await asyncio.wait_for(
                self._app(
                    {
                        "type": "pounce.worker.draining",
                        "worker_id": self._worker_id,
                        "generation": self._generation,
                        "reason": reason,
                        "timeout": timeout,
                    },
                    _worker_lifecycle_receive,
                    _worker_lifecycle_send,
                ),
                timeout=min(timeout, _DRAIN_HOOK_TIMEOUT),
            )
        except Exception:
            self._logger.debug(
                "Worker draining hook raised or timed out (expected for apps without it)",
                exc_info=True,
            )

    async def _bridge_shutdown(self, ext_event: threading.Event) -> None:
        """Poll an external ``threading.Event`` and set the async shutdown.

        Runs as a background task inside the worker's event loop.  Polls
        the threading event every 50 ms for responsive shutdown without
        busy-waiting.

        """
        loop = asyncio.get_running_loop()
        while not ext_event.is_set():
            await asyncio.sleep(0.05)
        # Trigger the asyncio-side shutdown
        if self._async_shutdown is not None:
            loop.call_soon(self._async_shutdown.set)

    def shutdown(self) -> None:
        """Signal the worker to stop accepting connections.

        Safe to call from any thread.  In multi-worker mode the
        supervisor sets the shared ``threading.Event`` which the bridge
        task picks up.  In single-worker mode we use
        ``call_soon_threadsafe`` to safely set the asyncio event from
        an external thread.

        """
        if self._ext_shutdown is not None:
            self._ext_shutdown.set()
        elif self._async_shutdown is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._async_shutdown.set)

    def _start_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Retain an accepted connection task through transport detach."""
        task = asyncio.create_task(self._run_connection(reader, writer))
        self._connection_tasks.add(task)
        task.add_done_callback(self._connection_tasks.discard)

    async def _run_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Run one handler with unconditional writer ownership."""
        try:
            await self._handle_connection(reader, writer)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("Unhandled error before connection cleanup")
        finally:
            writer.close()
            with contextlib.suppress(OSError, ConnectionError, RequestTimeoutError):
                await writer.wait_closed()

    # ------------------------------------------------------------------
    # Connection dispatch
    # ------------------------------------------------------------------

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single TCP connection through request-response cycles.

        After TLS handshake, checks ALPN to determine protocol:
        - "h2" → HTTP/2 multiplexed connection handler
        - "http/1.1" or None → HTTP/1.1 keep-alive loop

        HTTP/1.1 also supports WebSocket upgrade mid-connection.

        """
        # Reject new connections when draining (graceful shutdown or reload).
        # Existing connections continue processing, but we stop accepting new
        # work to allow the worker to drain cleanly.
        if self._draining:
            await write_drain_503_async(writer, timeout=self._config.write_timeout)
            return

        # Connection backpressure — reject when at capacity.
        # Send a minimal HTTP 503 response with Retry-After instead of
        # silently closing, so clients get actionable feedback.
        with self._conn_lock:
            at_capacity = (
                self._max_connections > 0 and self._active_connections >= self._max_connections
            )
        if at_capacity:
            try:
                writer.write(
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Retry-After: 5\r\n"
                    b"Content-Length: 19\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Connection: close\r\n"
                    b"\r\n"
                    b"Service Unavailable"
                )
                await drain_with_timeout(writer, self._config.write_timeout)
            except (OSError, ConnectionError, RequestTimeoutError):  # fmt: skip
                pass
            finally:
                writer.close()
                with contextlib.suppress(OSError, ConnectionError, RequestTimeoutError):
                    await writer.wait_closed()
            return

        # Disable Nagle's algorithm for low-latency request-response
        raw_sock = writer.get_extra_info("socket")
        if raw_sock is not None and raw_sock.family in (socket.AF_INET, socket.AF_INET6):
            raw_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        with self._conn_lock:
            self._active_connections += 1
        conn_id = next_connection_id()
        conn_start = lifecycle_ns()
        peername = writer.get_extra_info("peername")
        # Unix domain sockets: peername is a string path (or empty string)
        if peername and isinstance(peername, tuple) and len(peername) >= 2:
            client = (peername[0], peername[1])
        else:
            client = ("unix", 0)
        client_str = f"{client[0]}:{client[1]}"
        server_addr = writer.get_extra_info("sockname")
        if server_addr and isinstance(server_addr, tuple) and len(server_addr) >= 2:
            server = (server_addr[0], server_addr[1])
        elif self._config.uds is not None:
            server = (self._config.uds, 0)
        else:
            server = (self._config.host, self._config.port)

        # Determine protocol and emit ConnectionOpened
        detected_protocol = "h1"

        # Check ALPN negotiation result (only present on TLS connections)
        ssl_object = writer.get_extra_info("ssl_object")
        if ssl_object is not None:
            alpn = ssl_object.selected_alpn_protocol()
            if alpn == "h2":
                detected_protocol = "h2"
                self._lifecycle.record(
                    ConnectionOpened(
                        connection_id=conn_id,
                        worker_id=self._worker_id,
                        client_addr=client[0],
                        client_port=client[1],
                        server_addr=server[0],
                        server_port=server[1],
                        protocol="h2",
                        timestamp_ns=conn_start,
                    )
                )
                try:
                    await handle_h2_connection(
                        cast("ASGIApp", self._app),
                        self._config,
                        self._logger,
                        reader,
                        writer,
                        client,
                        server,
                        client_str,
                        worker_id=self._worker_id,
                        lifespan_state=self._lifespan_state,
                        is_draining=lambda: self._draining,
                    )
                except Exception:
                    self._logger.exception(
                        "Unhandled error on H2 connection from %s",
                        client_str,
                    )
                finally:
                    self._lifecycle.record(
                        ConnectionCompleted(
                            connection_id=conn_id,
                            worker_id=self._worker_id,
                            requests_served=0,
                            total_bytes_sent=0,
                            duration_ms=round(
                                (lifecycle_ns() - conn_start) / 1_000_000,
                                1,
                            ),
                            reason="complete",
                            timestamp_ns=lifecycle_ns(),
                        )
                    )
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except (OSError, ConnectionError):  # fmt: skip
                        pass
                    finally:
                        # Idle means the transport has detached, not merely
                        # that application work has returned.
                        with self._conn_lock:
                            self._active_connections -= 1
                return

        self._lifecycle.record(
            ConnectionOpened(
                connection_id=conn_id,
                worker_id=self._worker_id,
                client_addr=client[0],
                client_port=client[1],
                server_addr=server[0],
                server_port=server[1],
                protocol=detected_protocol,
                timestamp_ns=conn_start,
            )
        )

        proto = _create_h1_protocol(
            max_incomplete_event_size=self._config.h11_max_incomplete_event_size,
        )

        max_requests = self._config.max_requests_per_connection
        request_count = 0
        total_bytes = 0
        close_reason = "complete"

        try:
            # Reusable helper: process a batch of events from the protocol
            # layer. Returns True if the connection should close.
            async def _process_events(
                events: list,
                profile_ctx: RequestProfile | None = None,
            ) -> bool:
                nonlocal request_count
                idx = 0
                while idx < len(events):
                    event = events[idx]
                    idx += 1

                    if isinstance(event, RequestReceived):
                        request_count += 1

                        # Collect body events from the same parse batch
                        initial_body: list[BodyReceived] = []
                        while idx < len(events):
                            next_evt = events[idx]
                            if isinstance(next_evt, BodyReceived):
                                initial_body.append(next_evt)
                                idx += 1
                            else:
                                break

                        # Check for WebSocket upgrade
                        if _is_websocket_upgrade(event):
                            await handle_websocket(
                                cast("ASGIApp", self._app),
                                self._config,
                                self._logger,
                                event,
                                reader,
                                writer,
                                client,
                                server,
                                client_str,
                                worker_id=self._worker_id,
                                lifespan_state=self._lifespan_state,
                            )
                            return True  # WS takes over
                        await self._handle_request(
                            event,
                            proto,
                            reader,
                            writer,
                            client,
                            server,
                            client_str,
                            initial_body=initial_body,
                            connection_id=conn_id,
                            profile_ctx=profile_ctx,
                        )
                        if profile_ctx is not None:
                            self._profile.record(profile_ctx)
                    elif isinstance(event, ConnectionClosed):
                        return True  # Clean close
                return False

            # Use header_timeout for the initial header read of each request,
            # and keep_alive_timeout when waiting between keep-alive cycles.
            # header_timeout protects against slowloris (slow-header DoS) attacks.
            header_timeout = self._config.header_timeout
            ka_timeout = self._config.keep_alive_timeout
            awaiting_headers = True  # True until we receive the first request headers

            while True:
                # Read data from the client
                read_timeout = header_timeout if awaiting_headers else ka_timeout
                should_sample = self._profile.should_sample()
                read_start = monotonic_ns() if should_sample else 0
                try:
                    data = await asyncio.wait_for(
                        reader.read(65536),
                        timeout=read_timeout,
                    )
                except TimeoutError:
                    close_reason = "timeout"
                    break  # Timeout — close connection
                except (ConnectionError, OSError):  # fmt: skip
                    close_reason = "client_disconnect"
                    break

                if not data:
                    close_reason = "client_disconnect"
                    break  # Client disconnected

                read_ms = elapsed_ms(read_start) if should_sample else 0.0
                parse_start = monotonic_ns() if should_sample else 0
                # Parse through the protocol layer
                try:
                    events = proto.receive_data(data)
                except ParseError as exc:
                    close_reason = "error"
                    await self._send_error(
                        writer,
                        proto,
                        400,
                        str(exc),
                        code=exc.code,
                        hint=exc.hint,
                    )
                    break

                parse_ms = elapsed_ms(parse_start) if should_sample else 0.0
                profile_ctx = (
                    RequestProfile(read_ms=read_ms, parse_ms=parse_ms) if should_sample else None
                )

                if await _process_events(events, profile_ctx=profile_ctx):
                    return

                # After processing events, we've handled a request — switch
                # to keep-alive timeout for the inter-request idle period.
                awaiting_headers = False

                # Draining (#104): once the worker is draining, do not start a
                # new keep-alive cycle on this connection. The request we just
                # served completed normally; closing now lets the worker reach
                # idle promptly so the bounded drain window can end instead of
                # being pinned by a client that keeps looping requests.
                if self._draining:
                    break

                # Enforce max requests per connection
                if max_requests > 0 and request_count >= max_requests:
                    break  # Limit reached — close connection

                # Check if we can do another cycle (keep-alive)
                try:
                    proto.start_new_cycle()
                except h11.LocalProtocolError:
                    break  # Connection can't be reused
                except RuntimeError:
                    break  # Connection can't be reused

                # Next read is the start of a new request — use header_timeout
                awaiting_headers = True

                # NOTE: HTTP pipelining (next request buffered in h11
                # before we call reader.read()) is intentionally not
                # optimised here.  Pipelining is rarely used by modern
                # clients.  h11 preserves its buffer across cycles, so
                # the next reader.read() + receive_data() will flush it.

        except Exception as _conn_exc:
            close_reason = "error"
            self._logger.exception("Unhandled error on connection from %s", client_str)
            from pounce import _output

            _output.branded_traceback(_conn_exc, worker_id=self._worker_id)
        finally:
            self._lifecycle.record(
                ConnectionCompleted(
                    connection_id=conn_id,
                    worker_id=self._worker_id,
                    requests_served=request_count,
                    total_bytes_sent=total_bytes,
                    duration_ms=round(
                        (lifecycle_ns() - conn_start) / 1_000_000,
                        1,
                    ),
                    reason=close_reason,
                    timestamp_ns=lifecycle_ns(),
                )
            )
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ConnectionError):  # fmt: skip
                pass
            finally:
                # Keep the handler active through transport detach so worker
                # drain cannot return while asyncio still owns the socket.
                with self._conn_lock:
                    self._active_connections -= 1

    # ------------------------------------------------------------------
    # HTTP/1.1 request processing
    # ------------------------------------------------------------------

    async def _handle_request(
        self,
        request: RequestReceived,
        proto: H1Protocol,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        client: tuple[str, int],
        server: tuple[str, int],
        client_str: str,
        *,
        initial_body: list[BodyReceived] | None = None,
        connection_id: int = 0,
        profile_ctx: RequestProfile | None = None,
    ) -> None:
        """Process a single HTTP request through the ASGI pipeline."""
        request_start = monotonic_ns()

        self._lifecycle.record(
            RequestStarted(
                connection_id=connection_id,
                worker_id=self._worker_id,
                method=request.method.decode("ascii", errors="replace"),
                path=request.target.decode("ascii", errors="replace"),
                http_version=request.http_version,
                timestamp_ns=request_start,
            )
        )

        # Build ASGI scope and extract request ID
        scope, request_id = prepare_request(
            request, self._config, client, server, self._lifespan_state
        )
        scope.setdefault("extensions", {})["pounce.worker"] = {
            "version": 1,
            "worker_id": self._worker_id,
            "generation": self._generation,
        }

        compressor, dictionary = negotiate_compressor(
            self._config,
            request.headers,
            request_target=request.target.decode("ascii", errors="replace"),
        )

        if self._body_exceeds_limit(request, initial_body):
            await self._send_error(
                writer,
                proto,
                413,
                "Request body exceeds max_request_size",
                code="POUNCE_LIMIT_REQUEST_TOO_LARGE",
                hint=(
                    "Reduce the request body size or raise ServerConfig.max_request_size "
                    "for this deployment."
                ),
            )
            return

        # Advertise protocol-owned sendfile for static file serving. StaticFiles
        # emits a file-range intent; the h11 bridge keeps Content-Length and
        # chunk framing consistent before transferring bytes with os.sendfile.
        sendfile_fn = None
        if compressor is None and can_use_sendfile(writer):
            sendfile_fn = create_sendfile_callable(
                writer,
                write_timeout=self._config.write_timeout,
            )
            scope.setdefault("extensions", {})["pounce.sendfile"] = {"version": 1}

        # Built-in endpoints bypass ASGI; selection and response construction
        # are shared across H1/H2/H3 while each transport owns serialization.
        builtin = maybe_build_builtin_response(
            self._config,
            request.method,
            scope["path"],
            worker_id=self._worker_id,
            active_connections=self._active_connection_count,
            draining=self._draining,
        )
        if builtin is not None:
            send_state = SendState()
            send_state.status = builtin.status
            send_fn = create_send(
                proto,
                writer,
                send_state,
                request_method=request.method,
                request_id=request_id,
                config=self._config,
                server=server,
            )
            await send_fn(
                {
                    "type": "http.response.start",
                    "status": builtin.status,
                    "headers": builtin.headers,
                }
            )
            await send_fn({"type": "http.response.body", "body": builtin.body})
            return

        # Set up timing if enabled
        timing: ServerTiming | None = None
        if self._config.server_timing:
            timing = ServerTiming()
            timing.add("parse", elapsed_ms(request_start))

        # Honour ``Expect: 100-continue``: a compliant client withholds the
        # request body until it observes an interim ``100 Continue``. Emit it
        # via h11's InformationalResponse before we read the body, otherwise
        # such clients stall. h11 only reports this as True when a body is
        # actually expected, so the final response still flows normally after.
        # H1-only — H2/H3 do not use this mechanism. Trailers are unsupported.
        if proto.client_is_waiting_for_100_continue:
            writer.write(proto.send_100_continue())
            await drain_with_timeout(writer, self._config.write_timeout)

        # Determine body status and create receive callable.
        # All paths now create a disconnect event so the ASGI app can
        # receive ``http.disconnect`` when the client drops — critical
        # for streaming/SSE responses.
        disconnect = asyncio.Event()
        body_complete = False
        body_queue: asyncio.Queue[BodyReceived] | None = None

        if initial_body:
            # Body events arrived with the request head.
            body_queue = asyncio.Queue()
            for body_event in initial_body:
                await body_queue.put(body_event)
                if not body_event.more:
                    body_complete = True
            receive = create_receive_with_disconnect(body_queue, disconnect)
        elif _request_body_expected(request.headers):
            # Headers can arrive before body bytes. Keep reading from the
            # connection instead of presenting a bodyless request to ASGI.
            body_queue = asyncio.Queue()
            receive = create_receive_with_disconnect(body_queue, disconnect)
        else:
            # No body events — bodyless request (GET/HEAD).
            receive = create_disconnect_receive(disconnect)
            body_complete = True

        app_start = monotonic_ns()
        profile_app_start = app_start if profile_ctx is not None else 0
        send_state = SendState()
        stream_started_ns = 0

        def record_stream_started() -> None:
            nonlocal stream_started_ns
            if stream_started_ns:
                return
            stream_started_ns = lifecycle_ns()
            self._lifecycle.record(
                StreamOpened(
                    connection_id=connection_id,
                    worker_id=self._worker_id,
                    method=scope.get("method", "unknown"),
                    path=scope.get("path", "/"),
                    timestamp_ns=stream_started_ns,
                )
            )

        # Build Use-As-Dictionary advertisement headers for matching paths
        dict_advert_headers: list[tuple[bytes, bytes]] | None = None
        if self._config.compression_dictionaries:
            target_str = request.target.decode("ascii", errors="replace")
            dict_advert_headers = (
                use_as_dictionary_headers(
                    self._config.compression_dictionaries,
                    target_str,
                )
                or None
            )

        send = create_send(
            proto,
            writer,
            send_state,
            timing=timing,
            compressor=compressor,
            request_method=request.method,
            request_path=request.target,
            request_id=request_id,
            config=self._config,
            server=server,
            dictionary_hash=dictionary.sf_hash if dictionary else None,
            extra_headers=dict_advert_headers,
            sendfile_fn=sendfile_fn,
            compression_min_size=self._config.compression_min_size,
            on_stream_start=record_stream_started,
        )

        # Create OpenTelemetry span for this request
        otel_span = None
        if self._otel_span_manager:
            otel_span = self._otel_span_manager.create_request_span(
                method=scope.get("method", "GET"),
                path=scope.get("path", "/"),
                headers=request.headers,
                scheme=scope.get("scheme", "http"),
                server_host=server[0],
                server_port=server[1],
            )
            otel_span.__enter__()

        try:
            # Call the ASGI app with concurrent disconnect monitoring.
            # Mirrors the WebSocket handler pattern: two tasks, wait for
            # first to complete, cancel the other.
            if body_complete:
                await self._run_with_disconnect_monitor(
                    scope,
                    receive,
                    send,
                    send_state,
                    reader,
                    writer,
                    proto,
                    disconnect,
                    connection_id=connection_id,
                )
            else:
                # Body still arriving — read concurrently with the app.
                # body_queue is guaranteed non-None here because initial_body
                # was truthy but body_complete stayed False.
                assert body_queue is not None
                await self._run_with_body_reader(
                    scope,
                    receive,
                    send,
                    send_state,
                    body_queue,
                    proto,
                    reader,
                    writer,
                    disconnect=disconnect,
                )
        except Exception as e:
            # Record exception in span
            if self._otel_span_manager and otel_span:
                self._otel_span_manager.record_exception(otel_span, e)
            raise
        finally:
            # Record response and end span
            if self._otel_span_manager and otel_span:
                self._otel_span_manager.record_response(
                    otel_span,
                    status_code=send_state.status or 500,
                    response_size=send_state.bytes_sent,
                )
                otel_span.__exit__(None, None, None)

        if timing:
            timing.add("app", elapsed_ms(app_start))

        if profile_ctx is not None:
            profile_ctx.app_ms = elapsed_ms(profile_app_start)

        if stream_started_ns:
            if self._draining:
                stream_reason = "drain"
            elif send_state.client_disconnected:
                stream_reason = "client_disconnect"
            elif send_state.response_complete:
                stream_reason = "complete"
            else:
                stream_reason = "error"
            self._lifecycle.record(
                StreamClosed(
                    connection_id=connection_id,
                    worker_id=self._worker_id,
                    duration_ms=round((lifecycle_ns() - stream_started_ns) / 1_000_000, 1),
                    reason=stream_reason,
                    timestamp_ns=lifecycle_ns(),
                )
            )

        # If app returned without sending http.response.start, send 500 now.
        # Do not treat empty-body responses (HEAD/204/304) as "no response".
        if not send_state.response_started:
            status = 500
            with contextlib.suppress(OSError, ConnectionError, h11.LocalProtocolError):
                await self._send_error(
                    writer,
                    proto,
                    status,
                    "Internal Server Error",
                    code="POUNCE_APP_NO_RESPONSE",
                    hint="The ASGI app returned without sending http.response.start.",
                )
            send_state.status = status

        # Record response lifecycle event
        self._lifecycle.record(
            ResponseCompleted(
                connection_id=connection_id,
                worker_id=self._worker_id,
                status=send_state.status,
                bytes_sent=send_state.bytes_sent,
                duration_ms=elapsed_ms(request_start),
                timestamp_ns=lifecycle_ns(),
                method=scope.get("method", "unknown"),
                streaming=send_state.streaming,
            )
        )

        # Flush the writer
        drain_start = monotonic_ns() if profile_ctx is not None else 0
        with contextlib.suppress(ConnectionError, OSError, RequestTimeoutError):
            await drain_with_timeout(writer, self._config.write_timeout)
        if profile_ctx is not None:
            profile_ctx.drain_ms = elapsed_ms(drain_start)

        # Access log
        log_request(
            self._config,
            request.method.decode("ascii", errors="replace"),
            request.target.decode("ascii", errors="replace"),
            send_state.status,
            send_state.bytes_sent,
            elapsed_ms(request_start),
            client_str,
            http_version=scope.get("http_version", "1.1"),
            request_id=request_id,
            worker_id=self._worker_id,
        )

    async def _run_app_with_error_handling(
        self,
        scope: dict,
        receive: Receive,
        send: Send,
        send_state: SendState,
        writer: asyncio.StreamWriter,
        proto: H1Protocol,
    ) -> None:
        """Run the ASGI app with branded traceback and error response handling."""
        try:
            await self._app(scope, receive, send)
        except RequestTimeoutError as exc:
            if exc.code != "POUNCE_TIMEOUT_WRITE":
                raise
            if send_state.status == 0:
                send_state.status = 408
        except Exception as _app_exc:
            self._logger.exception("ASGI app error on %s %s", scope["method"], scope["path"])
            from pounce import _output

            _output.branded_traceback(_app_exc, worker_id=self._worker_id)
            with contextlib.suppress(OSError, ConnectionError, h11.LocalProtocolError):
                if self._config.debug:
                    exc_info = sys.exc_info()
                    await self._send_debug_error(
                        writer,
                        proto,
                        (exc_info[0], exc_info[1], exc_info[2])
                        if exc_info[0] is not None and exc_info[1] is not None
                        else (Exception, Exception("Unknown error"), exc_info[2]),
                        request_method=scope.get("method", "GET"),
                        request_path=scope.get("path", "/"),
                        request_headers=scope.get("headers"),
                    )
                else:
                    await self._send_error(
                        writer,
                        proto,
                        500,
                        "Internal Server Error",
                        code="POUNCE_APP_E",
                        hint="Check worker logs for the application traceback.",
                    )
            if send_state.status == 0:
                send_state.status = 500

    async def _run_with_disconnect_monitor(
        self,
        scope: dict,
        receive: Receive,
        send: Send,
        send_state: SendState,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        proto: H1Protocol,
        disconnect: asyncio.Event,
        *,
        connection_id: int = 0,
    ) -> None:
        """Run the ASGI app with concurrent client disconnect monitoring.

        For bodyless requests (GET/HEAD) where the body is already complete.
        Spawns a monitor task that reads from the socket to detect client
        disconnect, and cancels the app task when the client drops.

        Mirrors the WebSocket handler's concurrent-task pattern.

        """
        app_task = asyncio.create_task(
            self._run_app_with_error_handling(
                scope,
                receive,
                send,
                send_state,
                writer,
                proto,
            )
        )
        monitor_task = asyncio.create_task(self._monitor_disconnect(reader, disconnect))

        try:
            # Race the app against the disconnect monitor. The loser is *not*
            # auto-cancelled here: if the client drops after the full response
            # was already flushed, the app must be allowed to finish its
            # post-response work, so the caller owns which task gets drained.
            done, pending = await wait_first_completed(app_task, monitor_task)

            # If the monitor won (client disconnected), emit event
            if monitor_task in done and app_task not in done:
                send_state.client_disconnected = True
                self._lifecycle.record(
                    ClientDisconnected(
                        connection_id=connection_id,
                        worker_id=self._worker_id,
                        during_streaming=send_state.bytes_sent > 0,
                        timestamp_ns=lifecycle_ns(),
                    )
                )
                if send_state.response_complete:
                    try:
                        await app_task
                    except Exception:
                        if send_state.status == 0:
                            send_state.status = 500
                    return

            await cancel_and_drain(pending)

            # Propagate app exceptions for status tracking
            if app_task in done:
                try:
                    app_task.result()
                except Exception:
                    if send_state.status == 0:
                        send_state.status = 500
        except Exception:
            self._logger.exception(
                "Unhandled error during request handling for %s",
                scope.get("path", "?"),
            )
            if send_state.status == 0:
                send_state.status = 500

    async def _run_with_body_reader(
        self,
        scope: dict,
        receive: Receive,
        send: Send,
        send_state: SendState,
        body_queue: asyncio.Queue[BodyReceived],
        proto: H1Protocol,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        disconnect: asyncio.Event | None = None,
    ) -> None:
        """Run the ASGI app while concurrently reading request body.

        Used when the request body spans multiple socket reads (large POSTs,
        chunked uploads). Follows the same concurrent-task pattern as the
        WebSocket handler.

        The actual HTTP status is captured in *send_state* by the send
        callable; this method only sets 500 as a fallback when the app
        raises without having started a response.

        Args:
            disconnect: Optional event to set when the reader detects
                client disconnect (EOF or connection error).  When
                provided, this signals the disconnect-aware receive
                callable so the ASGI app receives ``http.disconnect``.

        """

        async def _read_body() -> bool:
            """Read remaining body data from the connection into the queue.

            Enforces max_request_size for streaming/chunked bodies. If the
            accumulated body exceeds the limit, the stream is terminated
            with an empty final chunk so the ASGI app sees EOF.
            """
            max_body = self._config.max_request_size
            total_bytes_read = 0
            timed_out = False

            try:
                while True:
                    try:
                        data = await asyncio.wait_for(
                            reader.read(65536),
                            timeout=self._config.request_timeout,
                        )
                    except TimeoutError:
                        timed_out = True
                        return True
                    except (ConnectionError, OSError):  # fmt: skip
                        await body_queue.put(BodyReceived(data=b"", more=False))
                        return False

                    if not data:
                        await body_queue.put(BodyReceived(data=b"", more=False))
                        return False

                    try:
                        events = proto.receive_data(data)
                    except ParseError:
                        await body_queue.put(BodyReceived(data=b"", more=False))
                        return False

                    for evt in events:
                        if isinstance(evt, BodyReceived):
                            total_bytes_read += len(evt.data)
                            if total_bytes_read > max_body:
                                self._logger.warning(
                                    "Request body exceeds max_request_size (%d bytes)",
                                    max_body,
                                )
                                await body_queue.put(BodyReceived(data=b"", more=False))
                                return False
                            await body_queue.put(evt)
                            if not evt.more:
                                return False
            except asyncio.CancelledError:
                # Ensure the app unblocks if cancelled
                await body_queue.put(BodyReceived(data=b"", more=False))
                raise
            finally:
                # Signal disconnect so the ASGI app's receive() returns
                # http.disconnect after the body is complete.
                if disconnect is not None and not timed_out:
                    disconnect.set()
            return False

        app_task = asyncio.create_task(
            self._run_app_with_error_handling(
                scope,
                receive,
                send,
                send_state,
                writer,
                proto,
            )
        )
        reader_task = asyncio.create_task(_read_body())

        try:
            # Race the app against the body reader. The loser is *not*
            # auto-cancelled here: when the reader wins, the ASGI app must be
            # allowed to finish generating its response, so the caller owns
            # which task gets drained.
            done, pending = await wait_first_completed(app_task, reader_task)

            if reader_task in done and reader_task.result():
                await cancel_and_drain(pending)
                if send_state.status == 0:
                    await self._send_error(
                        writer,
                        proto,
                        408,
                        "Request Timeout",
                        code="POUNCE_TIMEOUT_REQUEST_BODY",
                        hint=(
                            "Increase request_timeout only for clients that "
                            "legitimately upload slowly."
                        ),
                    )
                    send_state.status = 408
                return

            if reader_task in done and app_task not in done:
                # The request body is complete; let the ASGI app finish
                # generating its response.
                try:
                    await app_task
                except Exception:
                    if send_state.status == 0:
                        send_state.status = 500
            else:
                # The app won (or both finished): drain any remaining reader
                # task and propagate the app exception for status tracking.
                await cancel_and_drain(pending)
                if app_task in done:
                    try:
                        app_task.result()
                    except Exception:
                        if send_state.status == 0:
                            send_state.status = 500
        except Exception:
            self._logger.exception(
                "Unhandled error during body reading for %s",
                scope.get("path", "?"),
            )
            if send_state.status == 0:
                send_state.status = 500

    def _body_exceeds_limit(
        self,
        request: RequestReceived,
        initial_body: list[BodyReceived] | None,
    ) -> bool:
        """Return True when a request body is known to exceed max_request_size."""
        max_body = self._config.max_request_size
        declared = _declared_content_length(request.headers)
        if declared is not None and declared > max_body:
            self._logger.warning(
                "Request Content-Length %d exceeds max_request_size (%d bytes)",
                declared,
                max_body,
            )
            return True

        if initial_body:
            total = sum(len(event.data) for event in initial_body)
            if total > max_body:
                self._logger.warning(
                    "Request body exceeds max_request_size (%d bytes)",
                    max_body,
                )
                return True
        return False

    # ------------------------------------------------------------------
    # Client disconnect monitoring
    # ------------------------------------------------------------------

    @staticmethod
    async def _monitor_disconnect(
        reader: asyncio.StreamReader,
        disconnect: asyncio.Event,
    ) -> None:
        """Monitor the TCP connection for client disconnect.

        Reads from the socket to detect when the client closes the
        connection (EOF or error).  Sets *disconnect* to signal the
        ASGI ``receive()`` callable, which unblocks any app waiting
        for ``http.disconnect``.

        This mirrors the WebSocket handler's frame-reader pattern but
        only watches for connection close — no data is expected.

        """
        try:
            while True:
                data = await reader.read(1)
                if not data:
                    # Client disconnected — EOF
                    break
        except (ConnectionError, OSError):  # fmt: skip
            pass
        finally:
            disconnect.set()

    # ------------------------------------------------------------------
    # Error responses
    # ------------------------------------------------------------------

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        proto: H1Protocol,
        status: int,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        """Send a plain-text error response.

        When *code* is set, adds the ``X-Pounce-Error-Code`` response header so
        callers can disambiguate identical-status failures. When
        ``config.debug`` is True, ``hint`` (and the code) are appended to the
        response body for easier debugging.
        """
        if self._config.debug and code is not None:
            parts = [message, "", f"Pounce error code: {code}"]
            if hint:
                parts.append(f"Hint: {hint}")
            body = "\n".join(parts).encode("utf-8")
        else:
            body = message.encode("utf-8")
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", b"text/plain; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"connection", b"close"),
        ]
        if code is not None:
            headers.append((b"x-pounce-error-code", code.encode("ascii")))
        try:
            raw = proto.send_response(status, headers)
            writer.write(raw)
            writer.write(proto.send_body(body, more=False))
            await drain_with_timeout(writer, self._config.write_timeout)
        except Exception:  # noqa: S110 — best-effort error response on already-failing connection
            pass

    async def _send_debug_error(
        self,
        writer: asyncio.StreamWriter,
        proto: H1Protocol,
        exc_info: tuple[type[BaseException], BaseException, Any],
        *,
        request_method: str = "GET",
        request_path: str = "/",
        request_headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        """Send a rich debug error response with traceback.

        Args:
            writer: Stream writer for sending response.
            proto: Protocol handler.
            exc_info: Exception info tuple (type, value, traceback).
            request_method: HTTP method.
            request_path: Request path.
            request_headers: Request headers.

        """
        from pounce._debug import create_debug_error_response

        try:
            status, headers, body = create_debug_error_response(
                *exc_info,
                request_method=request_method,
                request_path=request_path,
                request_headers=request_headers,
            )

            raw = proto.send_response(status, headers)
            writer.write(raw)
            writer.write(proto.send_body(body, more=False))
            await drain_with_timeout(writer, self._config.write_timeout)
        except Exception:
            # Fallback to simple error if debug page fails
            await self._send_error(
                writer,
                proto,
                500,
                "Internal Server Error",
                code="POUNCE_APP_DEBUG_PAGE_FAILED",
                hint="Debug error page rendering failed; see worker logs.",
            )


# ---------------------------------------------------------------------------
# Worker lifecycle scope helpers
# ---------------------------------------------------------------------------


async def _worker_lifecycle_receive() -> dict[str, Any]:
    """Receive callable for worker lifecycle scopes.

    Returns ``http.disconnect`` immediately so that apps which pass
    unrecognised scope types to their HTTP handler (and call
    ``receive()``) unblock and return quickly instead of hanging.
    """
    return {"type": "http.disconnect"}


async def _worker_lifecycle_send(message: dict[str, Any]) -> None:
    """No-op send for worker lifecycle scopes."""


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


# _is_websocket_upgrade imported from pounce._headers
