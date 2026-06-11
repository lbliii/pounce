"""
Minimal ASGI "Hello, World!" application.

The simplest possible pounce app — no lifespan, no streaming, no
dependencies.  This is the "start here" example.

Run it:
    pounce serve --app examples.hello:app

Then visit http://127.0.0.1:8000/ in a browser or:
    curl http://127.0.0.1:8000/

"""

from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

# Pre-encode the response body and headers for zero per-request allocation.
_BODY = b"Hello, World!"
_HEADERS = [
    (b"content-type", b"text/plain; charset=utf-8"),
    (b"content-length", b"13"),
]


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Return ``Hello, World!`` for every HTTP request."""
    # ASGI servers send lifespan events before any HTTP traffic.
    # This app has nothing to initialise so it just acknowledges them.
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    # Consume the request body (required by the ASGI spec).
    await receive()

    # Send the response — two messages: headers then body.
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": _HEADERS,
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": _BODY,
        }
    )
