"""
Worker — the heart of pounce's request handling.

Runs a single asyncio event loop that accepts connections on a socket and
processes requests through the protocol → bridge → ASGI pipeline.

Each worker is self-contained: it owns its event loop, its set of active
connections, and its per-worker metrics.  The supervisor spawns workers as
threads (nogil) or processes (GIL) — the worker does not know which.

Connection flow (HTTP):
    socket.accept() → H1Protocol.receive_data() → build_scope()
    → negotiate_compression() → app(scope, receive, send) → access_log()

Connection flow (WebSocket):
    socket.accept() → H1Protocol detects upgrade → build_ws_scope()
    → send 101 → WSProtocol frames → app(scope, receive, send)

"""

from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import threading

from pounce._compression import Compressor, create_compressor, negotiate_encoding
from pounce._errors import ParseError
from pounce._timing import ServerTiming, elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.bridge import build_scope, create_receive, create_send
from pounce.asgi.ws_bridge import build_ws_scope, create_ws_receive, create_ws_send
from pounce.config import ServerConfig
from pounce.logging import access_log
from pounce.protocols._base import (
    BodyReceived,
    ConnectionClosed,
    RequestReceived,
    WebSocketDataReceived,
    WebSocketDisconnected,
)
from pounce.protocols.h1 import H1Protocol


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

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single TCP connection through request-response cycles.

        Supports keep-alive: loops until the client disconnects or an error
        occurs. Each request goes through the full pipeline:
        parse → scope → compress → ASGI app → response → log.

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

        proto = H1Protocol(
            max_incomplete_event_size=self._config.h11_max_incomplete_event_size,
        )

        try:
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

                for event in events:
                    if isinstance(event, RequestReceived):
                        # Check for WebSocket upgrade
                        if _is_websocket_upgrade(event):
                            await self._handle_websocket(
                                event, reader, writer, client, server, client_str,
                            )
                            return  # WS takes over the connection
                        await self._handle_request(
                            event, proto, reader, writer, client, server, client_str,
                        )
                    elif isinstance(event, ConnectionClosed):
                        return  # Clean close

                # Check if we can do another cycle (keep-alive)
                try:
                    proto.start_new_cycle()
                except Exception:
                    break  # Connection can't be reused

        except Exception:
            self._logger.exception("Unhandled error on connection from %s", client_str)
        finally:
            self._active_connections -= 1
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_request(
        self,
        request: RequestReceived,
        proto: H1Protocol,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        client: tuple[str, int],
        server: tuple[str, int],
        client_str: str,
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

        # Negotiate compression
        compressor: Compressor | None = None
        if self._config.compression:
            accept_encoding = _get_header(request.headers, b"accept-encoding")
            if accept_encoding:
                encoding = negotiate_encoding(accept_encoding)
                if encoding:
                    compressor = create_compressor(encoding)

        # Create ASGI bridge callables
        body_queue: asyncio.Queue[BodyReceived] = asyncio.Queue()
        receive = create_receive(body_queue)

        # Push the initial body event (for requests with no body, this signals completion)
        # For GET/HEAD, the EndOfMessage was already parsed by h11
        # We need to collect any remaining body events
        await body_queue.put(BodyReceived(data=b"", more=False))

        app_start = monotonic_ns()
        send = create_send(proto, writer, timing=timing, compressor=compressor)

        # Call the ASGI app
        status = 500
        bytes_sent = 0
        try:
            await self._app(scope, receive, send)
            status = 200  # Default if we can't determine from send
        except Exception as exc:
            self._logger.exception(
                "ASGI app error on %s %s", scope["method"], scope["path"]
            )
            try:
                await self._send_error(writer, proto, 500, "Internal Server Error")
            except Exception:
                pass
            status = 500

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

    async def _handle_websocket(
        self,
        request: RequestReceived,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        client: tuple[str, int],
        server: tuple[str, int],
        client_str: str,
    ) -> None:
        """Handle a WebSocket connection after upgrade detection.

        Lifecycle:
        1. Build ASGI ``websocket`` scope
        2. Push ``websocket.connect`` to the receive queue
        3. Run the ASGI app — it sends ``websocket.accept`` (or close)
        4. Read WebSocket frames and feed to receive queue
        5. App sends ``websocket.send`` / ``websocket.close``
        6. Clean up when either side disconnects

        """
        from pounce.protocols.ws import WSProtocol, is_wsproto_available

        if not is_wsproto_available():
            self._logger.warning(
                "WebSocket upgrade requested but wsproto not installed"
            )
            return

        request_start = monotonic_ns()

        # Build WebSocket ASGI scope
        scope = build_ws_scope(request, self._config, client, server)

        # Extract Sec-WebSocket-Key for the 101 handshake
        ws_key = _get_header(request.headers, b"sec-websocket-key")
        if not ws_key:
            self._logger.warning("WebSocket upgrade missing Sec-WebSocket-Key")
            return

        # Create protocol and ASGI bridge
        ws_proto = WSProtocol()
        accept_event = asyncio.Event()
        close_event = asyncio.Event()

        receive_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        receive = create_ws_receive(receive_queue)
        send = create_ws_send(
            writer, ws_proto, ws_key,
            accept_event=accept_event,
            close_event=close_event,
        )

        # Push the initial connect event
        await receive_queue.put({"type": "websocket.connect"})

        # Run the ASGI app and the frame reader concurrently
        async def _run_app() -> None:
            try:
                await self._app(scope, receive, send)
            except Exception:
                self._logger.exception(
                    "ASGI app error on WebSocket %s", scope["path"]
                )

        async def _read_frames() -> None:
            """Read WebSocket frames from the client and push to queue."""
            # Wait for the app to accept before reading frames
            await accept_event.wait()

            try:
                while not close_event.is_set():
                    try:
                        data = await asyncio.wait_for(
                            reader.read(65536),
                            timeout=self._config.keep_alive_timeout,
                        )
                    except asyncio.TimeoutError:
                        break
                    except (ConnectionError, OSError):
                        break

                    if not data:
                        break

                    events = ws_proto.receive_data(data)
                    for event in events:
                        if isinstance(event, WebSocketDataReceived):
                            if isinstance(event.data, str):
                                await receive_queue.put({
                                    "type": "websocket.receive",
                                    "text": event.data,
                                })
                            else:
                                await receive_queue.put({
                                    "type": "websocket.receive",
                                    "bytes": event.data,
                                })
                        elif isinstance(event, WebSocketDisconnected):
                            await receive_queue.put({
                                "type": "websocket.disconnect",
                                "code": event.code,
                            })
                            return
            finally:
                # Ensure the app unblocks if still waiting on receive
                if not close_event.is_set():
                    await receive_queue.put({
                        "type": "websocket.disconnect",
                        "code": 1006,
                    })

        app_task = asyncio.create_task(_run_app())
        reader_task = asyncio.create_task(_read_frames())

        try:
            # Wait for either the app or the reader to finish
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
        except Exception:
            self._logger.exception(
                "Unhandled error on WebSocket from %s", client_str
            )

        # Access log
        if self._config.access_log:
            duration = elapsed_ms(request_start)
            target = request.target.decode("ascii", errors="replace")
            access_log("WS", target, 101, 0, duration, client_str)

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


def _get_header(
    headers: tuple[tuple[bytes, bytes], ...], name: bytes
) -> bytes | None:
    """Get a header value by lowercase name."""
    name_lower = name.lower()
    for header_name, header_value in headers:
        if header_name.lower() == name_lower:
            return header_value
    return None
