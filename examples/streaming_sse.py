"""
Server-Sent Events (SSE) streaming example.

Streams named events with JSON payloads — more realistic than a bare
``data: tick N`` loop.  Demonstrates pounce's streaming-first pipeline:
body chunks flow directly to the socket, never buffered.

Run it:
    pounce examples.streaming_sse:app

Then open an SSE client:
    curl -N http://127.0.0.1:8000/

You will see a ``heartbeat`` event every second and a ``message`` event
every 3 seconds with a JSON payload.

"""

import asyncio
import json
import time
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

# ---------------------------------------------------------------------------
# SSE formatting helpers
# ---------------------------------------------------------------------------


def _sse_event(event: str, data: str, event_id: int | None = None) -> bytes:
    """Format a single SSE event as bytes.

    See https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events
    """
    parts: list[str] = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event}")
    # data lines — each line gets its own ``data:`` prefix
    parts.extend(f"data: {line}" for line in data.splitlines())
    parts.append("")  # trailing blank line terminates the event
    parts.append("")
    return "\n".join(parts).encode()


# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------

_HEARTBEAT_INTERVAL = 1.0  # seconds
_MESSAGE_INTERVAL = 3.0  # seconds


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Stream SSE events until the client disconnects."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    if scope["type"] != "http":
        return

    await receive()

    # SSE responses must not be compressed (breaks streaming in most clients).
    # pounce automatically disables compression when it detects a
    # text/event-stream content type in the response headers.
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream; charset=utf-8"),
                (b"cache-control", b"no-cache"),
                (b"connection", b"keep-alive"),
            ],
        }
    )

    tick = 0
    last_message = time.monotonic()

    try:
        while True:
            now = time.monotonic()
            tick += 1

            # Heartbeat — keeps the connection alive and proves streaming works.
            chunk = _sse_event("heartbeat", json.dumps({"tick": tick}), event_id=tick)

            # Periodic message with richer payload.
            if now - last_message >= _MESSAGE_INTERVAL:
                last_message = now
                payload = json.dumps(
                    {
                        "tick": tick,
                        "uptime_s": round(now, 1),
                        "note": "hello from pounce SSE",
                    }
                )
                chunk += _sse_event("message", payload, event_id=tick)

            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": True,
                }
            )
            await asyncio.sleep(_HEARTBEAT_INTERVAL)

    except asyncio.CancelledError, ConnectionError, OSError:
        # Client disconnected — graceful exit.
        pass

    # Final empty body to close the response.
    await send(
        {
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        }
    )
