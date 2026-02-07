"""
HTTP/2 ASGI bridge — maps H2Connection streams to ASGI scope/receive/send.

Each HTTP/2 stream maps to one ASGI invocation, just like one HTTP/1.1
request maps to one ASGI invocation. The difference is that multiple
streams can run concurrently on the same TCP connection.

The worker creates one ``H2StreamBridge`` per stream, which builds the
scope and creates receive/send callables. The send callable serializes
via the shared ``H2Connection`` (which is single-threaded within the
worker's event loop — no lock needed).

"""

from __future__ import annotations

import asyncio
from typing import Any

from pounce._compression import Compressor
from pounce._timing import ServerTiming
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived


def build_h2_scope(
    request: RequestReceived,
    config: ServerConfig,
    client: tuple[str, int],
    server: tuple[str, int],
) -> dict[str, Any]:
    """Build an ASGI HTTP scope from an HTTP/2 request.

    Same as ``build_scope()`` for H1, but sets ``http_version: "2"``
    and ``scheme: "https"`` (HTTP/2 typically requires TLS).

    """
    from urllib.parse import unquote

    target = request.target.decode("ascii", errors="replace")

    if "?" in target:
        path, _, query_string = target.partition("?")
    else:
        path = target
        query_string = ""

    path = unquote(path)
    headers: list[list[bytes]] = [[name, value] for name, value in request.headers]

    scheme = "https" if config.ssl_certfile else "http"

    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "2",
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


def create_h2_receive(
    body_queue: asyncio.Queue[dict[str, Any]],
) -> Any:
    """Create an ASGI receive callable for an HTTP/2 stream.

    The worker pushes body events into the queue as DATA frames arrive.

    """

    async def receive() -> dict[str, Any]:
        return await body_queue.get()

    return receive


def create_h2_send(
    h2_conn: Any,  # H2Connection — Any to avoid import cycle
    stream_id: int,
    writer: asyncio.StreamWriter,
    *,
    timing: ServerTiming | None = None,
    compressor: Compressor | None = None,
) -> Any:
    """Create an ASGI send callable for an HTTP/2 stream.

    Serializes via the shared H2Connection. After each h2 operation,
    flushes ``data_to_send()`` to the writer.

    Args:
        h2_conn: The H2Connection managing this connection.
        stream_id: The h2 stream identifier for this request.
        writer: The asyncio StreamWriter for the TCP connection.
        timing: Optional Server-Timing builder.
        compressor: Optional compressor for response body.

    """
    response_started = False
    response_complete = False

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_started, response_complete

        if message["type"] == "http.response.start":
            status: int = message["status"]
            headers: list[tuple[bytes, bytes]] = [
                (
                    name if isinstance(name, bytes) else name.encode(),
                    value if isinstance(value, bytes) else value.encode(),
                )
                for name, value in message.get("headers", [])
            ]

            # 103 Early Hints — informational response (RFC 8297)
            # Can be sent multiple times before the final response
            if status == 103:
                h2_conn.send_response_headers(stream_id, 103, headers)
                _flush(h2_conn, writer)
                return  # Don't mark response_started yet

            response_started = True

            # Inject Content-Encoding if compressing
            if compressor is not None:
                headers.append(
                    (b"content-encoding", compressor.encoding.encode("ascii"))
                )
                headers = [
                    (n, v) for n, v in headers if n.lower() != b"content-length"
                ]

            # Inject Server-Timing header
            if timing is not None:
                rendered = timing.render_bytes()
                if rendered:
                    headers.append((b"server-timing", rendered))

            h2_conn.send_response_headers(stream_id, status, headers)
            _flush(h2_conn, writer)

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

            end_stream = not more_body
            h2_conn.send_data(stream_id, body, end_stream=end_stream)
            _flush(h2_conn, writer)

            if not more_body:
                response_complete = True

    return send


def _flush(h2_conn: Any, writer: asyncio.StreamWriter) -> None:
    """Write pending h2 output bytes to the transport."""
    data = h2_conn.data_to_send()
    if data:
        writer.write(data)
