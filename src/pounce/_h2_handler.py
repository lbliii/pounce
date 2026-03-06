"""
HTTP/2 connection handler — manages multiplexed streams over a single connection.

Extracted from ``worker.py`` to keep protocol-specific connection handling
separate from the core Worker lifecycle.  The worker delegates to
``handle_h2_connection()`` when ALPN negotiation selects ``h2``.

Each function is a standalone coroutine that receives the minimal state
it needs (app, config, logger) — no Worker reference required.

Connection flow:
    TLS handshake → ALPN selects "h2" → H2Connection state machine
    → per-stream ASGI tasks → multiplexed responses → GOAWAY

"""

import asyncio
import contextlib
import logging

from pounce._compression import Compressor, create_compressor, negotiate_encoding
from pounce._health import build_health_response
from pounce._request_id import extract_or_generate
from pounce._timing import ServerTiming, elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.bridge import SendState
from pounce.asgi.h2_bridge import build_h2_scope, create_h2_receive, create_h2_send
from pounce.asgi.ws_bridge import build_ws_scope
from pounce.config import ServerConfig
from pounce.logging import access_log
from pounce.protocols._base import (
    RequestReceived,
    WebSocketDataReceived,
    WebSocketDisconnected,
)


def _get_header_from_tuple(
    headers: tuple[tuple[bytes, bytes], ...],
    name: bytes,
) -> bytes | None:
    """Get a header value by lowercase name from a headers tuple."""
    name_lower = name.lower()
    for header_name, header_value in headers:
        if header_name.lower() == name_lower:
            return header_value
    return None


