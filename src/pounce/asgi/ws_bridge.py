"""
WebSocket ASGI bridge — translates between WSProtocol events and ASGI.

Builds ASGI ``websocket`` scope dicts and creates the async receive/send
callables for WebSocket ASGI apps.

ASGI WebSocket lifecycle:
    1. App receives ``websocket.connect``
    2. App sends ``websocket.accept`` (or ``websocket.close`` to reject)
    3. App receives ``websocket.receive`` messages
    4. App sends ``websocket.send`` messages
    5. Either side sends ``websocket.close`` / receives ``websocket.disconnect``

"""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import unquote

from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived


def build_ws_scope(
    request: RequestReceived,
    config: ServerConfig,
    client: tuple[str, int],
    server: tuple[str, int],
) -> dict[str, Any]:
    """Build an ASGI WebSocket scope dict from the upgrade request.

    Args:
        request: The parsed HTTP upgrade request.
        config: Server configuration.
        client: Client (host, port) tuple.
        server: Server (host, port) tuple.

    Returns:
        ASGI scope dict with ``type: "websocket"``.

    """
    target = request.target.decode("ascii", errors="replace")

    if "?" in target:
        path, _, query_string = target.partition("?")
    else:
        path = target
        query_string = ""

    path = unquote(path)

    headers: list[list[bytes]] = [[name, value] for name, value in request.headers]

    # Extract requested subprotocols from Sec-WebSocket-Protocol header
    subprotocols: list[str] = []
    for name, value in request.headers:
        if name.lower() == b"sec-websocket-protocol":
            subprotocols = [
                p.strip() for p in value.decode("ascii", errors="replace").split(",")
            ]
            break

    scheme = "wss" if config.ssl_certfile else "ws"

    return {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": request.http_version,
        "scheme": scheme,
        "path": path,
        "raw_path": request.target.split(b"?")[0],
        "query_string": query_string.encode("ascii"),
        "root_path": config.root_path,
        "server": server,
        "client": client,
        "headers": headers,
        "subprotocols": subprotocols,
    }


def create_ws_receive(
    events: asyncio.Queue[dict[str, Any]],
) -> Any:
    """Create an ASGI receive callable for WebSocket.

    The worker pushes WebSocket events into the queue. The ASGI app
    consumes them via ``receive()``.

    Args:
        events: Queue of ASGI WebSocket event dicts.

    Returns:
        Async callable conforming to the ASGI Receive protocol.

    """

    async def receive() -> dict[str, Any]:
        return await events.get()

    return receive


def create_ws_send(
    writer: asyncio.StreamWriter,
    ws_protocol: Any,  # WSProtocol — typed as Any to avoid import cycle
    ws_key: bytes,
    *,
    accept_event: asyncio.Event,
    close_event: asyncio.Event,
) -> Any:
    """Create an ASGI send callable for WebSocket.

    Handles ``websocket.accept``, ``websocket.send``, and
    ``websocket.close`` messages from the ASGI app.

    Args:
        writer: Asyncio stream writer for the connection.
        ws_protocol: The WSProtocol instance for WebSocket framing.
        ws_key: The Sec-WebSocket-Key from the client's upgrade request.
        accept_event: Set when the app sends ``websocket.accept``.
        close_event: Set when the app sends ``websocket.close``.

    Returns:
        Async callable conforming to the ASGI Send protocol.

    """
    from pounce.protocols.ws import build_101_response

    accepted = False
    closed = False

    async def send(message: dict[str, Any]) -> None:
        nonlocal accepted, closed

        msg_type = message["type"]

        if msg_type == "websocket.accept":
            accepted = True
            subprotocol = message.get("subprotocol")
            # Send the HTTP 101 Switching Protocols response
            raw = build_101_response(ws_key, subprotocol=subprotocol)
            writer.write(raw)
            await writer.drain()
            accept_event.set()

        elif msg_type == "websocket.send":
            if not accepted:
                raise RuntimeError(
                    "Cannot send WebSocket data before websocket.accept"
                )
            if closed:
                raise RuntimeError(
                    "Cannot send WebSocket data after websocket.close"
                )

            # Text or binary — use wsproto for framing
            data = message.get("text")
            if data is not None:
                raw = ws_protocol.send_message(data)
            else:
                raw = ws_protocol.send_message(message.get("bytes", b""))
            writer.write(raw)
            await writer.drain()

        elif msg_type == "websocket.close":
            closed = True
            code = message.get("code", 1000)
            reason = message.get("reason", "")
            raw = ws_protocol.close(code=code, reason=reason)
            writer.write(raw)
            await writer.drain()
            close_event.set()

        elif msg_type == "websocket.http.response.start":
            # WebSocket rejection — send HTTP response instead of upgrade
            close_event.set()

    return send
