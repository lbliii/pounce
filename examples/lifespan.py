"""
ASGI lifespan example — startup/shutdown hooks with shared state.

Demonstrates the ASGI lifespan protocol that every real application needs:
startup to initialise resources, shutdown to clean them up.  The shared
state uses ``threading.Lock`` so it is safe under pounce's free-threading
worker model.

Run it:
    pounce examples.lifespan:app

Then visit http://127.0.0.1:8000/ — each request increments a counter.

"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

log = logging.getLogger("examples.lifespan")

# ---------------------------------------------------------------------------
# Shared application state — thread-safe via Lock
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_request_count: int = 0
_started_at: float = 0.0


# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Count requests and report uptime.

    Lifespan hooks record the server start time and log on shutdown.
    Every HTTP request increments a shared counter (protected by a lock).
    """
    global _request_count, _started_at

    # --- Lifespan -----------------------------------------------------------
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                _started_at = time.monotonic()
                log.info("lifespan: started at monotonic %.3f", _started_at)
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                uptime = time.monotonic() - _started_at
                log.info(
                    "lifespan: shutting down after %.1fs, %d requests served",
                    uptime,
                    _request_count,
                )
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    # --- HTTP ---------------------------------------------------------------
    await receive()

    with _state_lock:
        _request_count += 1
        count = _request_count

    uptime = time.monotonic() - _started_at
    body = f"request #{count} | uptime {uptime:.1f}s\n".encode()

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )
