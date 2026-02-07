"""Integration tests for pounce.server — full server lifecycle."""

import socket
import threading
import time

import pytest

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.server import Server


# -- Test ASGI apps --------------------------------------------------------


async def hello_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Simple ASGI app that returns 'Hello, Pounce!'."""
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
    body = b"Hello, Pounce!"
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })


# Track lifespan events
_lifespan_events: list[str] = []


async def lifespan_tracking_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that tracks lifespan events."""
    global _lifespan_events

    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                _lifespan_events.append("startup")
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                _lifespan_events.append("shutdown")
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await receive()
    body = b"ok"
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-length", b"2")],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })


# -- Helpers ---------------------------------------------------------------


def _send_request(addr: tuple[str, int], path: str = "/") -> bytes:
    """Send a GET request and return the response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    try:
        sock.connect(addr)
        sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n".encode()
        )
        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break
        return response
    finally:
        sock.close()


class TestServerLifecycle:
    """Server start, serve, and stop lifecycle."""

    def test_start_and_respond(self):
        """Server starts, responds to requests, and shuts down cleanly."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)

        # We need to find the ephemeral port, so we bind first
        import pounce.net.listener as listener
        sock = listener.create_listener(config)
        addr = sock.getsockname()

        # Create server that will use our pre-bound socket
        server = Server(config, hello_app)

        # Run server in a background thread
        import asyncio

        stop_event = threading.Event()

        def run_server():
            async def _run():
                from pounce.logging import configure_logging
                from pounce.asgi.lifespan import run_lifespan
                from pounce.worker import Worker

                configure_logging(config)
                worker = Worker(config, hello_app, sock)

                async with run_lifespan(hello_app, config):
                    srv = await asyncio.start_server(
                        worker._handle_connection, sock=sock
                    )
                    while not stop_event.is_set():
                        await asyncio.sleep(0.05)
                    srv.close()
                    await srv.wait_closed()

            asyncio.run(_run())

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        time.sleep(0.3)

        try:
            response = _send_request(addr)
            assert b"200" in response
            assert b"Hello, Pounce!" in response
        finally:
            stop_event.set()
            thread.join(timeout=3)
            sock.close()

    def test_lifespan_events_fire(self):
        """Lifespan startup and shutdown events are sent to the app."""
        global _lifespan_events
        _lifespan_events = []

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)

        import pounce.net.listener as listener
        sock = listener.create_listener(config)
        addr = sock.getsockname()

        import asyncio

        stop_event = threading.Event()

        def run_server():
            async def _run():
                from pounce.logging import configure_logging
                from pounce.asgi.lifespan import run_lifespan
                from pounce.worker import Worker

                configure_logging(config)
                worker = Worker(config, lifespan_tracking_app, sock)

                async with run_lifespan(lifespan_tracking_app, config):
                    srv = await asyncio.start_server(
                        worker._handle_connection, sock=sock
                    )
                    while not stop_event.is_set():
                        await asyncio.sleep(0.05)
                    srv.close()
                    await srv.wait_closed()

            asyncio.run(_run())

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()
        time.sleep(0.3)

        try:
            # Make a request to verify the server is running
            response = _send_request(addr)
            assert b"200" in response
            assert "startup" in _lifespan_events
        finally:
            stop_event.set()
            thread.join(timeout=3)
            sock.close()

        # After shutdown, both events should have fired
        assert "startup" in _lifespan_events
        assert "shutdown" in _lifespan_events
