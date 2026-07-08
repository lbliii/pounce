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
from collections.abc import Callable
from typing import Any

from pounce._concurrency import race_first_completed
from pounce._headers import get_header as _get_header_from_tuple
from pounce._priority import PriorityScheduler, parse_priority
from pounce._request_id import extract_or_generate
from pounce._request_pipeline import (
    is_trusted_peer,
    log_request,
    maybe_build_builtin_response,
    negotiate_compressor,
)
from pounce._timing import ServerTiming, elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.bridge import SendState, _sanitize_headers
from pounce.asgi.h2_bridge import build_h2_scope, create_h2_receive, create_h2_send
from pounce.asgi.ws_bridge import build_ws_scope
from pounce.config import ServerConfig
from pounce.protocols._base import (
    RequestReceived,
    WebSocketDataReceived,
    WebSocketDisconnected,
)

# RFC 7540 §7: ENHANCE_YOUR_CALM (0xb) signals the peer is exceeding a limit
# (here: the request body exceeded ``max_request_size``).  Sent via RST_STREAM
# after a 413 so the client learns the upload was refused and stops sending.
_H2_ERROR_ENHANCE_YOUR_CALM = 0xB


async def handle_h2_connection(
    app: ASGIApp,
    config: ServerConfig,
    logger: logging.Logger,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    client: tuple[str, int],
    server: tuple[str, int],
    client_str: str,
    *,
    worker_id: int | None = None,
    lifespan_state: dict[str, Any] | None = None,
    is_draining: Callable[[], bool] | None = None,
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

    # Per-connection RFC 9218 priority scheduler — gates DATA frame writes
    # so higher-urgency streams preempt lower ones and incremental streams
    # interleave fairly across concurrent requests.
    scheduler = PriorityScheduler()

    # Per-stream state: {stream_id: (task, body_queue)}
    stream_tasks: dict[int, tuple[asyncio.Task[None], asyncio.Queue[dict]]] = {}
    # Per-stream accumulated body bytes for max_request_size enforcement
    stream_body_bytes: dict[int, int] = {}
    stream_body_rejected: set[int] = set()
    max_body = config.max_request_size

    def _content_length_exceeds_limit(request: RequestReceived) -> bool:
        value = _get_header_from_tuple(request.headers, b"content-length")
        if value is None:
            return False
        try:
            return int(value) > max_body
        except ValueError:
            return False

    def _send_request_too_large(stream_id: int) -> None:
        # Capture whether the inbound (request) half is still open *before*
        # closing our send side with the 413.  RST_STREAM is only meaningful
        # — and only legal in the h2 state machine — while the stream is not
        # fully closed; if the client already ended its body it has stopped
        # uploading and there is nothing left to refuse.
        inbound_open = not h2_conn.stream_ended(stream_id)
        h2_conn.send_response_headers(
            stream_id,
            413,
            [
                (b"content-type", b"text/plain"),
                (b"x-pounce-error-code", b"POUNCE_LIMIT_REQUEST_TOO_LARGE"),
            ],
            end_stream=True,
        )
        if inbound_open:
            # Signal the peer to stop uploading via RST_STREAM.  The 413 with
            # end_stream only half-closes the outbound direction; without a
            # reset the inbound half stays open, the client keeps sending body,
            # and the protocol layer keeps re-crediting its flow-control window
            # for bytes we discard.  ENHANCE_YOUR_CALM tells the client the
            # upload was refused for exceeding a limit.  ``reset_stream`` drops
            # the stream from H2Connection bookkeeping, so subsequent in-flight
            # DATA frames produce no ``H2BodyReceived`` event and are not
            # flow-control-acknowledged.
            h2_conn.reset_stream(stream_id, error_code=_H2_ERROR_ENHANCE_YOUR_CALM)
        else:
            # Body already ended: no reset needed, just drop bookkeeping.
            h2_conn.remove_stream(stream_id)
        writer.write(h2_conn.data_to_send())
        scheduler.remove_stream(stream_id)
        stream_body_bytes.pop(stream_id, None)
        # Keep the stream_body_rejected guard so any DATA frame already parsed
        # in this batch (before the peer observes the reset) is dropped without
        # touching app state.
        stream_body_rejected.add(stream_id)

    async def _run_stream(
        stream_id: int,
        request: RequestReceived,
        body_queue: asyncio.Queue[dict],
    ) -> None:
        """Run the ASGI app for a single HTTP/2 stream."""
        request_start = monotonic_ns()
        scope = build_h2_scope(request, config, client, server, state=lifespan_state)

        # Generate or extract request ID for tracing
        request_id = extract_or_generate(
            request.headers, trusted=is_trusted_peer(config, client[0])
        )
        scope.setdefault("extensions", {})["request_id"] = request_id

        # RFC 9218: register stream priority from Priority header (u=N, i)
        priority_header = _get_header_from_tuple(request.headers, b"priority")
        if priority_header is not None:
            scheduler.set_priority(stream_id, parse_priority(priority_header))

        builtin = maybe_build_builtin_response(
            config,
            request.method,
            scope["path"],
            worker_id=worker_id if worker_id is not None else 0,
            active_connections=0,
            draining=is_draining if is_draining is not None else False,
        )
        if builtin is not None:
            send_state = SendState()
            send = create_h2_send(
                h2_conn,
                stream_id,
                writer,
                send_state,
                request_method=request.method,
                request_id=request_id,
                config=config,
                server=server,
                scheduler=scheduler,
            )
            try:
                await send(
                    {
                        "type": "http.response.start",
                        "status": builtin.status,
                        "headers": builtin.headers,
                    }
                )
                await send({"type": "http.response.body", "body": builtin.body})
            finally:
                h2_conn.remove_stream(stream_id)
                scheduler.remove_stream(stream_id)
                stream_tasks.pop(stream_id, None)
                stream_body_bytes.pop(stream_id, None)
                stream_body_rejected.discard(stream_id)
            return

        timing: ServerTiming | None = None
        if config.server_timing:
            timing = ServerTiming()
            timing.add("parse", elapsed_ms(request_start))

        compressor, dictionary = negotiate_compressor(
            config,
            request.headers,
            request_target=request.target.decode("ascii", errors="replace"),
        )

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
            dictionary_hash=dictionary.sf_hash if dictionary is not None else None,
            request_method=request.method,
            request_id=request_id,
            config=config,
            server=server,
            scheduler=scheduler,
            compression_min_size=config.compression_min_size,
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
            except (OSError, ConnectionError):  # fmt: skip
                pass
            if send_state.status == 0:
                send_state.status = 500
        finally:
            h2_conn.remove_stream(stream_id)
            scheduler.remove_stream(stream_id)
            stream_tasks.pop(stream_id, None)
            stream_body_bytes.pop(stream_id, None)
            stream_body_rejected.discard(stream_id)

        if timing:
            timing.add("app", elapsed_ms(app_start))

        with contextlib.suppress(ConnectionError, OSError):
            await writer.drain()

        log_request(
            config,
            request.method.decode("ascii", errors="replace"),
            request.target.decode("ascii", errors="replace"),
            send_state.status,
            send_state.bytes_sent,
            elapsed_ms(request_start),
            client_str,
            http_version="2",
            request_id=request_id,
            worker_id=worker_id,
        )

    try:
        while not h2_conn.is_closed:
            try:
                data = await asyncio.wait_for(
                    reader.read(65536),
                    timeout=config.keep_alive_timeout,
                )
            except TimeoutError:
                # ``keep_alive_timeout`` reaps an idle connection; it must not
                # cancel an in-flight response merely because the peer is
                # quietly receiving it.  Stream tasks remove themselves on
                # completion, so rearm the read timeout while any stream is
                # active and close only after the connection becomes idle.
                if stream_tasks:
                    continue
                break
            except (ConnectionError, OSError):  # fmt: skip
                break

            if not data:
                break

            events = h2_conn.receive_data(data)
            # Flush any h2-generated output (SETTINGS ACKs, WINDOW_UPDATEs)
            output = h2_conn.data_to_send()
            if output:
                writer.write(output)

            for event in events:
                match event:
                    case H2RequestReceived():
                        if _content_length_exceeds_limit(event.request):
                            logger.warning(
                                "H2 stream %d Content-Length exceeds max_request_size (%d bytes)",
                                event.stream_id,
                                max_body,
                            )
                            _send_request_too_large(event.stream_id)
                            continue
                        body_queue: asyncio.Queue[dict] = asyncio.Queue()
                        task = asyncio.create_task(
                            _run_stream(event.stream_id, event.request, body_queue)
                        )
                        stream_tasks[event.stream_id] = (task, body_queue)

                    case H2WebSocketRequest():
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
                                lifespan_state=lifespan_state,
                            )
                        )
                        stream_tasks[event.stream_id] = (ws_task, ws_queue)

                    case H2BodyReceived():
                        if event.stream_id in stream_body_rejected:
                            continue
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
                                pair[0].cancel()
                                stream_tasks.pop(sid, None)
                                _send_request_too_large(sid)
                            else:
                                await bq.put(
                                    {
                                        "type": "http.request",
                                        "body": event.body.data,
                                        "more_body": event.body.more,
                                    }
                                )

                    case H2StreamReset():
                        stream_body_bytes.pop(event.stream_id, None)
                        stream_body_rejected.discard(event.stream_id)
                        pair = stream_tasks.pop(event.stream_id, None)
                        if pair is not None:
                            pair[0].cancel()

                    case H2GoAway():
                        break  # Stop reading, finish existing streams

            try:
                await writer.drain()
            except (ConnectionError, OSError):  # fmt: skip
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
        except (OSError, ConnectionError):  # fmt: skip
            pass


