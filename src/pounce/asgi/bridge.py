"""
ASGI bridge — translates between protocol events and the ASGI interface.

Builds ASGI scope dicts from protocol events, and creates the async
receive/send callables that ASGI apps interact with.

Streaming-first: send() writes response chunks immediately to the
transport. No buffering — each http.response.body message is compressed
(if applicable) and flushed to the wire before the next one.

Phase 4 hot-path optimizations:
- Pre-encoded ASGI spec constants (avoid per-request dict allocation)
- Bodyless fast-path receive (skip asyncio.Queue for GET/HEAD)
- Single write call for head+body when small
- Reduced isinstance checks

"""

import asyncio
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from pounce._compression import Compressor
from pounce._timing import ServerTiming
from pounce._types import Receive, Send
from pounce.asgi._scope import build_base_scope
from pounce.config import ServerConfig
from pounce.protocols._base import BodyReceived, ProtocolHandler, RequestReceived


@dataclass(slots=True)
class SendState:
    """Mutable holder for response metrics populated by the send callable.

    The worker reads these after the ASGI app completes to get the actual
    HTTP status code and byte count for access logging.
    """

    status: int = 0
    bytes_sent: int = 0
    response_started: bool = False  # True when http.response.start was sent


# ---------------------------------------------------------------------------
# Pre-computed constants — allocated once at import, shared across workers
# ---------------------------------------------------------------------------

# Pre-built terminal body message for bodyless requests (GET, HEAD, etc.)
# Avoids asyncio.Queue entirely for the common no-body case.
# Read-only via MappingProxyType so misbehaving apps can't corrupt it.
_EMPTY_BODY_MESSAGE: MappingProxyType[str, Any] = MappingProxyType(
    {
        "type": "http.request",
        "body": b"",
        "more_body": False,
    }
)

# Pre-built disconnect message — ASGI spec §2.1.3.
# Returned by disconnect-aware receive callables when the client drops.
_DISCONNECT_MESSAGE: MappingProxyType[str, Any] = MappingProxyType(
    {
        "type": "http.disconnect",
    }
)


