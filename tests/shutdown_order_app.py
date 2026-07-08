"""ASGI fixture that records request/lifespan ordering across subprocesses."""

from __future__ import annotations

import asyncio
import os
from typing import Any


def _record(event: str) -> None:
    path = os.environ.get("POUNCE_SHUTDOWN_ORDER_LOG")
    if path is None:
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, f"{event}\n".encode("ascii"))
    finally:
        os.close(fd)


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                _record("lifespan.shutdown")
                await send({"type": "lifespan.shutdown.complete"})
                return

    if scope["type"] != "http":
        return

    await receive()
    if scope["path"] == "/slow":
        _record("request.start")
        await asyncio.sleep(0.6)
        body = b"slow-done"
    else:
        body = b"fast-ok"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(body)).encode("ascii"))],
        }
    )
    await send({"type": "http.response.body", "body": body})
    if scope["path"] == "/slow":
        _record("request.complete")
