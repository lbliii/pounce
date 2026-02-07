"""
Minimal ASGI hello-world app for benchmarking pounce.

Usage:
    # Single worker
    pounce benchmarks.hello_app:app

    # Multi-worker (auto-detect)
    pounce benchmarks.hello_app:app --workers 0

    # Then benchmark with:
    wrk -t4 -c100 -d10s http://127.0.0.1:8000/
    # or
    hey -n 10000 -c 100 http://127.0.0.1:8000/

"""

from __future__ import annotations

from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

_BODY = b"Hello, World!"
_HEADERS = [
    (b"content-type", b"text/plain; charset=utf-8"),
    (b"content-length", b"13"),
]


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal ASGI app — returns 'Hello, World!' as fast as possible."""
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
        "headers": _HEADERS,
    })
    await send({
        "type": "http.response.body",
        "body": _BODY,
    })
