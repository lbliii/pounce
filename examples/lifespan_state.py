"""
Lifespan state example — stores IIC-safe values during startup.

Used by subinterpreter integration tests to verify lifespan state
is passed through to workers.

Run it:
    pounce serve --app examples.lifespan_state:app

Then visit http://127.0.0.1:8000/ — returns the app_name from state.

"""

from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Return the app_name from lifespan state, or 'no-state' if missing."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                scope["state"]["app_name"] = "pounce-test"
                scope["state"]["version"] = 42
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await receive()

    # Read state injected during lifespan (or via subinterpreter bootstrap)
    state = scope.get("state", {})
    app_name = state.get("app_name", "no-state")
    version = state.get("version", 0)
    body = f"{app_name}:{version}\n".encode()

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )
