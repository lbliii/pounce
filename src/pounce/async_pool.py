"""
AsyncPool — dedicated event loop for streaming and WebSocket connections.

Receives handoffs from SyncWorkers when the ASGI app returns a streaming
response (more_body=True) or WebSocket upgrade. Wraps the socket in asyncio
streams and continues the ASGI lifecycle.

"""

import asyncio
import contextlib
import logging
import queue
import socket
import ssl
import threading
from dataclasses import dataclass
from typing import Any

from pounce._compression import Compressor, create_compressor
from pounce._errors import RequestTimeoutError
from pounce._headers import get_header as _get_header
from pounce._timeouts import drain_with_timeout
from pounce._timing import elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.bridge import SendState, create_send
from pounce.config import ServerConfig
from pounce.lifecycle import (
    LifecycleCollector,
    ResponseCompleted,
    StreamClosed,
    StreamOpened,
)
from pounce.protocols._base import RequestReceived
from pounce.protocols.h1 import H1Protocol


def _create_h1_protocol(
    *,
    max_incomplete_event_size: int | None = None,
) -> H1Protocol:
    """Create an HTTP/1.1 protocol handler."""
    return H1Protocol(max_incomplete_event_size=max_incomplete_event_size)


@dataclass(frozen=True, slots=True)
class StreamingHandoff:
    """Handoff for HTTP streaming response (more_body=True)."""

    conn: socket.socket
    scope: dict[str, Any]
    body: bytes
    request_id: str | None
    worker_id: int = 0
    connection_id: int = 0


@dataclass(frozen=True, slots=True)
class WebSocketHandoff:
    """Handoff for WebSocket upgrade."""

    conn: socket.socket
    request: RequestReceived
    client: tuple[str, int]
    server: tuple[str, int]
    scope: dict[str, Any]


type HandoffRequest = StreamingHandoff | WebSocketHandoff


