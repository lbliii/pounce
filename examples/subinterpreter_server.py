"""
Subinterpreter worker mode example.

Demonstrates running pounce with subinterpreter workers (PEP 734).
Each worker runs in its own subinterpreter — thread-like performance
with process-like isolation, all in one process.

Requires Python 3.14+ with ``concurrent.interpreters`` (PEP 734).

Run it:
    pounce examples.subinterpreter_server:app --workers 4 --worker-mode subinterpreter

Or programmatically:
    python examples/subinterpreter_server.py

"""

import threading
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any

# Thread-safe counter — each subinterpreter gets its own copy
_lock = threading.Lock()
_request_count = 0


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """JSON API that reports which worker handled the request."""
    global _request_count

    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    if scope["type"] == "pounce.worker.startup":
        return
    if scope["type"] == "pounce.worker.shutdown":
        return

    await receive()

    with _lock:
        _request_count += 1
        count = _request_count

    # scope["server"] is (host, port), but worker_id isn't in scope
    # by default — this counter proves isolation (each interpreter
    # maintains its own _request_count)
    import json

    body = json.dumps(
        {
            "message": "Hello from subinterpreter worker!",
            "requests_in_this_worker": count,
        }
    ).encode()

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


if __name__ == "__main__":
    from pounce import Server
    from pounce.config import ServerConfig

    config = ServerConfig(
        host="127.0.0.1",
        port=8000,
        workers=4,
        worker_mode="subinterpreter",
    )
    server = Server(config, app=None, app_path="examples.subinterpreter_server:app")
    server.run()
