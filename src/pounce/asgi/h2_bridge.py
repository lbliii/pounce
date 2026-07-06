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

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pounce._compression import Compressor, should_compress_body
from pounce._headers import get_header
from pounce._priority import PriorityScheduler
from pounce._timing import ServerTiming
from pounce._types import Receive, Send
from pounce.asgi._scope import build_base_scope
from pounce.asgi.bridge import SendState, _sanitize_headers, is_streaming_response
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived

logger = logging.getLogger("pounce.asgi.h2_bridge")

if TYPE_CHECKING:
    from pounce.protocols.h2 import H2Connection


def build_h2_scope(
    request: RequestReceived,
    config: ServerConfig,
    client: tuple[str, int],
    server: tuple[str, int],
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ASGI HTTP scope from an HTTP/2 request.

    Same as ``build_scope()`` for H1, but sets ``http_version: "2"``
    and ``scheme: "https"`` (HTTP/2 typically requires TLS).

    """
    from pounce._proxy import apply_proxy_headers

    scheme = "https" if config.ssl_certfile else "http"
    scope = build_base_scope(
        request,
        scope_type="http",
        http_version="2",
        scheme=scheme,
        server=server,
        client=client,
        root_path=config.root_path,
    )
    scope = apply_proxy_headers(
        scope,
        trusted_hosts=config.trusted_hosts,
        trusted_hops=config.forwarded_for_trusted_hops,
    )
    if state is not None:
        scope["state"] = state
    return scope


def create_h2_receive(
    body_queue: asyncio.Queue[dict[str, Any]],
) -> Receive:
    """Create an ASGI receive callable for an HTTP/2 stream.

    The worker pushes body events into the queue as DATA frames arrive.

    """

    async def receive() -> dict[str, Any]:
        return await body_queue.get()

    return receive


def create_h2_send(
    h2_conn: H2Connection,
    stream_id: int,
    writer: asyncio.StreamWriter,
    state: SendState,
    *,
    timing: ServerTiming | None = None,
    compressor: Compressor | None = None,
    dictionary_hash: str | None = None,
    request_method: bytes = b"GET",
    request_id: str | None = None,
    config: ServerConfig | None = None,
    server: tuple[str, int] | None = None,
    scheduler: PriorityScheduler | None = None,
    compression_min_size: int = 0,
) -> Send:
    """Create an ASGI send callable for an HTTP/2 stream.

    Serializes via the shared H2Connection. After each h2 operation,
    flushes ``data_to_send()`` to the writer.

    Args:
        h2_conn: The H2Connection managing this connection.
        stream_id: The h2 stream identifier for this request.
        writer: The asyncio StreamWriter for the TCP connection.
        state: Mutable holder populated with response status and byte count.
        timing: Optional Server-Timing builder.
        compressor: Optional compressor for response body.

    """
    response_started = False
    response_complete = False
    # When compression is gated on compression_min_size but the app did not
    # supply a Content-Length, the header commit is deferred until the first
    # body frame so the single-shot body size is known.
    deferred_start: tuple[int, list[tuple[bytes, bytes]]] | None = None

    def _commit_head(status: int, headers: list[tuple[bytes, bytes]]) -> None:
        """Build and send the response headers, injecting Content-Encoding.

        Reads the (possibly mutated) ``compressor`` nonlocal so callers can
        disable compression just before committing the head.
        """
        nonlocal compressor
        # Single pass: detect SSE and strip content-length when compressing
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

        # Inject Server-Timing header
        if timing is not None:
            rendered = timing.render_bytes()
            if rendered:
                headers.append((b"server-timing", rendered))

        # Alt-Svc for HTTP/3 upgrade (RFC 7838)
        # Use actual bound port from server tuple; config.port may be 0 (ephemeral)
        if config is not None and config.http3_enabled:
            port = server[1] if server and server[1] > 0 else config.port
            if port > 0:
                headers.append(
                    (
                        b"alt-svc",
                        f'h3=":{port}"; ma=2592000'.encode("ascii"),
                    ),
                )

        h2_conn.send_response_headers(stream_id, status, headers)
        _flush(h2_conn, writer)

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
            # Can be sent multiple times before the final response.  Behaviour
            # is consistent with the H1 and H3 bridges: default-on, headers
            # sanitized, response_started left False so the final response is
            # still committed afterwards.
            if status == 103:
                h2_conn.send_response_headers(stream_id, 103, _sanitize_headers(headers))
                _flush(h2_conn, writer)
                return  # Don't mark response_started yet

            response_started = True
            state.response_started = True
            state.status = status

            # Inject X-Request-ID response header
            if request_id is not None:
                headers.append((b"x-request-id", request_id.encode("latin-1")))

            # Defense-in-depth: strip CR/LF from header values
            headers = _sanitize_headers(headers)
            state.streaming = is_streaming_response(headers)

            # Bodyless responses (1xx, 204, 304) — disable compression
            if compressor is not None and (100 <= status <= 199 or status in {204, 304}):
                compressor = None

            # HEAD responses — disable compression to preserve Content-Length
            if compressor is not None and request_method == b"HEAD":
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

            if request_method == b"HEAD":
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

            end_stream = not more_body

            # Respect H2 flow control: split large bodies into
            # window-sized chunks to avoid FlowControlError.
            # RFC 9218: gate each chunk through the priority scheduler so
            # higher-urgency streams preempt lower-urgency ones and
            # incremental streams interleave fairly.
            if scheduler is not None and body:
                scheduler.schedule(stream_id)
            remaining = body
            deadline = asyncio.get_event_loop().time() + 30.0
            try:
                while remaining:
                    window = h2_conn.local_flow_control_window(stream_id)
                    if window <= 0:
                        # Window exhausted — flush and wait for WINDOW_UPDATE
                        _flush(h2_conn, writer)
                        await writer.drain()
                        if asyncio.get_event_loop().time() > deadline:
                            logger.warning(
                                "H2 flow control window timeout on stream %d — resetting stream",
                                stream_id,
                            )
                            h2_conn.reset_stream(stream_id)
                            _flush(h2_conn, writer)
                            return
                        continue
                    if scheduler is not None:
                        await scheduler.await_turn(stream_id)
                    chunk_size = min(len(remaining), window)
                    is_last = end_stream and chunk_size == len(remaining)
                    h2_conn.send_data(
                        stream_id,
                        remaining[:chunk_size],
                        end_stream=is_last,
                    )
                    remaining = remaining[chunk_size:]
                    _flush(h2_conn, writer)
                    if scheduler is not None:
                        scheduler.mark_wrote(stream_id)
            finally:
                if scheduler is not None and (end_stream or not body):
                    scheduler.unschedule(stream_id)

            if not body and end_stream:
                # Empty body with end_stream — send zero-length DATA
                h2_conn.send_data(stream_id, b"", end_stream=True)
                _flush(h2_conn, writer)

            # Back-pressure: drain when the transport buffer is large
            transport = writer.transport
            if transport is not None and transport.get_write_buffer_size() > 65536:
                await writer.drain()

            state.bytes_sent += original_len
            if not more_body:
                response_complete = True
                state.response_complete = True

    return send


def _flush(h2_conn: H2Connection, writer: asyncio.StreamWriter) -> None:
    """Write pending h2 output bytes to the transport."""
    data = h2_conn.data_to_send()
    if data:
        writer.write(data)