async def handle_h2_websocket_stream(
    app: ASGIApp,
    config: ServerConfig,
    logger: logging.Logger,
    h2_conn: Any,  # H2Connection
    stream_id: int,
    request: RequestReceived,
    data_queue: asyncio.Queue[dict],
    writer: asyncio.StreamWriter,
    client: tuple[str, int],
    server: tuple[str, int],
    client_str: str,
    *,
    lifespan_state: dict[str, Any] | None = None,
) -> None:
    """Handle a WebSocket-over-HTTP/2 stream (RFC 8441).

    The Extended CONNECT bootstraps a WebSocket session within an H2
    stream.  Data frames on this stream carry WebSocket frames (via
    wsproto), and the ASGI app sees a standard ``websocket`` scope.

    This path mirrors the HTTP/1.1 WebSocket handler (``_ws_handler``):
    it enforces ``websocket_max_message_size`` (WS close 1009 + RST
    stream), negotiates permessage-deflate from the CONNECT headers,
    guards ``websocket.send`` against send-before-accept / send-after-close,
    and implements the ``websocket.http.response.start`` / ``.body`` reject
    path. Unlike H1 the 200 acceptance headers are deferred until the app
    sends ``websocket.accept`` so the negotiated ``Sec-WebSocket-Extensions``
    can be echoed (and a reject can send a non-200 status instead).

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
    from pounce._ws_handler import _permessage_deflate_offer
    from pounce.protocols.ws import WSProtocol, is_wsproto_available

    if not is_wsproto_available():
        logger.warning("WebSocket over H2 requested but wsproto not installed")
        return

    # Negotiate permessage-deflate from the Extended CONNECT headers, exactly
    # like the H1 path: only when config allows it AND the client offered it.
    deflate_offer = (
        _permessage_deflate_offer(request.headers) if config.websocket_compression else None
    )
    ws_proto = WSProtocol(
        enable_compression=deflate_offer is not None,
        compression_offer=deflate_offer,
    )

    # Build WebSocket ASGI scope
    scope = build_ws_scope(request, config, client, server, state=lifespan_state)

    receive_queue: asyncio.Queue[dict] = asyncio.Queue()
    close_event = asyncio.Event()

    # Push the initial connect event
    await receive_queue.put({"type": "websocket.connect"})

    async def _ws_receive() -> dict:
        return await receive_queue.get()

    accepted = False
    closed = False

    async def _ws_send(message: dict) -> None:
        nonlocal accepted, closed

        msg_type = message["type"]

        if msg_type == "websocket.accept":
            accepted = True
            # Send the deferred 200 OK response, echoing the negotiated
            # Sec-WebSocket-Extensions (and subprotocol) so a strict client can
            # size its decompression context correctly.
            accept_headers: list[tuple[bytes, bytes]] = []
            subprotocol = message.get("subprotocol")
            if subprotocol:
                accept_headers.append((b"sec-websocket-protocol", subprotocol.encode("ascii")))
            if ws_proto.extensions_response:
                accept_headers.append(
                    (
                        b"sec-websocket-extensions",
                        ws_proto.extensions_response.encode("ascii"),
                    )
                )
            h2_conn.send_response_headers(stream_id, 200, accept_headers)
            writer.write(h2_conn.data_to_send())

        elif msg_type == "websocket.send":
            if not accepted:
                raise RuntimeError("Cannot send WebSocket data before websocket.accept")
            if closed:
                raise RuntimeError("Cannot send WebSocket data after websocket.close")

            data = message.get("text")
            if data is not None:
                raw = ws_proto.send_message(data)
            else:
                raw = ws_proto.send_message(message.get("bytes", b""))
            h2_conn.send_data(stream_id, raw)
            writer.write(h2_conn.data_to_send())

        elif msg_type == "websocket.close":
            closed = True
            if not accepted:
                # Reject before acceptance: 403 on the still-open CONNECT stream.
                h2_conn.send_response_headers(stream_id, 403, [], end_stream=True)
                writer.write(h2_conn.data_to_send())
            else:
                code = message.get("code", 1000)
                reason = message.get("reason", "")
                raw = ws_proto.close(code=code, reason=reason)
                h2_conn.send_data(stream_id, raw, end_stream=True)
                writer.write(h2_conn.data_to_send())
            close_event.set()

        elif msg_type == "websocket.http.response.start":
            # WebSocket rejection — send an HTTP response instead of accepting.
            # Coerce the status to an int (non-int falls back to 403) and run
            # headers through the same CRLF guard as every other response path.
            closed = True
            raw_status = message.get("status", 403)
            status = raw_status if isinstance(raw_status, int) else 403
            decoded_headers: list[tuple[bytes, bytes]] = [
                (
                    name if isinstance(name, bytes) else name.encode(),
                    value if isinstance(value, bytes) else value.encode(),
                )
                for name, value in message.get("headers", [])
            ]
            headers = _sanitize_headers(decoded_headers)
            h2_conn.send_response_headers(stream_id, status, headers)
            writer.write(h2_conn.data_to_send())

        elif msg_type == "websocket.http.response.body":
            body = message.get("body", b"")
            more_body = message.get("more_body", False)
            h2_conn.send_data(stream_id, body, end_stream=not more_body)
            writer.write(h2_conn.data_to_send())
            if not more_body:
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
                    h2_conn.send_data(stream_id, outbound)
                    writer.write(h2_conn.data_to_send())
                for ws_event in events:
                    if isinstance(ws_event, WebSocketDataReceived):
                        msg_size = len(ws_event.data)
                        if msg_size > config.websocket_max_message_size:
                            # RFC 6455 §7.4.1: 1009 = Message Too Big. Send a
                            # close frame, then RST the stream and stop reading.
                            close_data = ws_proto.close(1009, "Message too large")
                            h2_conn.send_data(stream_id, close_data, end_stream=True)
                            writer.write(h2_conn.data_to_send())
                            h2_conn.reset_stream(stream_id)
                            writer.write(h2_conn.data_to_send())
                            close_event.set()
                            return
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
        # Race the app against the data-frame reader; the loser is cancelled
        # and drained inside the helper so nothing leaks. App exceptions are
        # already swallowed inside _run_app above.
        await race_first_completed(app_task, data_task)
    except Exception:
        logger.exception(
            "Unhandled error on H2 WebSocket from %s",
            client_str,
        )
