"""
WebSocket echo server — demonstrates pounce's WebSocket support.

A WebSocket connection is an upgrade from HTTP/1.1 (or a stream in
HTTP/2).  Pounce handles the handshake transparently — your ASGI app
just receives ``scope["type"] == "websocket"`` and interacts via
``receive()`` / ``send()`` messages.

Prerequisites:
    pip install bengal-pounce[ws]   # installs wsproto

Run it:
    pounce examples.websocket_echo:app

Test with websocat, wscat, or a browser console:
    websocat ws://127.0.0.1:8000/ws
    > hello
    < echo: hello

Or from the browser console:
    ws = new WebSocket("ws://127.0.0.1:8000/ws");
    ws.onmessage = (e) => console.log(e.data);
    ws.onopen = () => ws.send("hello");

"""

from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Echo WebSocket messages back with a prefix."""
    # --- Lifespan -----------------------------------------------------------
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    # --- HTTP fallback for non-WebSocket requests ---------------------------
    if scope["type"] == "http":
        await receive()
        body = b"This endpoint expects a WebSocket connection.\n"
        await send(
            {
                "type": "http.response.start",
                "status": 426,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"content-length", str(len(body)).encode()),
                    (b"upgrade", b"websocket"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
        return

    # --- WebSocket ----------------------------------------------------------
    assert scope["type"] == "websocket"

    # Wait for the client to initiate the connection.
    message = await receive()
    if message["type"] != "websocket.connect":
        return

    # Accept the connection (optionally set subprotocol here).
    await send({"type": "websocket.accept"})

    # Echo loop — runs until the client disconnects.
    try:
        while True:
            message = await receive()

            if message["type"] == "websocket.disconnect":
                break

            if message["type"] == "websocket.receive":
                text = message.get("text")
                data = message.get("bytes")

                if text is not None:
                    await send(
                        {
                            "type": "websocket.send",
                            "text": f"echo: {text}",
                        }
                    )
                elif data is not None:
                    await send(
                        {
                            "type": "websocket.send",
                            "bytes": data,
                        }
                    )
    except Exception:
        pass

    # Close the connection from the server side.
    await send({"type": "websocket.close", "code": 1000})
