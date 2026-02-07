"""
Worker — the heart of pounce's request handling.

Runs a single asyncio event loop that accepts connections on a socket and
processes HTTP requests through the protocol → bridge → ASGI pipeline.

Phase 1 is single-threaded (one worker per server). Phase 2 adds the
supervisor with multiple worker threads.

Connection flow:
    socket.accept() → H1Protocol.receive_data() → build_scope()
    → negotiate_compression() → app(scope, receive, send) → access_log()

"""

from __future__ import annotations

import asyncio
import logging
import socket

from pounce._compression import Compressor, create_compressor, negotiate_encoding
from pounce._errors import AppError, ParseError, PounceError
from pounce._timing import ServerTiming, elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.bridge import build_scope, create_receive, create_send
from pounce.config import ServerConfig
from pounce.logging import access_log
from pounce.protocols._base import BodyReceived, ConnectionClosed, RequestReceived
from pounce.protocols.h1 import H1Protocol

logger = logging.getLogger("pounce.worker")


class Worker:
    """Single-threaded async worker that serves HTTP requests.

    Accepts connections from the provided socket and handles them using
    asyncio streams. Each connection is processed in its own task.

    Args:
        config: Server configuration.
        app: The ASGI application.
        sock: A bound, listening socket.

    """

    __slots__ = ("_config", "_app", "_sock", "_shutdown_event", "_active_connections")

    def __init__(
        self, config: ServerConfig, app: ASGIApp, sock: socket.socket
    ) -> None:
        self._config = config
        self._app = app
        self._sock = sock
        self._shutdown_event = asyncio.Event()
        self._active_connections = 0

    def run(self) -> None:
        """Start the worker's event loop (blocking)."""
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        """Accept connections until shutdown is signaled."""
        server = await asyncio.start_server(
            self._handle_connection,
            sock=self._sock,
        )

        logger.info("Worker started, accepting connections")

        try:
            # Wait for shutdown signal
            await self._shutdown_event.wait()
        finally:
            server.close()
            await server.wait_closed()
            logger.info("Worker stopped")

    def shutdown(self) -> None:
        """Signal the worker to stop accepting connections."""
        self._shutdown_event.set()

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
            logger.exception("Unhandled error on connection from %s", client_str)
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
            logger.exception("ASGI app error on %s %s", scope["method"], scope["path"])
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


def _get_header(
    headers: tuple[tuple[bytes, bytes], ...], name: bytes
) -> bytes | None:
    """Get a header value by lowercase name."""
    name_lower = name.lower()
    for header_name, header_value in headers:
        if header_name.lower() == name_lower:
            return header_value
    return None
