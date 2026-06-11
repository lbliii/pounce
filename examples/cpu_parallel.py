"""
CPU-bound parallel workload — the free-threading showcase.

This is the "marketing screenshot" for pounce.  Each request does real
CPU work (iterative hashing) inside the ASGI handler.  On Python 3.14t
with multiple ``--workers``, pounce runs these handlers in parallel
across OS threads sharing a single interpreter — true parallelism with
no fork, no IPC, no duplicated memory.

Run it:
    # Single worker — baseline
    pounce serve --app examples.cpu_parallel:app --workers 1 --no-access-log

    # Multi-worker — threads on 3.14t, processes on GIL builds
    pounce serve --app examples.cpu_parallel:app --workers 4 --no-access-log

Benchmark:
    wrk -t4 -c100 -d10s http://127.0.0.1:8000/

    # or
    hey -n 5000 -c 50 http://127.0.0.1:8000/

Compare req/s between 1 and 4 workers.  On 3.14t you should see near-linear
scaling because threads share the interpreter and run without the GIL.

"""

import hashlib
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

# Number of hash iterations per request.  Tune this to control how much
# CPU each request consumes.  1000 iterations ≈ 0.5-1ms on modern hardware.
_ITERATIONS = 1000


def _cpu_work(iterations: int) -> bytes:
    """Burn CPU by iteratively hashing.

    Returns the final digest as hex bytes.  This is pure computation —
    no I/O, no shared state — so it benefits directly from parallel
    threads on 3.14t.
    """
    digest = b"pounce"
    for _ in range(iterations):
        digest = hashlib.sha256(digest).digest()
    return digest.hex().encode()


# Pre-build the response template
_PREFIX = b'{"iterations": ' + str(_ITERATIONS).encode() + b', "digest": "'
_SUFFIX = b'"}\n'


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Do CPU work per request, return the result as JSON."""
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

    # --- CPU work (this is the part that parallelises on 3.14t) ---
    hex_digest = _cpu_work(_ITERATIONS)
    body = _PREFIX + hex_digest + _SUFFIX

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
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
