"""
Programmatic server control — start, serve, and shut down from code.

Most examples use ``pounce serve --app myapp:app`` on the command line.  This
example
shows the ``pounce.Server`` class directly, which is useful for:

- **Embedding** pounce inside a larger application
- **Testing** — start a real server, hit it with HTTP, then shut it down
- **Graceful shutdown** — call ``server.shutdown()`` from any thread

The hardening work in 0.2.0 added proper connection draining: on shutdown,
the server stops accepting new connections and waits up to
``shutdown_timeout`` seconds for active requests to finish before
force-closing.

Run it:
    python examples/programmatic_server.py

The server starts, handles one request (via curl or browser at
http://127.0.0.1:8000/), then shuts down after 3 seconds.

"""

import threading
import time
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any


async def app(scope: Scope, receive: Receive, send: Send) -> None:
    """Simple app for the demo."""
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
    body = b"Hello from programmatic server!\n"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def main() -> None:
    """Start the server, let it run for a few seconds, then shut down."""
    from pounce import ServerConfig
    from pounce.server import Server

    config = ServerConfig(
        host="127.0.0.1",
        port=8000,
        shutdown_timeout=5.0,  # Wait up to 5s for active connections to drain
    )

    server = Server(config, app)

    # Run the server in a background thread so we can control it.
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    print(f"Server running on http://{config.host}:{config.port}")
    print("Shutting down in 3 seconds... (try: curl http://127.0.0.1:8000/)")

    time.sleep(3)

    # Graceful shutdown — drains active connections before stopping.
    print("Calling server.shutdown()...")
    server.shutdown()
    thread.join(timeout=10)
    print("Server stopped.")


if __name__ == "__main__":
    main()
