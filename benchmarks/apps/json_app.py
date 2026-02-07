"""
JSON serialization benchmark — measures encode overhead per request.
"""

import json
from typing import Any

_PAYLOAD = {
    "message": "Hello, World!",
    "server": "pounce",
    "framework": "none",
    "features": ["free-threading", "streaming", "zstd"],
}
_BODY = json.dumps(_PAYLOAD).encode("utf-8")
_HEADERS = [
    (b"content-type", b"application/json"),
    (b"content-length", str(len(_BODY)).encode("ascii")),
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
