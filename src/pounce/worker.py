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
import logging
import socket
import ssl
import threading

from pounce._compression import Compressor, create_compressor, negotiate_encoding
from pounce._errors import ParseError
from pounce._h2_handler import handle_h2_connection
from pounce._timing import ServerTiming, elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce._ws_handler import handle_websocket
from pounce.asgi.bridge import build_scope, create_empty_receive, create_receive, create_send
from pounce.config import ServerConfig
from pounce.logging import access_log
from pounce.protocols._base import (
    BodyReceived,
    ConnectionClosed,
    RequestReceived,
)
from pounce.protocols.h1 import H1Protocol

# Auto-detect httptools for C-accelerated HTTP/1.1 parsing.
# Falls back to h11 (pure Python) when httptools is not installed.
try:
    from pounce.protocols.h1_httptools import is_httptools_available

    _use_httptools = is_httptools_available()
except ImportError:
    _use_httptools = False


def _create_h1_protocol(
    *, max_incomplete_event_size: int | None = None,
) -> H1Protocol:
    """Create the best available HTTP/1.1 protocol handler.

    Uses httptools when installed (``pip install pounce[fast]``),
    falls back to h11 (pure Python) otherwise.

    """
    if _use_httptools:
        from pounce.protocols.h1_httptools import H1HttpToolsProtocol

        return H1HttpToolsProtocol(  # type: ignore[return-value]
            max_incomplete_event_size=max_incomplete_event_size,
        )
    return H1Protocol(max_incomplete_event_size=max_incomplete_event_size)


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
        "_config",
        "_app",
        "_sock",
        "_worker_id",
        "_ext_shutdown",
        "_async_shutdown",
        "_loop",
        "_active_connections",
        "_max_connections",
        "_ssl_context",
        "_logger",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        sock: socket.socket,
        *,
        worker_id: int = 0,
        shutdown_event: threading.Event | None = None,
        max_connections: int = 0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._config = config
        self._app = app
        self._sock = sock
        self._worker_id = worker_id
        self._ext_shutdown = shutdown_event
        self._async_shutdown: asyncio.Event | None = None  # created inside event loop
        self._loop: asyncio.AbstractEventLoop | None = None  # set in _serve
        self._active_connections = 0
        self._max_connections = max_connections
        self._ssl_context = ssl_context
        self._logger = logging.getLogger(f"pounce.worker.{worker_id}")

    def run(self) -> None:
        """Start the worker's event loop (blocking)."""
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        """Accept connections until shutdown is signaled."""
        self._loop = asyncio.get_running_loop()
        self._async_shutdown = asyncio.Event()

        server = await asyncio.start_server(
            self._handle_connection,
            sock=self._sock,
            ssl=self._ssl_context,
        )

        self._logger.info("Worker %d started, accepting connections", self._worker_id)

        # If an external threading.Event was provided (multi-worker mode),
        # bridge it into the asyncio event loop so the supervisor can
        # trigger shutdown from its own thread.
        bridge_task: asyncio.Task[None] | None = None
        if self._ext_shutdown is not None:
            bridge_task = asyncio.create_task(
                self._bridge_shutdown(self._ext_shutdown)
            )

        try:
            await self._async_shutdown.wait()
        finally:
            if bridge_task is not None:
                bridge_task.cancel()
            # Guard against shared-fd sockets: on macOS all workers share
            # the same socket fd.  When the first worker closes the asyncio
            # server it unregisters the fd from the selector.  The second
            # worker then tries to unregister the same (now-invalid) fd,
            # raising ``ValueError: Invalid file descriptor: -1``.
            try:
                server.close()
                await server.wait_closed()
            except (ValueError, OSError):
                pass  # fd already closed by another worker sharing the socket
            self._logger.info("Worker %d stopped", self._worker_id)

    async def _bridge_shutdown(self, ext_event: threading.Event) -> None:
        """Poll an external ``threading.Event`` and set the async shutdown.

        Runs as a background task inside the worker's event loop.  Checks
        the threading event every 0.25 s — fast enough for responsive
        shutdown without measurable overhead.

        """
        loop = asyncio.get_running_loop()
        while not ext_event.is_set():
            await asyncio.sleep(0.25)
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
        # Connection backpressure — reject when at capacity
        if self._max_connections > 0 and self._active_connections >= self._max_connections:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return

        self._active_connections += 1
        peername = writer.get_extra_info("peername")
        client = (peername[0], peername[1]) if peername else ("unknown", 0)
        client_str = f"{client[0]}:{client[1]}"
        server_addr = writer.get_extra_info("sockname")
        server = (server_addr[0], server_addr[1]) if server_addr else (self._config.host, self._config.port)

        # Check ALPN negotiation result (only present on TLS connections)
        ssl_object = writer.get_extra_info("ssl_object")
        if ssl_object is not None:
            alpn = ssl_object.selected_alpn_protocol()
            if alpn == "h2":
                try:
                    await handle_h2_connection(
                        self._app, self._config, self._logger,
                        reader, writer, client, server, client_str,
                    )
                except Exception:
                    self._logger.exception(
                        "Unhandled error on H2 connection from %s", client_str,
                    )
                finally:
                    self._active_connections -= 1
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass
                return

        proto = _create_h1_protocol(
            max_incomplete_event_size=self._config.h11_max_incomplete_event_size,
        )

        max_requests = self._config.max_requests_per_connection
        request_count = 0

        try:
            # Reusable helper: process a batch of events from the protocol
            # layer. Returns True if the connection should close.
            async def _process_events(events: list) -> bool:
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
                                self._app, self._config, self._logger,
                                event, reader, writer, client, server, client_str,
                            )
                            return True  # WS takes over
                        await self._handle_request(
                            event, proto, reader, writer, client, server, client_str,
                            initial_body=initial_body,
                        )
                    elif isinstance(event, ConnectionClosed):
                        return True  # Clean close
                return False

            while True:
                # Read data from the client
                try:
                    data = await asyncio.wait_for(
                        reader.read(65536),
                        timeout=self._config.keep_alive_timeout,
                    )
                except asyncio.TimeoutError:
                    break  # Keep-alive timeout — close connection
                except (ConnectionError, OSError):
                    break

                if not data:
                    break  # Client disconnected

                # Parse through the protocol layer
                try:
                    events = proto.receive_data(data)
                except ParseError as exc:
                    await self._send_error(writer, proto, 400, str(exc))
                    break

                if await _process_events(events):
                    return

                # Enforce max requests per connection
                if max_requests > 0 and request_count >= max_requests:
                    break  # Limit reached — close connection

                # Check if we can do another cycle (keep-alive)
                try:
                    proto.start_new_cycle()
                except Exception:
                    break  # Connection can't be reused

                # NOTE: HTTP pipelining (next request buffered in h11
                # before we call reader.read()) is intentionally not
                # optimised here.  Pipelining is rarely used by modern
                # clients.  h11 preserves its buffer across cycles, so
                # the next reader.read() + receive_data() will flush it.

        except Exception:
            self._logger.exception("Unhandled error on connection from %s", client_str)
        finally:
            self._active_connections -= 1
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

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
    ) -> None:
        """Process a single HTTP request through the ASGI pipeline."""
        request_start = monotonic_ns()

        # Build ASGI scope
        scope = build_scope(request, self._config, client, server)

        # Set up timing if enabled
        timing: ServerTiming | None = None
        if self._config.server_timing:
            timing = ServerTiming()
            timing.add("parse", elapsed_ms(request_start))

        # Single-pass header lookup — scan once instead of per-header
        compressor: Compressor | None = None
        if self._config.compression:
            accept_encoding = _get_header_from_tuple(
                request.headers, b"accept-encoding",
            )
            if accept_encoding:
                encoding = negotiate_encoding(accept_encoding)
                if encoding:
                    compressor = create_compressor(encoding)

        # Determine body status and create receive callable.
        # For bodyless requests (most GET/HEAD), use the fast-path
        # receive that returns a static message — no asyncio.Queue.
        body_complete = False
        body_queue: asyncio.Queue[BodyReceived] | None = None

        if initial_body:
            # Body events arrived with the request head
            body_queue = asyncio.Queue()
            for body_event in initial_body:
                await body_queue.put(body_event)
                if not body_event.more:
                    body_complete = True
            receive = create_receive(body_queue)
        else:
            # No body events — bodyless request (GET/HEAD).
            # Use the fast-path: no Queue, static message.
            receive = create_empty_receive()
            body_complete = True

        app_start = monotonic_ns()
        send = create_send(proto, writer, timing=timing, compressor=compressor)

        # Call the ASGI app
        status = 500
        bytes_sent = 0

        if body_complete:
            # Fast path: entire body (or no body) already available.
            # No concurrent reading needed — just run the app.
            try:
                await self._app(scope, receive, send)
                status = 200
            except Exception:
                self._logger.exception(
                    "ASGI app error on %s %s", scope["method"], scope["path"]
                )
                try:
                    await self._send_error(writer, proto, 500, "Internal Server Error")
                except Exception:
                    pass
                status = 500
        else:
            # Body still arriving — read concurrently with the app.
            # Same pattern as WebSocket: two tasks, wait for first to finish.
            status = await self._run_with_body_reader(
                scope, receive, send, body_queue, proto, reader, writer,
            )

        if timing:
            timing.add("app", elapsed_ms(app_start))

        # Flush the writer
        try:
            await writer.drain()
        except (ConnectionError, OSError):
            pass

        # Access log
        if self._config.access_log:
            duration = elapsed_ms(request_start)
            target = request.target.decode("ascii", errors="replace")
            method = request.method.decode("ascii", errors="replace")
            access_log(method, target, status, bytes_sent, duration, client_str)

    async def _run_with_body_reader(
        self,
        scope: dict,
        receive: object,
        send: object,
        body_queue: asyncio.Queue[BodyReceived],
        proto: H1Protocol,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> int:
        """Run the ASGI app while concurrently reading request body.

        Used when the request body spans multiple socket reads (large POSTs,
        chunked uploads). Follows the same concurrent-task pattern as the
        WebSocket handler.

        Returns:
            HTTP status code (200 on success, 500 on error).

        """

        async def _read_body() -> None:
            """Read remaining body data from the connection into the queue."""
            try:
                while True:
                    try:
                        data = await asyncio.wait_for(
                            reader.read(65536),
                            timeout=self._config.request_timeout,
                        )
                    except asyncio.TimeoutError:
                        await body_queue.put(BodyReceived(data=b"", more=False))
                        return
                    except (ConnectionError, OSError):
                        await body_queue.put(BodyReceived(data=b"", more=False))
                        return

                    if not data:
                        await body_queue.put(BodyReceived(data=b"", more=False))
                        return

                    try:
                        events = proto.receive_data(data)
                    except ParseError:
                        await body_queue.put(BodyReceived(data=b"", more=False))
                        return

                    for evt in events:
                        if isinstance(evt, BodyReceived):
                            await body_queue.put(evt)
                            if not evt.more:
                                return
            except asyncio.CancelledError:
                # Ensure the app unblocks if cancelled
                await body_queue.put(BodyReceived(data=b"", more=False))
                raise

        async def _run_app() -> int:
            try:
                await self._app(scope, receive, send)
                return 200
            except Exception:
                self._logger.exception(
                    "ASGI app error on %s %s", scope["method"], scope["path"]
                )
                try:
                    await self._send_error(writer, proto, 500, "Internal Server Error")
                except Exception:
                    pass
                return 500

        app_task = asyncio.create_task(_run_app())
        reader_task = asyncio.create_task(_read_body())

        status = 500
        try:
            done, pending = await asyncio.wait(
                {app_task, reader_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Extract status from the app task
            if app_task in done:
                try:
                    status = app_task.result()
                except Exception:
                    status = 500
            else:
                # Reader finished first (body complete), wait for app
                try:
                    status = await app_task
                except Exception:
                    status = 500
        except Exception:
            self._logger.exception(
                "Unhandled error during body reading for %s",
                scope.get("path", "?"),
            )
            status = 500

        return status

    # ------------------------------------------------------------------
    # Error responses
    # ------------------------------------------------------------------

    async def _send_error(
        self,
        writer: asyncio.StreamWriter,
        proto: H1Protocol,
        status: int,
        message: str,
    ) -> None:
        """Send a plain-text error response."""
        body = message.encode("utf-8")
        try:
            raw = proto.send_response(
                status,
                [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"connection", b"close"),
                ],
            )
            writer.write(raw)
            writer.write(proto.send_body(body, more=False))
            await writer.drain()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _is_websocket_upgrade(request: RequestReceived) -> bool:
    """Check if the request is a WebSocket upgrade.

    Detects ``Connection: Upgrade`` + ``Upgrade: websocket`` headers.

    """
    has_upgrade_connection = False
    has_websocket_upgrade = False

    for name, value in request.headers:
        name_lower = name.lower()
        if name_lower == b"connection":
            has_upgrade_connection = b"upgrade" in value.lower()
        elif name_lower == b"upgrade":
            has_websocket_upgrade = value.lower() == b"websocket"

    return has_upgrade_connection and has_websocket_upgrade

def _get_header_from_tuple(
    headers: tuple[tuple[bytes, bytes], ...], name: bytes,
) -> bytes | None:
    """Get a header value by lowercase name from a headers tuple.

    Single linear scan — use when only one header is needed.  For
    multiple lookups, build a dict with ``_headers_to_dict``.

    """
    name_lower = name.lower()
    for header_name, header_value in headers:
        if header_name.lower() == name_lower:
            return header_value
    return None
