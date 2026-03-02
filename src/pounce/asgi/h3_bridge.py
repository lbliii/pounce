"""
HTTP/3 ASGI bridge — maps aioquic H3 events to ASGI scope/receive/send.

Each HTTP/3 stream maps to one ASGI invocation, similar to HTTP/2.
HTTP/3 uses QUIC (UDP) transport with TLS 1.3 built-in.

"""

import asyncio
from typing import TYPE_CHECKING, Any

from pounce._compression import Compressor
from pounce._timing import ServerTiming
from pounce._types import Receive, Send
from pounce.asgi.bridge import SendState, _sanitize_headers
from pounce.config import ServerConfig

if TYPE_CHECKING:
    from aioquic.h3.connection import H3Connection


def build_h3_scope(
    headers: list[tuple[bytes, bytes]],
    config: ServerConfig,
    client: tuple[str, int],
    server: tuple[str, int],
    *,
    stream_id: int = 0,
    is_0rtt: bool = False,
) -> dict[str, Any]:
    """Build an ASGI HTTP scope from HTTP/3 HeadersReceived.

    Parses pseudo-headers (:method, :path, :scheme, :authority) and
    builds the scope. HTTP/3 requires TLS, so scheme is always https.

    Args:
        headers: HTTP/3 header list from aioquic HeadersReceived.
        config: Server configuration.
        client: Client (host, port) from QUIC connection.
        server: Server (host, port) tuple.
        stream_id: HTTP/3 stream ID (for extensions).
        is_0rtt: True if request arrived via 0-RTT (replay risk).

    """
    from urllib.parse import unquote

    from pounce._proxy import apply_proxy_headers

    method = "GET"
    path = "/"
    scheme = "https"  # QUIC mandates TLS
    header_list: list[tuple[bytes, bytes]] = []

    for name, value in headers:
        name_lower = name.lower()
        if name_lower == b":method":
            method = value.decode("ascii")
        elif name_lower == b":path":
            path = value.decode("ascii")
        elif name_lower == b":scheme":
            scheme = value.decode("ascii")
        elif name_lower == b":authority":
            value.decode("ascii")
        else:
            header_list.append((name_lower, value))

    # Parse path and query_string
    if "?" in path:
        path_part, _, query_part = path.partition("?")
        path = unquote(path_part)
        query_string = query_part.encode("ascii")
        raw_path = path_part.encode("ascii")
    else:
        path = unquote(path)
        query_string = b""
        raw_path = path.encode("ascii")

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "3",
        "method": method,
        "path": path,
        "raw_path": raw_path,
        "query_string": query_string,
        "root_path": config.root_path,
        "scheme": scheme,
        "server": server,
        "client": client,
        "headers": header_list,
        "extensions": {
            "http.response.push": {},
            "pounce.h3.stream_id": stream_id,
            "pounce.h3.is_0rtt": is_0rtt,
        },
    }
    return apply_proxy_headers(scope, trusted_hosts=config.trusted_hosts)


def create_h3_receive(
    body_queue: asyncio.Queue[dict[str, Any]],
) -> Receive:
    """Create an ASGI receive callable for an HTTP/3 stream.

    The protocol pushes body events into the queue as DataReceived arrives.

    """

    async def receive() -> dict[str, Any]:
        return await body_queue.get()

    return receive


def create_h3_send(
    h3_conn: H3Connection,
    stream_id: int,
    transmit: Any,
    state: SendState,
    *,
    timing: ServerTiming | None = None,
    compressor: Compressor | None = None,
    request_method: str = "GET",
    request_id: str | None = None,
) -> Send:
    """Create an ASGI send callable for an HTTP/3 stream.

    Serializes via aioquic H3Connection. After each send, call transmit()
    to flush QUIC packets to the wire.

    """
    response_started = False
    response_complete = False

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_started, response_complete, compressor

        if message["type"] == "http.response.start":
            status: int = message["status"]
            headers: list[tuple[bytes, bytes]] = [
                (
                    name if isinstance(name, bytes) else name.encode(),
                    value if isinstance(value, bytes) else value.encode(),
                )
                for name, value in message.get("headers", [])
            ]

            if status == 103:
                h3_conn.send_headers(
                    stream_id=stream_id,
                    headers=[(b":status", str(status).encode()), *headers],
                )
                transmit()
                return

            response_started = True
            state.status = status
            headers = _sanitize_headers(headers)

            if request_id is not None:
                headers.append((b"x-request-id", request_id.encode("latin-1")))

            if compressor is not None and (100 <= status <= 199 or status in {204, 304}):
                compressor = None
            if compressor is not None and request_method == "HEAD":
                compressor = None
            if compressor is not None:
                for name, value in headers:
                    if name == b"content-type" and b"text/event-stream" in value:
                        compressor = None
                        break

            if compressor is not None:
                headers.append((b"content-encoding", compressor.encoding.encode("ascii")))
                headers = [(n, v) for n, v in headers if n.lower() != b"content-length"]

            if timing is not None:
                rendered = timing.render_bytes()
                if rendered:
                    headers.append((b"server-timing", rendered))

            h3_conn.send_headers(
                stream_id=stream_id,
                headers=[(b":status", str(status).encode()), *headers],
            )
            transmit()

        elif message["type"] == "http.response.body":
            if not response_started:
                raise RuntimeError("Received http.response.body before http.response.start")
            if response_complete:
                raise RuntimeError("Received http.response.body after response is complete")

            body: bytes = message.get("body", b"")
            more_body: bool = message.get("more_body", False)

            if compressor is not None and body:
                body = compressor.compress(body)
                if not more_body:
                    body += compressor.flush()
                else:
                    body += compressor.sync_flush()
            elif compressor is not None and not more_body:
                body = compressor.flush()

            h3_conn.send_data(
                stream_id=stream_id,
                data=body,
                end_stream=not more_body,
            )
            transmit()

            state.bytes_sent += len(body)
            if not more_body:
                response_complete = True

    return send
