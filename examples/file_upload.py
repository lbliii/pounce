"""
File upload with backpressure — showcasing pounce's flow control.

Accepts file uploads via ``POST /upload`` and reports statistics about
the transfer: bytes received, chunk count, and transfer time.  Serves
an HTML upload form on ``GET /``.

This example makes pounce's **backpressure mechanism** visible.  When a
client sends data faster than the server can write the response (or
faster than a slow downstream can consume it), pounce automatically
pauses reading from the kernel socket buffer:

    Client sends bytes
      -> kernel receive buffer
        -> pounce reads into asyncio StreamReader
          -> ASGI app ``receive()`` delivers chunks
            -> app processes chunk
              -> ``send()`` writes to asyncio StreamWriter
                -> if write buffer > 64 KB, ``await drain()``
                  -> pauses until kernel flushes to client

This prevents unbounded memory growth for streaming responses with
slow clients, without penalising small responses.

Run it:
    pounce examples.file_upload:app --server-timing

Then open http://127.0.0.1:8000/ in a browser and upload a file, or:

    # Upload a 10 MB random file
    dd if=/dev/urandom bs=1M count=10 2>/dev/null | \\
        curl -X POST -H "Content-Type: application/octet-stream" \\
             --data-binary @- http://127.0.0.1:8000/upload

    # Upload an existing file
    curl -X POST -H "Content-Type: application/octet-stream" \\
         --data-binary @largefile.bin http://127.0.0.1:8000/upload

The ``--server-timing`` flag injects a ``Server-Timing`` response
header so you can see parse and processing time in the browser
DevTools Network panel.

"""

import json
import time
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

# ---------------------------------------------------------------------------
# Embedded HTML upload page
# ---------------------------------------------------------------------------

_HTML = b"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pounce file upload</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: system-ui, -apple-system, sans-serif;
    background: #0f172a; color: #e2e8f0;
    display: flex; flex-direction: column; align-items: center;
    min-height: 100vh; padding: 2rem 1rem;
  }
  h1 { font-size: 1.5rem; color: #38bdf8; margin-bottom: 0.5rem; }
  p.sub { font-size: 0.85rem; color: #94a3b8; margin-bottom: 1.5rem; }
  .card {
    background: #1e293b; border: 1px solid #334155;
    border-radius: 0.75rem; padding: 1.5rem;
    width: 100%; max-width: 480px;
  }
  input[type="file"] {
    display: block; width: 100%; margin-bottom: 1rem;
    color: #e2e8f0; font-size: 0.9rem;
  }
  button {
    padding: 0.5rem 1.5rem;
    border: none; border-radius: 0.375rem;
    background: #0ea5e9; color: #fff;
    font-size: 0.9rem; cursor: pointer;
  }
  button:hover { background: #38bdf8; }
  button:disabled { opacity: 0.5; cursor: default; }
  #progress {
    margin-top: 1rem; font-size: 0.85rem; color: #94a3b8;
    min-height: 1.25rem;
  }
  #result {
    margin-top: 1rem; padding: 0.75rem;
    background: #0f172a; border-radius: 0.375rem;
    font-family: ui-monospace, monospace;
    font-size: 0.8rem; white-space: pre-wrap;
    display: none;
  }
</style>
</head>
<body>
<h1>pounce file upload</h1>
<p class="sub">Upload a file to see chunked body reading and backpressure stats.</p>
<div class="card">
  <input type="file" id="file" />
  <button id="btn" onclick="upload()">Upload</button>
  <div id="progress"></div>
  <div id="result"></div>
</div>
<script>
async function upload() {
  const file = document.getElementById("file").files[0];
  if (!file) return;
  const btn = document.getElementById("btn");
  const prog = document.getElementById("progress");
  const res = document.getElementById("result");
  btn.disabled = true;
    prog.textContent = "uploading " + (file.size / 1024).toFixed(1) + " KB\\u2026";
  res.style.display = "none";
  try {
    const resp = await fetch("/upload", {
      method: "POST",
      headers: {"Content-Type": "application/octet-stream"},
      body: file,
    });
    const data = await resp.json();
    prog.textContent = "done";
    res.style.display = "block";
    res.textContent = JSON.stringify(data, null, 2);
  } catch (e) {
    prog.textContent = "error: " + e.message;
  } finally {
    btn.disabled = false;
  }
}
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_response(data: dict[str, object]) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    """Encode *data* as JSON and return ``(body, headers)``."""
    body = json.dumps(data, indent=2).encode()
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
    ]
    return body, headers


# ---------------------------------------------------------------------------
# ASGI app
# ---------------------------------------------------------------------------


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """File upload endpoint with chunked body reading.

    - ``GET /`` — HTML upload form.
    - ``POST /upload`` — accept a file upload, return stats as JSON.
    - Everything else — 404.
    """
    # --- Lifespan -----------------------------------------------------------
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    # --- HTTP ---------------------------------------------------------------
    assert scope["type"] == "http"

    method = scope["method"]
    path = scope["path"]

    # GET / — serve the upload form
    if method == "GET" and path == "/":
        await receive()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                    (b"content-length", str(len(_HTML)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": _HTML})
        return

    # POST /upload — accept the upload
    if method == "POST" and path == "/upload":
        start = time.monotonic()
        total_bytes = 0
        chunk_count = 0

        # Read the request body in chunks.  Each ``receive()`` call
        # returns one chunk from the client.  Pounce streams these
        # directly from the socket — no buffering of the full body.
        while True:
            message = await receive()
            body_chunk = message.get("body", b"")
            total_bytes += len(body_chunk)
            if body_chunk:
                chunk_count += 1
            if not message.get("more_body", False):
                break

        elapsed = time.monotonic() - start
        throughput = (total_bytes / elapsed / 1_048_576) if elapsed > 0 else 0.0

        result: dict[str, object] = {
            "bytes_received": total_bytes,
            "chunks": chunk_count,
            "elapsed_s": round(elapsed, 4),
            "throughput_mbps": round(throughput, 2),
            "note": (
                "Pounce streams the request body in chunks.  If the "
                "server's response write buffer exceeds 64 KB, it "
                "automatically drains (pauses) until the kernel flushes "
                "data to the client — preventing unbounded memory growth."
            ),
        }

        body, headers = _json_response(result)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})
        return

    # Everything else — 404
    await receive()
    body, headers = _json_response({"error": "not found"})
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": headers,
        }
    )
    await send({"type": "http.response.body", "body": body})
