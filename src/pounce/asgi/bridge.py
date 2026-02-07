"""
ASGI bridge — translates between protocol events and the ASGI interface.

Builds ASGI scope dicts from protocol events, and creates the async
receive/send callables that ASGI apps interact with.

Streaming-first: send() writes response chunks immediately to the
transport. No buffering — each http.response.body message is compressed
(if applicable) and flushed to the wire before the next one.

"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import unquote

from pounce._compression import Compressor
from pounce._timing import ServerTiming
from pounce.config import ServerConfig
from pounce.protocols._base import BodyReceived, ProtocolHandler, RequestReceived


def build_scope(
    request: RequestReceived,
    config: ServerConfig,
    client: tuple[str, int],
    server: tuple[str, int],
) -> dict[str, Any]:
    """Build an ASGI HTTP scope dict from a parsed request.

    Args:
        request: The parsed HTTP request head.
        config: Server configuration.
        client: Client (host, port) tuple.
        server: Server (host, port) tuple.

    Returns:
        ASGI scope dict ready to pass to an ASGI app.

    """
    target = request.target.decode("ascii", errors="replace")

    # Split target into path and query string
    if "?" in target:
        path, _, query_string = target.partition("?")
    else:
        path = target
        query_string = ""

    # Decode percent-encoded path
    path = unquote(path)

    # Build headers as list of [name, value] pairs (ASGI expects bytes)
    headers: list[list[bytes]] = [[name, value] for name, value in request.headers]

    scheme = "https" if config.ssl_certfile else "http"

    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": request.http_version,
        "method": request.method.decode("ascii"),
        "path": path,
        "raw_path": request.target.split(b"?")[0],
        "query_string": query_string.encode("ascii"),
        "root_path": config.root_path,
        "scheme": scheme,
        "server": server,
        "client": client,
        "headers": headers,
    }


def create_receive(
    body_events: asyncio.Queue[BodyReceived],
) -> Any:
    """Create an ASGI receive callable from a body event queue.

    The worker pushes BodyReceived events into the queue as they arrive.
    The ASGI app calls receive() to consume them as http.request messages.

    Args:
        body_events: Queue of body events from the protocol layer.

    Returns:
        Async callable conforming to the ASGI Receive protocol.

    """

    async def receive() -> dict[str, Any]:
        event = await body_events.get()
        return {
            "type": "http.request",
            "body": event.data,
            "more_body": event.more,
        }

    return receive


def create_send(
    protocol: ProtocolHandler,
    transport: asyncio.WriteTransport | asyncio.StreamWriter,
    *,
    timing: ServerTiming | None = None,
    compressor: Compressor | None = None,
) -> Any:
    """Create an ASGI send callable that streams to the transport.

    Streaming-first: each response.body chunk is written immediately.
    No buffering — the client sees data as soon as the app produces it.

    Args:
        protocol: Protocol handler for serialization.
        transport: Asyncio write transport for the connection.
        timing: Optional Server-Timing header builder.
        compressor: Optional content compressor for the response.

    Returns:
        Async callable conforming to the ASGI Send protocol.

    """
    response_started = False
    response_complete = False

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_started, response_complete

        if message["type"] == "http.response.start":
            response_started = True

            status: int = message["status"]
            headers: list[tuple[bytes, bytes]] = [
                (name if isinstance(name, bytes) else name.encode(),
                 value if isinstance(value, bytes) else value.encode())
                for name, value in message.get("headers", [])
            ]

            # Inject Content-Encoding if compressing
            if compressor is not None:
                headers.append(
                    (b"content-encoding", compressor.encoding.encode("ascii"))
                )
                # Remove content-length since compressed size differs
                headers = [
                    (n, v) for n, v in headers if n.lower() != b"content-length"
                ]
                # Use chunked transfer encoding
                if not any(n.lower() == b"transfer-encoding" for n, _ in headers):
                    headers.append((b"transfer-encoding", b"chunked"))

            # Inject Server-Timing header
            if timing is not None:
                rendered = timing.render_bytes()
                if rendered:
                    headers.append((b"server-timing", rendered))

            raw = protocol.send_response(status, headers)
            _write(transport, raw)

        elif message["type"] == "http.response.body":
            if not response_started:
                raise RuntimeError(
                    "Received http.response.body before http.response.start"
                )
            if response_complete:
                raise RuntimeError(
                    "Received http.response.body after response is complete"
                )

            body: bytes = message.get("body", b"")
            more_body: bool = message.get("more_body", False)

            if compressor is not None and body:
                body = compressor.compress(body)
                if not more_body:
                    body += compressor.flush()
            elif compressor is not None and not more_body:
                body = compressor.flush()

            raw = protocol.send_body(body, more=more_body)
            _write(transport, raw)

            if not more_body:
                response_complete = True

    return send


def _write(
    transport: asyncio.WriteTransport | asyncio.StreamWriter,
    data: bytes,
) -> None:
    """Write bytes to the transport, handling both transport types."""
    if isinstance(transport, asyncio.StreamWriter):
        transport.write(data)
    else:
        transport.write(data)