def _sanitize_headers(headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
    """Strip CR/LF characters from response header names and values.

    Prevents CRLF injection attacks where a malicious ASGI app could inject
    extra headers or split the HTTP response.  This is defense-in-depth — h11
    also validates header content, but we guard before serialization.

    """
    clean: list[tuple[bytes, bytes]] = []
    for name, value in headers:
        if b"\r" in name or b"\n" in name:
            name = name.replace(b"\r", b"").replace(b"\n", b"")
        if b"\r" in value or b"\n" in value:
            value = value.replace(b"\r", b"").replace(b"\n", b"")
        if name:  # skip empty names after stripping
            clean.append((name, value))
    return clean


def build_scope(
    request: RequestReceived,
    config: ServerConfig,
    client: tuple[str, int],
    server: tuple[str, int],
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ASGI HTTP scope dict from a parsed request.

    Args:
        request: The parsed HTTP request head.
        config: Server configuration.
        client: Client (host, port) tuple.
        server: Server (host, port) tuple.
        state: Lifespan state dict to inject into scope["state"].

    Returns:
        ASGI scope dict ready to pass to an ASGI app.

    """
    from pounce._proxy import apply_proxy_headers

    scheme = "https" if config.ssl_certfile else "http"
    scope = build_base_scope(
        request,
        scope_type="http",
        http_version=request.http_version,
        scheme=scheme,
        server=server,
        client=client,
        root_path=config.root_path,
    )
    scope = apply_proxy_headers(scope, trusted_hosts=config.trusted_hosts)

    # Inject lifespan state (ASGI 3.0 spec)
    if state is not None:
        scope["state"] = state

    return scope


def create_receive(
    body_events: asyncio.Queue[BodyReceived],
) -> Receive:
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


def create_empty_receive() -> Receive:
    """Create a fast-path receive for bodyless requests (GET, HEAD, etc.).

    Returns a static empty-body message without asyncio.Queue overhead.
    Called at most once per request — second call would hang, but ASGI
    apps should not call receive() twice for bodyless requests.

    """
    called = False

    async def receive() -> dict[str, Any]:
        nonlocal called
        if not called:
            called = True
            return dict(_EMPTY_BODY_MESSAGE)
        # Block forever — the app shouldn't call receive() again
        # after getting more_body=False.
        await asyncio.Event().wait()
        return dict(_EMPTY_BODY_MESSAGE)  # unreachable, keeps type checker happy

    return receive


def create_disconnect_receive(
    disconnect: asyncio.Event,
) -> Receive:
    """Create a receive callable that delivers ``http.disconnect``.

    For bodyless requests (GET, HEAD, etc.): returns the empty body message
    on first call, then waits for the disconnect event before returning
    ``http.disconnect``.  This allows ASGI apps (e.g. Chirp's SSE handler)
    to detect client disconnects and stop producing events.

    Args:
        disconnect: Event set by the connection monitor when the client
            closes the socket.

    Returns:
        Async callable conforming to the ASGI Receive protocol.

    """
    called = False

    async def receive() -> dict[str, Any]:
        nonlocal called
        if not called:
            called = True
            return dict(_EMPTY_BODY_MESSAGE)
        # Wait until client disconnects
        await disconnect.wait()
        return dict(_DISCONNECT_MESSAGE)

    return receive


def create_receive_with_disconnect(
    body_events: asyncio.Queue[BodyReceived],
    disconnect: asyncio.Event,
) -> Receive:
    """Create a receive callable that delivers body events then ``http.disconnect``.

    Used when the request has a body (POST, PUT, etc.).  Delivers body chunks
    from the queue until the body is complete, then waits for the disconnect
    event and returns ``http.disconnect``.

    Args:
        body_events: Queue of body events from the protocol layer.
        disconnect: Event set by the connection monitor when the client
            closes the socket.

    Returns:
        Async callable conforming to the ASGI Receive protocol.

    """
    body_done = False

    async def receive() -> dict[str, Any]:
        nonlocal body_done
        if not body_done:
            event = await body_events.get()
            if not event.more:
                body_done = True
            return {
                "type": "http.request",
                "body": event.data,
                "more_body": event.more,
            }
        # Body done — wait for disconnect
        await disconnect.wait()
        return dict(_DISCONNECT_MESSAGE)

    return receive


# Threshold for write coalescing: if head + body fit in this many bytes,
# combine them into a single write()/syscall.
_COALESCE_THRESHOLD = 16384  # 16 KB

# Back-pressure threshold: drain the transport buffer when it exceeds
# this size.  Prevents unbounded memory growth when the client reads
# slowly during streaming responses.
_DRAIN_THRESHOLD = 65536  # 64 KB


def create_send(
    protocol: ProtocolHandler,
    writer: asyncio.StreamWriter,
    state: SendState,
    *,
    timing: ServerTiming | None = None,
    compressor: Compressor | None = None,
    request_method: bytes = b"GET",
    request_id: str | None = None,
    config: ServerConfig | None = None,
    server: tuple[str, int] | None = None,
) -> Send:
    """Create an ASGI send callable that streams to the transport.

    Streaming-first: each response.body chunk is written immediately.
    No buffering — the client sees data as soon as the app produces it.

    Write coalescing: the response head is held back and combined with
    the first body chunk in a single ``writer.write()`` call when the
    total fits within ``_COALESCE_THRESHOLD``.  This halves the number
    of syscalls for small responses (the common case).

    Args:
        protocol: Protocol handler for serialization.
        writer: Asyncio stream writer for the connection.
        state: Mutable holder populated with response status and byte count.
        timing: Optional Server-Timing header builder.
        compressor: Optional content compressor for the response.

    Returns:
        Async callable conforming to the ASGI Send protocol.

    """
    response_started = False
    response_complete = False
    # Buffer for write coalescing: holds the serialized response head
    # until the first body chunk arrives.
    pending_head: bytes = b""

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_started, response_complete, pending_head, compressor

        msg_type = message["type"]

        if msg_type == "http.response.start":
            status: int = message["status"]

            # 103 Early Hints — informational response (RFC 8297)
            # Skip for HTTP/1.1 as browser support is limited
            if status == 103:
                return

            response_started = True
            state.response_started = True
            state.status = status
            headers: list[tuple[bytes, bytes]] = [
                (
                    name if isinstance(name, bytes) else name.encode(),
                    value if isinstance(value, bytes) else value.encode(),
                )
                for name, value in message.get("headers", [])
            ]

            # Defense-in-depth: strip CR/LF from header values to prevent
            # header injection attacks from ASGI apps.  h11 also validates,
            # but we guard at the bridge level to catch it before serialization.
            headers = _sanitize_headers(headers)

            # Inject X-Request-ID response header for request tracing
            if request_id is not None:
                headers.append((b"x-request-id", request_id.encode("latin-1")))

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

            # SSE must not be compressed — EventSource API doesn't support it
            if compressor is not None:
                for name, value in headers:
                    if name == b"content-type" and b"text/event-stream" in value:
                        compressor = None
                        break

            # Bodyless responses (RFC 9110 §6.4.1): 1xx, 204, 304 MUST NOT
            # contain a message body.  Disable compression so the compressor's
            # flush() doesn't produce gzip/zstd trailer bytes that h11 would
            # reject as "Too much data for declared Content-Length".
            if compressor is not None and (100 <= status <= 199 or status in {204, 304}):
                compressor = None

            # HEAD responses: the app may produce body bytes (for Content-Length
            # calculation), but they must not be sent on the wire.  If compression
            # modifies the body, Content-Length from the app won't match the
            # compressed bytes.  Disable compression so Content-Length is preserved.
            if compressor is not None and request_method == b"HEAD":
                compressor = None

            # Inject Content-Encoding if compressing
            if compressor is not None:
                headers.append((b"content-encoding", compressor.encoding.encode("ascii")))
                # Remove content-length since compressed size differs
                headers = [(n, v) for n, v in headers if n.lower() != b"content-length"]

            # Auto-inject chunked transfer encoding when the ASGI app
            # doesn't provide Content-Length.  Without either CL or
            # chunked TE, HTTP/1.1 keep-alive connections have no way
            # to delimit response boundaries — the browser hangs.
            # This matches Uvicorn / Hypercorn behaviour and is the
            # standard expectation of any ASGI framework.
            has_content_length = any(n.lower() == b"content-length" for n, _ in headers)
            has_transfer_encoding = any(n.lower() == b"transfer-encoding" for n, _ in headers)
            if not has_content_length and not has_transfer_encoding:
                headers.append((b"transfer-encoding", b"chunked"))

            # Inject Server-Timing header
            if timing is not None:
                rendered = timing.render_bytes()
                if rendered:
                    headers.append((b"server-timing", rendered))

            raw = protocol.send_response(status, headers)

            # Hold back the head for coalescing with the first body chunk.
            pending_head = raw

        elif msg_type == "http.response.body":
            if not response_started:
                raise RuntimeError("Received http.response.body before http.response.start")
            if response_complete:
                raise RuntimeError("Received http.response.body after response is complete")

            # Defense-in-depth: if the client already disconnected, skip
            # the write entirely.  This prevents asyncio's transport from
            # logging ``socket.send() raised exception.`` for every chunk
            # written to a dead connection during the race window between
            # disconnect detection and app cancellation.
            if writer.is_closing():
                return

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

            raw = protocol.send_body(body, more=more_body)

            # Write coalescing: combine pending head + body into one write
            if pending_head:
                if raw and len(pending_head) + len(raw) <= _COALESCE_THRESHOLD:
                    # Small enough to coalesce — single write
                    writer.write(pending_head + raw)
                else:
                    # Too large or no body — flush head, then body
                    writer.write(pending_head)
                    if raw:
                        writer.write(raw)
                pending_head = b""
            elif raw:
                writer.write(raw)

            # Back-pressure: drain when the transport buffer is large.
            # Avoids unbounded memory growth for streaming responses
            # with slow clients, without penalizing small responses.
            transport = writer.transport
            if transport is not None and transport.get_write_buffer_size() > _DRAIN_THRESHOLD:
                await writer.drain()

            state.bytes_sent += len(body)
            if not more_body:
                response_complete = True

    return send
