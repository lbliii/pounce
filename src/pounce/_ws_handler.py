"""
WebSocket connection handler — HTTP/1.1 upgrade lifecycle.

Extracted from ``worker.py`` to keep protocol-specific connection handling
separate from the core Worker lifecycle.  The worker delegates to
``handle_websocket()`` when it detects an HTTP/1.1 WebSocket upgrade.

Connection flow:
    H1 request → detect ``Connection: Upgrade`` + ``Upgrade: websocket``
    → build ASGI ``websocket`` scope → 101 Switching Protocols
    → wsproto frame loop → app(scope, receive, send)

"""

import asyncio
import contextlib
import logging

from pounce._headers import get_header as _get_header_from_tuple
from pounce._timing import elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.ws_bridge import build_ws_scope, create_ws_receive, create_ws_send
from pounce.config import ServerConfig
from pounce.logging import access_log
from pounce.protocols._base import (
    RequestReceived,
    WebSocketDataReceived,
    WebSocketDisconnected,
)


async def handle_websocket(
    app: ASGIApp,
    config: ServerConfig,
    logger: logging.Logger,
    request: RequestReceived,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    client: tuple[str, int],
    server: tuple[str, int],
    client_str: str,
    *,
    worker_id: int | None = None,
) -> None:
    """Handle a WebSocket connection after HTTP/1.1 upgrade detection.

    Lifecycle:
        1. Build ASGI ``websocket`` scope
        2. Push ``websocket.connect`` to the receive queue
        3. Run the ASGI app — it sends ``websocket.accept`` (or close)
        4. Read WebSocket frames and feed to receive queue
        5. App sends ``websocket.send`` / ``websocket.close``
        6. Clean up when either side disconnects

    Args:
        app: The ASGI application callable.
        config: Immutable server configuration.
        logger: Logger scoped to the worker.
        request: The parsed HTTP request that triggered the upgrade.
        reader: Asyncio stream reader for the connection.
        writer: Asyncio stream writer for the connection.
        client: ``(host, port)`` of the remote peer.
        server: ``(host, port)`` of the local endpoint.
        client_str: Formatted ``"host:port"`` string for logging.

    """
    from pounce.protocols.ws import WSProtocol, is_wsproto_available

    if not is_wsproto_available():
        logger.warning("WebSocket upgrade requested but wsproto not installed")
        return

    request_start = monotonic_ns()

    # Build WebSocket ASGI scope
    scope = build_ws_scope(request, config, client, server)

    # Extract Sec-WebSocket-Key for the 101 handshake
    ws_key = _get_header_from_tuple(request.headers, b"sec-websocket-key")
    if not ws_key:
        logger.warning("WebSocket upgrade missing Sec-WebSocket-Key")
        return

    # Create protocol and ASGI bridge (with compression if enabled)
    ws_proto = WSProtocol(enable_compression=config.websocket_compression)
    accept_event = asyncio.Event()
    close_event = asyncio.Event()

    receive_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    receive = create_ws_receive(receive_queue)
    send = create_ws_send(
        writer,
        ws_proto,
        ws_key,
        accept_event=accept_event,
        close_event=close_event,
    )

    # Push the initial connect event
    await receive_queue.put({"type": "websocket.connect"})

    # Run the ASGI app and the frame reader concurrently
    async def _run_app() -> None:
        try:
            await app(scope, receive, send)
        except Exception:
            logger.exception("ASGI app error on WebSocket %s", scope["path"])

    async def _read_frames() -> None:
        """Read WebSocket frames from the client and push to queue."""
        # Wait for the app to accept before reading frames
        await accept_event.wait()

        try:
            while not close_event.is_set():
                try:
                    data = await asyncio.wait_for(
                        reader.read(65536),
                        timeout=config.keep_alive_timeout,
                    )
                except TimeoutError:
                    break
                except ConnectionError, OSError:
                    break

                if not data:
                    break

                events, outbound = ws_proto.receive_data(data)
                if outbound:
                    writer.write(outbound)
                    await writer.drain()
                for event in events:
                    if isinstance(event, WebSocketDataReceived):
                        if isinstance(event.data, str):
                            await receive_queue.put(
                                {
                                    "type": "websocket.receive",
                                    "text": event.data,
                                }
                            )
                        else:
                            await receive_queue.put(
                                {
                                    "type": "websocket.receive",
                                    "bytes": event.data,
                                }
                            )
                    elif isinstance(event, WebSocketDisconnected):
                        await receive_queue.put(
                            {
                                "type": "websocket.disconnect",
                                "code": event.code,
                            }
                        )
                        return
        finally:
            # Ensure the app unblocks if still waiting on receive
            if not close_event.is_set():
                await receive_queue.put(
                    {
                        "type": "websocket.disconnect",
                        "code": 1006,
                    }
                )

    app_task = asyncio.create_task(_run_app())
    reader_task = asyncio.create_task(_read_frames())

    try:
        # Wait for either the app or the reader to finish
        _done, pending = await asyncio.wait(
            {app_task, reader_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    except Exception:
        logger.exception("Unhandled error on WebSocket from %s", client_str)

    # Access log
    if config.access_log:
        duration = elapsed_ms(request_start)
        target = request.target.decode("ascii", errors="replace")
        access_log("WS", target, 101, 0, duration, client_str, worker_id=worker_id)
