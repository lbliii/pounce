"""
Compression demonstration — shows pounce's automatic zstd/gzip negotiation.

Pounce automatically compresses responses larger than ``compression_min_size``
(default 500 bytes) when the client sends an ``Accept-Encoding`` header.
The encoding priority is **zstd > gzip > identity**.

This app returns a ~2 KB JSON payload so compression activates.  You don't
need to do anything in your app code — pounce handles negotiation and
encoding transparently.

Run it:
    pounce serve --app examples.compression_demo:app

Test compression:
    # zstd (preferred — Python 3.14 stdlib, PEP 784)
    curl -s -H "Accept-Encoding: zstd" -o /dev/null -w "size: %{size_download}" \\
        http://127.0.0.1:8000/

    # gzip
    curl -s -H "Accept-Encoding: gzip" -o /dev/null -w "size: %{size_download}" \\
        http://127.0.0.1:8000/

    # no compression
    curl -s -H "Accept-Encoding: identity" -o /dev/null -w "size: %{size_download}" \\
        http://127.0.0.1:8000/

Observe the ``Content-Encoding`` response header to see which encoding was
selected.  The uncompressed payload is ~2 KB; zstd typically compresses it
to ~300-400 bytes.

"""

import json
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

# ---------------------------------------------------------------------------
# Build a large-ish JSON payload (> 500 bytes to exceed compression_min_size)
# ---------------------------------------------------------------------------

_DATA: dict[str, Any] = {
    "server": "pounce",
    "feature": "automatic content-encoding negotiation",
    "supported_encodings": ["zstd", "gzip", "identity"],
    "notes": {
        "zstd": (
            "New in Python 3.14 stdlib (PEP 784). Best ratio and speed. "
            "Pounce uses compression.zstd — zero extra dependencies."
        ),
        "gzip": (
            "Universal fallback. Pounce uses stdlib zlib with wbits=31 for gzip-format output."
        ),
        "identity": "No compression. Used when the response is small or the client declines.",
    },
    "how_it_works": [
        "1. Client sends Accept-Encoding header (e.g., 'gzip, zstd')",
        "2. Pounce parses q-values and selects the best supported encoding",
        "3. A fresh compressor is created per-request (thread-safe, no shared state)",
        "4. Response body chunks are compressed on the fly via the streaming pipeline",
        "5. Content-Encoding header is set automatically",
    ],
    "config": {
        "compression": "bool — enable/disable (default: True)",
        "compression_min_size": "int — skip compression below this size (default: 500 bytes)",
    },
}

_BODY = json.dumps(_DATA, indent=2).encode()

# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Return a JSON payload large enough for pounce to compress."""
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

    # NOTE: We set content-length to the *uncompressed* size here.  Pounce
    # replaces it with the compressed size and adds Content-Encoding when
    # compression is active.  If you omit content-length entirely, pounce
    # uses chunked transfer-encoding instead.
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(_BODY)).encode()),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": _BODY,
        }
    )
