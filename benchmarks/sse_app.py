"""
SSE streaming ASGI app for benchmarking pounce.

Streams Server-Sent Events at a configurable interval. Used by the SSE
stress test to verify that sustained concurrent streaming connections
do not leak memory.

Usage:
    pounce benchmarks.sse_app:app --workers 2 --no-access-log

"""

from __future__ import annotations

import asyncio
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

# Default: send one event every 100ms
_INTERVAL = 0.1


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that streams SSE events until the client disconnects."""
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

    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/event-stream"),
            (b"cache-control", b"no-cache"),
            (b"connection", b"keep-alive"),
        ],
    })

    tick = 0
    try:
        while True:
            chunk = f"data: tick {tick}\n\n".encode()
            await send({
                "type": "http.response.body",
                "body": chunk,
                "more_body": True,
            })
            tick += 1
            await asyncio.sleep(_INTERVAL)
    except (asyncio.CancelledError, ConnectionError, OSError):
        # Client disconnected — send final empty body to close
        pass

    await send({
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    })
