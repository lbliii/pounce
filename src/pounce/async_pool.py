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
from pounce._types import ASGIApp
from pounce.asgi.bridge import SendState, create_send
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived
from pounce.protocols.h1 import H1Protocol


def _create_h1_protocol(
    *,
    max_incomplete_event_size: int | None = None,
) -> H1Protocol:
    """Create an HTTP/1.1 protocol handler."""
    return H1Protocol(max_incomplete_event_size=max_incomplete_event_size)


def _get_header(headers: tuple[tuple[bytes, bytes], ...], name: bytes) -> bytes | None:
    """Get a header value by lowercase name."""
    name_lower = name.lower()
    for hname, hvalue in headers:
        if hname.lower() == name_lower:
            return hvalue
    return None


@dataclass(slots=True)
class StreamingHandoff:
    """Handoff for HTTP streaming response (more_body=True)."""

    conn: socket.socket
    scope: dict[str, Any]
    body: bytes
    request_id: str | None


@dataclass(slots=True)
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
        lifecycle_collector: Any = None,
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
        self._logger = logging.getLogger("pounce.async_pool")

    def set_lifespan_state(self, state: dict[str, Any]) -> None:
        """Set the lifespan state dict shared with all requests."""
        self._lifespan_state = state

    def accept_handoff(self, handoff: HandoffRequest) -> None:
        """Accept a handoff from a SyncWorker (thread-safe)."""
        self._queue.put(handoff)

    def run(self) -> None:
        """Run the event loop until shutdown (blocking)."""
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        """Event loop: process handoffs until shutdown."""
        self._loop = asyncio.get_running_loop()

        while not (self._ext_shutdown and self._ext_shutdown.is_set()):
            try:
                handoff = self._queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.25)
                continue

            task = asyncio.create_task(self._handle_handoff_async(handoff))
            self._handoff_tasks.add(task)
            task.add_done_callback(self._handoff_tasks.discard)

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
        except OSError, ConnectionError:
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
        send = create_send(
            proto,
            writer,
            send_state,
            compressor=compressor,
            request_method=request_method,
            request_id=handoff.request_id,
            config=self._config,
            server=scope.get("server", ("localhost", 0)),
        )

        scope = dict(scope)
        if self._lifespan_state:
            scope["state"] = self._lifespan_state

        try:
            await self._app(scope, receive, send)
        except Exception:
            self._logger.exception("ASGI app error on handoff")
            if not send_state.response_started:
                try:
                    raw = proto.send_response(500, [(b"content-type", b"text/plain")])
                    raw += proto.send_body(b"Internal Server Error", more=False)
                    writer.write(raw)
                    await writer.drain()
                except OSError, ConnectionError:
                    pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError, ConnectionError:
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
        except OSError, ConnectionError:
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
            )
        except Exception:
            self._logger.exception("WebSocket handoff error")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except OSError, ConnectionError:
                pass
