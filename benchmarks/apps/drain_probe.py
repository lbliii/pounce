"""Drain-under-load probe app for the reload/drain integration proof (#104).

Exposes a small surface that exercises every drain code path across worker
modes (async / sync / subinterpreter / process):

- ``GET /fast``     -> immediate 200 (keep-alive client loops on this)
- ``GET /slow``     -> sleeps ~0.6s then 200 (in-flight request that must
                       complete across a drain)
- ``GET /stream``   -> chunked streaming body paced by sleeps; on the sync
                       worker this triggers a handoff to the AsyncPool, so it
                       proves streaming survives drain / runs new code on reload
- ``GET /version``  -> returns ``VERSION`` so a SIGHUP reload can prove the
                       reimported module is what serves new requests (#102)

Pure ASGI, no framework. Safe to import by path in a subinterpreter.
"""

from __future__ import annotations

import asyncio
from typing import Any

# Bumped by the #104 reload test (rewritten on disk + SIGHUP) to prove the
# reimported module serves new requests. Keep on its own line for easy patching.
VERSION = "v1"


async def _send_text(send: Any, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _drain_receive_body(receive: Any) -> None:
    """Consume the request body so keep-alive framing stays intact."""
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return
        if not message.get("more_body", False):
            return


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

    if scope["type"] != "http":
        return

    path = scope["path"]
    await _drain_receive_body(receive)

    if path == "/slow":
        await asyncio.sleep(0.6)
        await _send_text(send, 200, b"slow-done")
        return

    if path == "/version":
        await _send_text(send, 200, VERSION.encode("ascii"))
        return

    if path == "/stream":
        # Streaming body (more_body=True) -> AsyncPool handoff on the sync worker.
        chunks = [b"chunk-0\n", b"chunk-1\n", b"chunk-2\n"]
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain; charset=utf-8")],
            }
        )
        for i, chunk in enumerate(chunks):
            more = i < len(chunks) - 1
            await send({"type": "http.response.body", "body": chunk, "more_body": more})
            if more:
                await asyncio.sleep(0.15)
        return

    # Default: /fast and everything else.
    await _send_text(send, 200, b"fast-ok")