async def handle_h2_connection(
    app: ASGIApp,
    config: ServerConfig,
    logger: logging.Logger,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    client: tuple[str, int],
    server: tuple[str, int],
    client_str: str,
) -> None:
    """Handle a full HTTP/2 connection with multiplexed streams.

    Runs one ASGI task per stream.  The main loop reads data from the
    network, feeds it to ``H2Connection``, and dispatches per-stream
    events to the appropriate ASGI task.

    Args:
        app: The ASGI application callable.
        config: Immutable server configuration.
        logger: Logger scoped to the worker.
        reader: Asyncio stream reader for the connection.
        writer: Asyncio stream writer for the connection.
        client: ``(host, port)`` of the remote peer.
        server: ``(host, port)`` of the local endpoint.
        client_str: Formatted ``"host:port"`` string for logging.

    """
    from pounce.protocols.h2 import (
        H2BodyReceived,
        H2Connection,
        H2GoAway,
        H2RequestReceived,
        H2StreamReset,
        H2WebSocketRequest,
        is_h2_available,
    )

    if not is_h2_available():
        logger.warning("H2 negotiated but h2 library not available")
        return

    h2_conn = H2Connection()
    h2_conn.initiate_connection()
    writer.write(h2_conn.data_to_send())
    await writer.drain()

    # Per-stream state: {stream_id: (task, body_queue)}
    stream_tasks: dict[int, tuple[asyncio.Task[None], asyncio.Queue[dict]]] = {}
    # Per-stream accumulated body bytes for max_request_size enforcement
    stream_body_bytes: dict[int, int] = {}
    max_body = config.max_request_size

    async def _run_stream(
        stream_id: int,
        request: RequestReceived,
        body_queue: asyncio.Queue[dict],
    ) -> None:
        """Run the ASGI app for a single HTTP/2 stream."""
        request_start = monotonic_ns()
        scope = build_h2_scope(request, config, client, server)

        # Generate or extract request ID for tracing
        is_trusted_peer = bool(
            config.trusted_hosts
            and ("*" in config.trusted_hosts or client[0] in config.trusted_hosts)
        )
        request_id = extract_or_generate(request.headers, trusted=is_trusted_peer)
        scope.setdefault("extensions", {})["request_id"] = request_id

        # Built-in health check — respond before ASGI dispatch
        health_path = config.health_check_path
        if health_path is not None and scope["path"] == health_path and request.method == b"GET":
            h_status, h_headers, h_body = build_health_response(
                worker_id=0,  # H2 handler doesn't track worker_id
                active_connections=0,
            )
            h2_conn.send_response_headers(stream_id, h_status, h_headers)
            h2_conn.send_data(stream_id, h_body, end_stream=True)
            writer.write(h2_conn.data_to_send())
            h2_conn.remove_stream(stream_id)
            stream_tasks.pop(stream_id, None)
            return

        timing: ServerTiming | None = None
        if config.server_timing:
            timing = ServerTiming()
            timing.add("parse", elapsed_ms(request_start))

        compressor: Compressor | None = None
        if config.compression:
            accept_encoding = _get_header_from_tuple(request.headers, b"accept-encoding")
            if accept_encoding:
                encoding = negotiate_encoding(accept_encoding)
                if encoding:
                    compressor = create_compressor(encoding)

        receive = create_h2_receive(body_queue)
        app_start = monotonic_ns()
        send_state = SendState()
        send = create_h2_send(
            h2_conn,
            stream_id,
            writer,
            send_state,
            timing=timing,
            compressor=compressor,
            request_method=request.method,
            request_id=request_id,
            config=config,
            server=server,
        )

        try:
            await app(scope, receive, send)
        except Exception:
            logger.exception(
                "ASGI app error on H2 stream %d %s %s",
                stream_id,
                scope["method"],
                scope["path"],
            )
            try:
                h2_conn.send_response_headers(
                    stream_id,
                    500,
                    [(b"content-type", b"text/plain")],
                )
                h2_conn.send_data(
                    stream_id,
                    b"Internal Server Error",
                    end_stream=True,
                )
                writer.write(h2_conn.data_to_send())
            except (OSError, ConnectionError):
                pass
            if send_state.status == 0:
                send_state.status = 500
        finally:
            h2_conn.remove_stream(stream_id)
            stream_tasks.pop(stream_id, None)
            stream_body_bytes.pop(stream_id, None)

        if timing:
            timing.add("app", elapsed_ms(app_start))

        with contextlib.suppress(ConnectionError, OSError):
            await writer.drain()

        if config.access_log:
            duration = elapsed_ms(request_start)
            target = request.target.decode("ascii", errors="replace")
            method = request.method.decode("ascii", errors="replace")
            log_filter = config.access_log_filter
            if log_filter is None or log_filter(method, target, send_state.status):
                access_log(
                    method,
                    target,
                    send_state.status,
                    send_state.bytes_sent,
                    duration,
                    client_str,
                    http_version="2",
                    request_id=request_id,
                )

    try:
        while not h2_conn.is_closed:
            try:
                data = await asyncio.wait_for(
                    reader.read(65536),
                    timeout=config.keep_alive_timeout,
                )
            except TimeoutError:
                break
            except (ConnectionError, OSError):
                break

            if not data:
                break

            events = h2_conn.receive_data(data)
            # Flush any h2-generated output (SETTINGS ACKs, WINDOW_UPDATEs)
            output = h2_conn.data_to_send()
            if output:
                writer.write(output)

            for event in events:
                if isinstance(event, H2RequestReceived):
                    body_queue: asyncio.Queue[dict] = asyncio.Queue()
                    task = asyncio.create_task(
                        _run_stream(event.stream_id, event.request, body_queue)
                    )
                    stream_tasks[event.stream_id] = (task, body_queue)

                elif isinstance(event, H2WebSocketRequest):
                    # RFC 8441: WebSocket over HTTP/2 via Extended CONNECT
                    ws_queue: asyncio.Queue[dict] = asyncio.Queue()
                    ws_task = asyncio.create_task(
                        handle_h2_websocket_stream(
                            app,
                            config,
                            logger,
                            h2_conn,
                            event.stream_id,
                            event.request,
                            ws_queue,
                            writer,
                            client,
                            server,
                            client_str,
                        )
                    )
                    stream_tasks[event.stream_id] = (ws_task, ws_queue)

                elif isinstance(event, H2BodyReceived):
                    pair = stream_tasks.get(event.stream_id)
                    if pair is not None:
                        _, bq = pair
                        # Enforce max_request_size for streaming H2 bodies
                        sid = event.stream_id
                        stream_body_bytes[sid] = stream_body_bytes.get(sid, 0) + len(
                            event.body.data
                        )
                        if stream_body_bytes[sid] > max_body:
                            logger.warning(
                                "H2 stream %d body exceeds max_request_size (%d bytes)",
                                sid,
                                max_body,
                            )
                            await bq.put(
                                {
                                    "type": "http.request",
                                    "body": b"",
                                    "more_body": False,
                                }
                            )
                        else:
                            await bq.put(
                                {
                                    "type": "http.request",
                                    "body": event.body.data,
                                    "more_body": event.body.more,
                                }
                            )

                elif isinstance(event, H2StreamReset):
                    stream_body_bytes.pop(event.stream_id, None)
                    pair = stream_tasks.pop(event.stream_id, None)
                    if pair is not None:
                        pair[0].cancel()

                elif isinstance(event, H2GoAway):
                    break  # Stop reading, finish existing streams

            try:
                await writer.drain()
            except (ConnectionError, OSError):
                break

    finally:
        # Cancel all remaining stream tasks
        for task, _ in stream_tasks.values():
            task.cancel()
        # Wait for cancellations to complete
        if stream_tasks:
            await asyncio.gather(
                *(task for task, _ in stream_tasks.values()),
                return_exceptions=True,
            )
        # Send GOAWAY
        try:
            h2_conn.close_connection()
            writer.write(h2_conn.data_to_send())
            await writer.drain()
        except (OSError, ConnectionError):
            pass