class AsyncPool:
    """Dedicated event loop for streaming/SSE/WebSocket connections.

    Accepts handoffs from SyncWorker threads via a thread-safe queue.
    Wraps sockets in asyncio streams and runs the ASGI app.

    """

    __slots__ = (
        "_app",
        "_config",
        "_ext_shutdown",
        "_handoff_tasks",
        "_lifecycle",
        "_lifespan_state",
        "_logger",
        "_loop",
        "_pool_shutdown",
        "_queue",
        "_ssl_context",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        *,
        shutdown_event: threading.Event | None = None,
        ssl_context: ssl.SSLContext | None = None,
        lifecycle_collector: LifecycleCollector | None = None,
    ) -> None:
        self._config = config
        self._app = app
        self._ext_shutdown = shutdown_event
        self._ssl_context = ssl_context
        self._lifecycle = lifecycle_collector
        self._lifespan_state: dict[str, Any] = {}
        self._queue: queue.Queue[HandoffRequest] = queue.Queue()
        self._handoff_tasks: set[asyncio.Task[None]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        # Per-pool shutdown, distinct from the shared ``_ext_shutdown`` so a
        # single old pool can be retired on graceful reload (issue #102)
        # without signalling the supervisor-wide shutdown event.
        self._pool_shutdown = threading.Event()
        self._logger = logging.getLogger("pounce.async_pool")

    def set_lifespan_state(self, state: dict[str, Any]) -> None:
        """Set the lifespan state dict shared with all requests."""
        self._lifespan_state = state

    def set_app(self, app: ASGIApp) -> None:
        """Swap the ASGI app used for future handoffs.

        Safe to call while the pool is running: ``self._app`` is read once per
        handoff (in ``_handle_streaming_handoff`` / ``_handle_websocket_handoff``),
        not captured at loop start. Used by graceful reload to point a freshly
        built pool at the reimported app (issue #102).
        """
        self._app = app

    def request_shutdown(self) -> None:
        """Signal this pool's serve loop to stop accepting new handoffs.

        Distinct from the shared ``_ext_shutdown``: retires only this pool so a
        graceful reload can drain the old generation without tearing down the
        whole server (issue #102).
        """
        self._pool_shutdown.set()

    def accept_handoff(self, handoff: HandoffRequest) -> None:
        """Accept a handoff from a SyncWorker (thread-safe)."""
        self._queue.put(handoff)

    def run(self) -> None:
        """Run the event loop until shutdown (blocking)."""
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        """Event loop: process handoffs until shutdown.

        The pool retires on its OWN ``_pool_shutdown`` event, NOT on the shared
        ``_ext_shutdown`` (#104). On a FULL shutdown the supervisor sets
        ``_ext_shutdown`` immediately but only calls ``request_shutdown()``
        (which sets ``_pool_shutdown``) AFTER the sync workers have drained —
        because a draining worker can still be SERVING an in-flight
        streaming/WebSocket request that it hands off to this pool. Retiring on
        ``_ext_shutdown`` would close the pool before that late handoff arrives,
        orphaning the connection (empty response). A safety deadline keyed off
        ``_ext_shutdown`` guarantees the pool still self-retires even if
        ``request_shutdown()`` is never delivered (e.g. a supervisor fault).
        """
        self._loop = asyncio.get_running_loop()

        safety_deadline: float | None = None
        while not self._pool_shutdown.is_set():
            if self._ext_shutdown is not None and self._ext_shutdown.is_set():
                if safety_deadline is None:
                    # 2x the per-pool drain budget: ample for the workers to
                    # finish draining and enqueue their final handoffs.
                    safety_deadline = self._loop.time() + 2 * self._config.shutdown_timeout
                elif self._loop.time() >= safety_deadline:
                    break
            try:
                handoff = self._queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.05)
                continue

            self._start_handoff(handoff)

        # Drain any handoffs that landed just as the pool was retiring, then
        # await every in-flight task. Both bounded by ``shutdown_timeout`` so an
        # unbounded stream cannot pin the pool forever; survivors are cancelled.
        await self._drain_queued_handoffs(self._config.shutdown_timeout)
        await self._drain_handoffs(self._config.shutdown_timeout)

    def _start_handoff(self, handoff: HandoffRequest) -> None:
        """Schedule a handoff as a tracked task."""
        task = asyncio.create_task(self._handle_handoff_async(handoff))
        self._handoff_tasks.add(task)
        task.add_done_callback(self._handoff_tasks.discard)

    async def _drain_queued_handoffs(self, timeout: float) -> None:
        """Pull and start any handoffs that land during the drain window.

        On a FULL shutdown the sync workers retire concurrently and hand off
        their last in-flight streaming/WebSocket requests to this pool. The pool
        may observe shutdown and exit its main loop *before* those handoffs are
        enqueued, so keep polling the queue here until it has stayed empty AND
        no handoff task is in flight for a sustained idle interval (the workers
        have finished enqueuing). Bounded by *timeout* so this can never spin
        forever; in-flight tasks themselves are awaited by ``_drain_handoffs``.
        """
        loop = self._loop
        deadline = (loop.time() + timeout) if loop is not None else timeout
        # Require a sustained idle stretch (queue empty + nothing in flight)
        # before concluding the workers are done enqueuing.
        idle_needed = 6  # 6 * 0.05s = 0.3s of sustained idle
        idle_polls = 0
        while loop is None or loop.time() < deadline:
            try:
                handoff = self._queue.get_nowait()
            except queue.Empty:
                if not any(not t.done() for t in self._handoff_tasks):
                    idle_polls += 1
                    if idle_polls >= idle_needed:
                        return
                else:
                    idle_polls = 0
                await asyncio.sleep(0.05)
                continue
            idle_polls = 0
            self._start_handoff(handoff)

    async def _drain_handoffs(self, timeout: float) -> None:
        """Await in-flight handoff tasks up to *timeout*, then cancel stragglers."""
        pending = [t for t in self._handoff_tasks if not t.done()]
        if not pending:
            return
        self._logger.debug("AsyncPool draining %d in-flight handoff(s)", len(pending))
        _done, still_pending = await asyncio.wait(pending, timeout=timeout)
        for task in still_pending:
            task.cancel()
        if still_pending:
            # return_exceptions=True swallows the CancelledError(s) from the
            # tasks we just cancelled — nothing propagates out of gather().
            await asyncio.gather(*still_pending, return_exceptions=True)

    async def _handle_handoff_async(self, handoff: HandoffRequest) -> None:
        """Handle a handoff (async task)."""
        try:
            if isinstance(handoff, StreamingHandoff):
                await self._handle_streaming_handoff(handoff)
            elif isinstance(handoff, WebSocketHandoff):
                await self._handle_websocket_handoff(handoff)
        except Exception:
            self._logger.exception("Error handling handoff")
            with contextlib.suppress(OSError):
                handoff.conn.close()

    async def _handle_streaming_handoff(self, handoff: StreamingHandoff) -> None:
        """Handle HTTP streaming handoff: wrap socket, run app from scratch."""
        conn = handoff.conn
        scope = handoff.scope
        body = handoff.body

        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.connect_accepted_socket(lambda: protocol, conn)
        except (OSError, ConnectionError):  # fmt: skip
            conn.close()
            return

        writer = asyncio.StreamWriter(transport, protocol, reader, loop)

        proto = _create_h1_protocol(
            max_incomplete_event_size=self._config.h11_max_incomplete_event_size,
        )

        request_method = scope.get("method", "GET").encode()
        raw_headers = scope.get("headers", [])
        request_headers = tuple(
            (
                k.encode() if isinstance(k, str) else k,
                v.encode() if isinstance(v, str) else v,
            )
            for k, v in raw_headers
        )
        raw_path = scope.get("raw_path")
        if raw_path is None:
            raw_path = scope.get("path", "/").encode()
        if isinstance(raw_path, str):
            raw_path = raw_path.encode()
        request = RequestReceived(
            method=request_method,
            target=raw_path,
            headers=request_headers,
            http_version=scope.get("http_version", "1.1"),
        )

        compressor: Compressor | None = None
        if self._config.compression:
            from pounce._compression import negotiate_encoding

            accept_enc = _get_header(request.headers, b"accept-encoding")
            if accept_enc:
                enc = negotiate_encoding(accept_enc)
                if enc:
                    compressor = create_compressor(enc)

        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": body, "more_body": False}

        send_state = SendState()
        request_started_ns = monotonic_ns()
        stream_started_ns = 0

        def record_stream_started() -> None:
            nonlocal stream_started_ns
            if stream_started_ns or self._lifecycle is None:
                return
            stream_started_ns = monotonic_ns()
            self._lifecycle.record(
                StreamOpened(
                    connection_id=handoff.connection_id,
                    worker_id=handoff.worker_id,
                    method=scope.get("method", "unknown"),
                    path=scope.get("path", "/"),
                    timestamp_ns=stream_started_ns,
                )
            )

        send = create_send(
            proto,
            writer,
            send_state,
            compressor=compressor,
            request_method=request_method,
            request_path=raw_path if isinstance(raw_path, bytes) else raw_path.encode(),
            request_id=handoff.request_id,
            config=self._config,
            server=scope.get("server", ("localhost", 0)),
            compression_min_size=self._config.compression_min_size,
            on_stream_start=record_stream_started,
        )

        scope = dict(scope)
        if self._lifespan_state:
            scope["state"] = self._lifespan_state

        try:
            await self._app(scope, receive, send)
        except RequestTimeoutError as exc:
            if exc.code != "POUNCE_TIMEOUT_WRITE":
                raise
        except Exception:
            self._logger.exception("ASGI app error on handoff")
            if not send_state.response_started:
                try:
                    raw = proto.send_response(500, [(b"content-type", b"text/plain")])
                    raw += proto.send_body(b"Internal Server Error", more=False)
                    writer.write(raw)
                    await drain_with_timeout(writer, self._config.write_timeout)
                except (OSError, ConnectionError, RequestTimeoutError):  # fmt: skip
                    pass
        finally:
            if self._lifecycle is not None:
                if stream_started_ns:
                    if self._pool_shutdown.is_set():
                        reason = "drain"
                    elif send_state.response_complete:
                        reason = "complete"
                    else:
                        reason = "error"
                    self._lifecycle.record(
                        StreamClosed(
                            connection_id=handoff.connection_id,
                            worker_id=handoff.worker_id,
                            duration_ms=elapsed_ms(stream_started_ns),
                            reason=reason,
                            timestamp_ns=monotonic_ns(),
                        )
                    )
                self._lifecycle.record(
                    ResponseCompleted(
                        connection_id=handoff.connection_id,
                        worker_id=handoff.worker_id,
                        status=send_state.status or 500,
                        bytes_sent=send_state.bytes_sent,
                        duration_ms=elapsed_ms(request_started_ns),
                        timestamp_ns=monotonic_ns(),
                        method=scope.get("method", "unknown"),
                        streaming=send_state.streaming,
                    )
                )
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ConnectionError):  # fmt: skip
                pass

    async def _handle_websocket_handoff(self, handoff: WebSocketHandoff) -> None:
        """Handle WebSocket handoff: wrap socket, run handle_websocket."""
        from pounce._ws_handler import handle_websocket

        conn = handoff.conn
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        loop = asyncio.get_running_loop()
        try:
            transport, _ = await loop.connect_accepted_socket(lambda: protocol, conn)
        except (OSError, ConnectionError):  # fmt: skip
            conn.close()
            return

        writer = asyncio.StreamWriter(transport, protocol, reader, loop)
        client_str = f"{handoff.client[0]}:{handoff.client[1]}"

        try:
            await handle_websocket(
                self._app,
                self._config,
                self._logger,
                handoff.request,
                reader,
                writer,
                handoff.client,
                handoff.server,
                client_str,
                lifespan_state=self._lifespan_state,
            )
        except Exception:
            self._logger.exception("WebSocket handoff error")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, ConnectionError):  # fmt: skip
                pass
