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

import asyncio
from typing import TYPE_CHECKING, Any

from pounce._types import Receive, Send
from pounce.asgi._scope import build_base_scope
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived

if TYPE_CHECKING:
    from pounce.protocols.ws import WSProtocol


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
    # Extract requested subprotocols from Sec-WebSocket-Protocol header
    subprotocols: list[str] = []
    for name, value in request.headers:
        if name.lower() == b"sec-websocket-protocol":
            subprotocols = [p.strip() for p in value.decode("ascii", errors="replace").split(",")]
            break

    scheme = "wss" if config.ssl_certfile else "ws"
    scope = build_base_scope(
        request,
        scope_type="websocket",
        http_version=request.http_version,
        scheme=scheme,
        server=server,
        client=client,
        root_path=config.root_path,
    )
    scope["subprotocols"] = subprotocols
    return scope


def create_ws_receive(
    events: asyncio.Queue[dict[str, Any]],
) -> Receive:
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
    ws_protocol: WSProtocol,
    ws_key: bytes,
    *,
    accept_event: asyncio.Event,
    close_event: asyncio.Event,
) -> Send:
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
            # Send the HTTP 101 Switching Protocols response with extensions
            raw = build_101_response(
                ws_key,
                subprotocol=subprotocol,
                extensions=ws_protocol.extensions_response,
            )
            writer.write(raw)
            await writer.drain()
            accept_event.set()

        elif msg_type == "websocket.send":
            if not accepted:
                raise RuntimeError("Cannot send WebSocket data before websocket.accept")
            if closed:
                raise RuntimeError("Cannot send WebSocket data after websocket.close")

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
            if not accepted:
                # Reject before upgrade: send HTTP 403 instead of WS close frame
                raw = (
                    b"HTTP/1.1 403 Forbidden\r\n"
                    b"content-type: text/plain\r\n"
                    b"content-length: 9\r\n"
                    b"connection: close\r\n"
                    b"\r\n"
                    b"Forbidden"
                )
                writer.write(raw)
                await writer.drain()
            else:
                code = message.get("code", 1000)
                reason = message.get("reason", "")
                raw = ws_protocol.close(code=code, reason=reason)
                writer.write(raw)
                await writer.drain()
            close_event.set()

        elif msg_type == "websocket.http.response.start":
            # WebSocket rejection — send HTTP response instead of upgrade
            closed = True
            status = message.get("status", 403)
            headers: list[tuple[bytes, bytes]] = [
                (
                    name if isinstance(name, bytes) else name.encode(),
                    value if isinstance(value, bytes) else value.encode(),
                )
                for name, value in message.get("headers", [])
            ]
            parts = [f"HTTP/1.1 {status} Rejected\r\n".encode()]
            for name, value in headers:
                parts.extend((name, b": ", value, b"\r\n"))
            parts.append(b"\r\n")
            writer.write(b"".join(parts))

        elif msg_type == "websocket.http.response.body":
            # Body of an HTTP rejection response
            body = message.get("body", b"")
            if body:
                writer.write(body)
            more_body = message.get("more_body", False)
            if not more_body:
                await writer.drain()
                close_event.set()

    return send