async def handle_h2_websocket_stream(
    app: ASGIApp,
    config: ServerConfig,
    logger: logging.Logger,
    h2_conn: object,  # H2Connection
    stream_id: int,
    request: RequestReceived,
    data_queue: asyncio.Queue[dict],
    writer: asyncio.StreamWriter,
    client: tuple[str, int],
    server: tuple[str, int],
    client_str: str,
) -> None:
    """Handle a WebSocket-over-HTTP/2 stream (RFC 8441).

    The Extended CONNECT bootstraps a WebSocket session within an H2
    stream.  Data frames on this stream carry WebSocket frames (via
    wsproto), and the ASGI app sees a standard ``websocket`` scope.

    Args:
        app: The ASGI application callable.
        config: Immutable server configuration.
        logger: Logger scoped to the worker.
        h2_conn: The ``H2Connection`` managing this HTTP/2 connection.
        stream_id: The H2 stream identifier for this WebSocket session.
        request: The parsed request event (Extended CONNECT).
        data_queue: Queue fed by the H2 connection's event loop.
        writer: Asyncio stream writer for the connection.
        client: ``(host, port)`` of the remote peer.
        server: ``(host, port)`` of the local endpoint.
        client_str: Formatted ``"host:port"`` string for logging.

    """
    from pounce.protocols.ws import WSProtocol, is_wsproto_available

    if not is_wsproto_available():
        logger.warning("WebSocket over H2 requested but wsproto not installed")
        return

    ws_proto = WSProtocol()

    # Send 200 OK response headers to accept the Extended CONNECT
    h2_conn.send_response_headers(stream_id, 200, [])  # type: ignore[union-attr]
    writer.write(h2_conn.data_to_send())  # type: ignore[union-attr]

    # Build WebSocket ASGI scope
    scope = build_ws_scope(request, config, client, server)

    receive_queue: asyncio.Queue[dict] = asyncio.Queue()
    close_event = asyncio.Event()

    # Push the initial connect event
    await receive_queue.put({"type": "websocket.connect"})

    async def _ws_receive() -> dict:
        return await receive_queue.get()

    accepted = False

    async def _ws_send(message: dict) -> None:
        nonlocal accepted

        msg_type = message["type"]

        if msg_type == "websocket.accept":
            accepted = True
            # Already sent 200 OK; no further handshake needed for H2 WS

        elif msg_type == "websocket.send":
            data = message.get("text")
            if data is not None:
                raw = ws_proto.send_message(data)
            else:
                raw = ws_proto.send_message(message.get("bytes", b""))
            h2_conn.send_data(stream_id, raw)  # type: ignore[union-attr]
            writer.write(h2_conn.data_to_send())  # type: ignore[union-attr]

        elif msg_type == "websocket.close":
            code = message.get("code", 1000)
            reason = message.get("reason", "")
            raw = ws_proto.close(code=code, reason=reason)
            h2_conn.send_data(stream_id, raw, end_stream=True)  # type: ignore[union-attr]
            writer.write(h2_conn.data_to_send())  # type: ignore[union-attr]
            close_event.set()

    # Run the ASGI app
    async def _run_app() -> None:
        try:
            await app(scope, _ws_receive, _ws_send)
        except Exception:
            logger.exception(
                "ASGI app error on H2 WebSocket stream %d",
                stream_id,
            )

    # Process incoming H2 data frames as WebSocket frames
    async def _process_data() -> None:
        while not close_event.is_set():
            try:
                msg = await asyncio.wait_for(data_queue.get(), timeout=30.0)
            except TimeoutError:
                continue

            raw_data = msg.get("body", b"")
            if raw_data:
                events, outbound = ws_proto.receive_data(raw_data)
                if outbound:
                    h2_conn.send_data(stream_id, outbound)  # type: ignore[union-attr]
                    writer.write(h2_conn.data_to_send())  # type: ignore[union-attr]
                for ws_event in events:
                    if isinstance(ws_event, WebSocketDataReceived):
                        if isinstance(ws_event.data, str):
                            await receive_queue.put(
                                {
                                    "type": "websocket.receive",
                                    "text": ws_event.data,
                                }
                            )
                        else:
                            await receive_queue.put(
                                {
                                    "type": "websocket.receive",
                                    "bytes": ws_event.data,
                                }
                            )
                    elif isinstance(ws_event, WebSocketDisconnected):
                        await receive_queue.put(
                            {
                                "type": "websocket.disconnect",
                                "code": ws_event.code,
                            }
                        )
                        return

            if not msg.get("more_body", True):
                await receive_queue.put(
                    {
                        "type": "websocket.disconnect",
                        "code": 1000,
                    }
                )
                return

    app_task = asyncio.create_task(_run_app())
    data_task = asyncio.create_task(_process_data())

    try:
        _done, pending = await asyncio.wait(
            {app_task, data_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    except Exception:
        logger.exception(
            "Unhandled error on H2 WebSocket from %s",
            client_str,
        )
