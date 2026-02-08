"""
Factory-pattern ASGI application.

Demonstrates the app factory pattern where the ASGI callable is created
by calling a function rather than imported directly.  Useful when the
app needs configuration or setup before it can handle requests.

Run it:
    pounce examples.factory_app:create_app()

Then visit http://127.0.0.1:8000/ in a browser or:
    curl http://127.0.0.1:8000/

"""

from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any


def create_app(greeting: str = "Hello from factory!") -> Any:
    """Create an ASGI app with the given greeting.

    This is the factory function.  Pounce calls it when the app string
    ends with ``()`` — e.g., ``examples.factory_app:create_app()``.

    Args:
        greeting: The response body text.

    Returns:
        An ASGI application callable.

    """
    body = greeting.encode("utf-8")
    headers = [
        (b"content-type", b"text/plain; charset=utf-8"),
        (b"content-length", str(len(body)).encode("ascii")),
    ]

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )

    return app
