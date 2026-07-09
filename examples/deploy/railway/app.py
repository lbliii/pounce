"""Small ASGI app and production-shaped Pounce config for Railway."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from pounce import ServerConfig, __version__, run


def _runtime_payload(scope: dict[str, Any]) -> dict[str, Any]:
    """Return public, non-secret identity for the deployed Pounce build."""
    return {
        "status": "ok",
        "service": os.environ.get("RAILWAY_SERVICE_NAME", "pounce-railway"),
        "channel": os.environ.get("POUNCE_DEPLOYMENT_CHANNEL", "release"),
        "message": "Hello from pounce on Railway!",
        "pounce_version": __version__,
        "python_version": sys.version.split()[0],
        "gil_enabled": sys._is_gil_enabled(),
        "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA"),
        "git_branch": os.environ.get("RAILWAY_GIT_BRANCH"),
        "scheme": scope["scheme"],
    }


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
    if scope["path"] == "/stream":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                ],
            }
        )
        for sequence in (1, 2):
            event = json.dumps(
                {
                    "sequence": sequence,
                    "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA"),
                },
                separators=(",", ":"),
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": f"event: canary\ndata: {event}\n\n".encode(),
                    "more_body": sequence == 1,
                }
            )
            if sequence == 1:
                await asyncio.sleep(0.05)
        return

    if scope["path"] == "/slow":
        await asyncio.sleep(float(os.environ.get("POUNCE_RECIPE_SLOW_SECONDS", "1.5")))

    body = json.dumps(_runtime_payload(scope), separators=(",", ":")).encode("utf-8")
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
