"""ASGI fixture whose required per-worker startup hook always fails."""

from __future__ import annotations

from pounce._types import Receive, Scope, Send


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Complete lifespan, but reject required worker initialization."""
    if scope["type"] == "pounce.worker.startup":
        raise RuntimeError("required worker initialization failed")
    if scope["type"] != "lifespan":
        return

    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return
