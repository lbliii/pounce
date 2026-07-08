"""Small ASGI app and production-shaped Pounce config for Railway."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from pounce import ServerConfig, run


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Serve a JSON probe app with one deliberately slow route."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    if scope["type"] != "http":
        return

    await receive()
    if scope["path"] == "/slow":
        await asyncio.sleep(float(os.environ.get("POUNCE_RECIPE_SLOW_SECONDS", "1.5")))

    body = json.dumps(
        {
            "status": "ok",
            "service": "pounce-railway",
            "message": "Hello from pounce on Railway!",
            "gil_enabled": sys._is_gil_enabled(),
            "scheme": scope["scheme"],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_config() -> ServerConfig:
    """Return the Railway runtime contract using only public Pounce config."""
    return ServerConfig(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        workers=int(os.environ.get("POUNCE_WORKERS", "2")),
        health_check_path="/readyz",
        shutdown_timeout=float(os.environ.get("POUNCE_SHUTDOWN_TIMEOUT", "10")),
        log_format="json",
        access_log=False,
    )


if __name__ == "__main__":
    run(app, config=build_config())
