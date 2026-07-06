"""
HTTP/3 ASGI bridge — maps zoomies H3 events to ASGI scope/receive/send.

Each HTTP/3 stream maps to one ASGI invocation, similar to HTTP/2.
HTTP/3 uses QUIC (UDP) transport with TLS 1.3 built-in.

"""

import asyncio
from typing import TYPE_CHECKING, Any

from pounce._compression import Compressor, should_compress_body
from pounce._headers import get_header
from pounce._timing import ServerTiming
from pounce._types import Receive, Send
from pounce.asgi.bridge import SendState, _sanitize_headers, is_streaming_response
from pounce.config import ServerConfig

if TYPE_CHECKING:
    from zoomies.h3 import H3Connection


class H3PseudoHeaderError(ValueError):
    """HTTP/3 request pseudo-headers are missing, duplicated, or contradictory."""


def build_h3_scope(
    headers: list[tuple[bytes, bytes]],
    config: ServerConfig,
    client: tuple[str, int],
    server: tuple[str, int],
    *,
    stream_id: int = 0,
    is_0rtt: bool = False,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ASGI HTTP scope from HTTP/3 HeadersReceived.

    Parses pseudo-headers (:method, :path, :scheme, :authority) and
    builds the scope. HTTP/3 requires TLS, so scheme is always https.

    Args:
        headers: HTTP/3 header list from zoomies H3HeadersReceived.
        config: Server configuration.
        client: Client (host, port) from QUIC connection.
        server: Server (host, port) tuple.
        stream_id: HTTP/3 stream ID (for extensions).
        is_0rtt: True if request arrived via 0-RTT (replay risk).

    """
    from urllib.parse import unquote

    from pounce._proxy import apply_proxy_headers

    method: str | None = None
    raw_path_value: bytes | None = None
    scheme: str | None = None
    authority: bytes | None = None
    host: bytes | None = None
    header_list: list[tuple[bytes, bytes]] = []
    seen_pseudo_headers: set[bytes] = set()

    for name, value in headers:
        name_lower = name.lower()
        if name_lower.startswith(b":"):
            if name_lower in seen_pseudo_headers:
                raise H3PseudoHeaderError("duplicate HTTP/3 pseudo-header")
            seen_pseudo_headers.add(name_lower)

        if name_lower == b":method":
            method = value.decode("ascii", errors="replace")
        elif name_lower == b":path":
            raw_path_value = value
        elif name_lower == b":scheme":
            scheme = value.decode("ascii", errors="replace")
        elif name_lower == b":authority":
            authority = value
        elif name_lower.startswith(b":"):
            raise H3PseudoHeaderError("unknown HTTP/3 pseudo-header")
        else:
            header_list.append((name_lower, value))
            if name_lower == b"host":
                host = value

    if authority is not None and host is not None and authority != host:
        raise H3PseudoHeaderError("HTTP/3 host does not match :authority")
    effective_authority = authority or host
    if method is None or raw_path_value is None or scheme is None or effective_authority is None:
        raise H3PseudoHeaderError("missing required HTTP/3 pseudo-header")
    if host is None:
        header_list.insert(0, (b"host", effective_authority))

    # Parse path and query_string from raw bytes — split before decoding
    if b"?" in raw_path_value:
        raw_path, _, raw_query = raw_path_value.partition(b"?")
        query_string = raw_query
        path = unquote(raw_path.decode("ascii", errors="replace"))
    else:
        raw_path = raw_path_value
        query_string = b""
        path = unquote(raw_path.decode("ascii", errors="replace"))

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
            "pounce.h3.stream_id": stream_id,
            "pounce.h3.is_0rtt": is_0rtt,
        },
    }
    scope = apply_proxy_headers(
        scope,
        trusted_hosts=config.trusted_hosts,
        trusted_hops=config.forwarded_for_trusted_hops,
    )
    if state is not None:
        scope["state"] = state
    return scope


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
    dictionary_hash: str | None = None,
    request_method: str = "GET",
    request_id: str | None = None,
    compression_min_size: int = 0,
) -> Send:
    """Create an ASGI send callable for an HTTP/3 stream.

    Serializes via zoomies H3Connection. After each send, call transmit()
    to flush QUIC packets to the wire.

    """
    response_started = False
    response_complete = False
    # When compression is gated on compression_min_size but the app did not
    # supply a Content-Length, the header commit is deferred until the first
    # body frame so the single-shot body size is known.
    deferred_start: tuple[int, list[tuple[bytes, bytes]]] | None = None

    def _commit_head(status: int, headers: list[tuple[bytes, bytes]]) -> None:
        """Build and send response headers, injecting Content-Encoding.

        Reads the (possibly mutated) ``compressor`` nonlocal so callers can
        disable compression just before committing the head.
        """
        nonlocal compressor
        if compressor is not None:
            filtered: list[tuple[bytes, bytes]] = []
            has_content_encoding = False
            is_sse = False
            for name, value in headers:
                nl = name.lower()
                if nl == b"content-type" and b"text/event-stream" in value.lower():
                    is_sse = True
                elif nl == b"content-encoding":
                    has_content_encoding = True
                if nl == b"content-length":
                    continue
                filtered.append((name, value))
            if is_sse or has_content_encoding:
                compressor = None
            else:
                filtered.append((b"content-encoding", compressor.encoding.encode("ascii")))
                if dictionary_hash is not None:
                    filtered.append((b"used-dictionary", dictionary_hash.encode("ascii")))
                headers = filtered

        if timing is not None:
            rendered = timing.render_bytes()
            if rendered:
                headers.append((b"server-timing", rendered))

        h3_conn.send_headers(
            stream_id=stream_id,
            headers=[(b":status", str(status).encode()), *headers],
        )
        transmit()

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_started, response_complete, compressor, deferred_start

        if message["type"] == "http.response.start":
            status: int = message["status"]
            headers: list[tuple[bytes, bytes]] = [
                (
                    name if isinstance(name, bytes) else name.encode(),
                    value if isinstance(value, bytes) else value.encode(),
                )
                for name, value in message.get("headers", [])
            ]

            # 103 Early Hints — informational response (RFC 8297).
            # Behaviour is consistent with the H1 and H2 bridges: default-on,
            # headers sanitized, response_started left False so the final
            # response is still committed afterwards.
            if status == 103:
                h3_conn.send_headers(
                    stream_id=stream_id,
                    headers=[(b":status", str(status).encode()), *_sanitize_headers(headers)],
                )
                transmit()
                return

            response_started = True
            state.response_started = True
            state.status = status
            if request_id is not None:
                headers.append((b"x-request-id", request_id.encode("latin-1")))

            headers = _sanitize_headers(headers)
            state.streaming = is_streaming_response(headers)

            if compressor is not None and (100 <= status <= 199 or status in {204, 304}):
                compressor = None
            if compressor is not None and request_method == "HEAD":
                compressor = None

            # Enforce compression_min_size (config-contract parity).  Key off an
            # app-supplied Content-Length, else defer the head commit until the
            # first body frame reveals the single-shot body size.  Streaming
            # responses of unknown size still compress (documented fallback).
            if compressor is not None and compression_min_size > 0:
                cl = get_header(headers, b"content-length")
                if cl is not None:
                    try:
                        declared = int(cl)
                    except ValueError:
                        declared = None
                    if declared is not None and declared < compression_min_size:
                        compressor = None
                else:
                    deferred_start = (status, headers)
                    return

            _commit_head(status, headers)

        elif message["type"] == "http.response.body":
            if not response_started:
                raise RuntimeError("Received http.response.body before http.response.start")
            if response_complete:
                raise RuntimeError("Received http.response.body after response is complete")

            body: bytes = message.get("body", b"")
            more_body: bool = message.get("more_body", False)

            if request_method == "HEAD":
                body = b""

            original_len = len(body)

            # Resolve a deferred head commit now that the body size is known.
            if deferred_start is not None:
                d_status, d_headers = deferred_start
                deferred_start = None
                known_size = original_len if not more_body else None
                if not should_compress_body(known_size, compression_min_size):
                    compressor = None
                _commit_head(d_status, d_headers)

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

            state.bytes_sent += original_len
            if not more_body:
                response_complete = True
                state.response_complete = True

        else:
            raise RuntimeError(
                f"Unexpected ASGI message type: {message['type']!r} for "
                f"{request_method} {request_id or '?'}. "
                f"Expected 'http.response.start' or 'http.response.body'."
            )

    return send
