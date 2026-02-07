"""
Minimal hello-world ASGI app — the standard throughput benchmark.

Pre-encodes everything to measure server overhead, not app logic.
"""

from typing import Any

_BODY = b"Hello, World!"
_HEADERS = [
    (b"content-type", b"text/plain; charset=utf-8"),
    (b"content-length", b"13"),
]


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": _HEADERS})
    await send({"type": "http.response.body", "body": _BODY})
