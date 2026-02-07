"""Integration tests for pounce.server — full server lifecycle."""

import asyncio
import threading
import time

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.asgi.lifespan import run_lifespan
from pounce.config import ServerConfig
from pounce.logging import configure_logging
from pounce.net.listener import create_listener
from pounce.worker import Worker

from tests.conftest import send_raw_request


# -- Lifespan-tracking app (server-specific) --------------------------------


_lifespan_events: list[str] = []


async def _lifespan_tracking_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that records lifespan events to a global list."""
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


# -- Helper to run a full server in background ----------------------------


def _run_server_background(
    app: ASGIApp,
    config: ServerConfig,
) -> tuple[tuple[str, int], threading.Event, threading.Thread]:
    """Start an async server (with lifespan) in a background thread.

    Returns (addr, stop_event, thread).
    """
    sock = create_listener(config)
    addr = sock.getsockname()
    stop_event = threading.Event()

    def _run() -> None:
        async def _serve() -> None:
            configure_logging(config)
            worker = Worker(config, app, sock)
            async with run_lifespan(app, config):
                srv = await asyncio.start_server(
                    worker._handle_connection, sock=sock
                )
                while not stop_event.is_set():
                    await asyncio.sleep(0.05)
                srv.close()
                await srv.wait_closed()

        asyncio.run(_serve())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    time.sleep(0.3)
    return addr, stop_event, thread


class TestServerLifecycle:
    """Server start, serve, and stop lifecycle."""

    def test_start_and_respond(self, hello_app: ASGIApp):
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        addr, stop, thread = _run_server_background(hello_app, config)

        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            assert b"Hello, World!" in response
        finally:
            stop.set()
            thread.join(timeout=3)

    def test_lifespan_events_fire(self):
        global _lifespan_events
        _lifespan_events = []

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        addr, stop, thread = _run_server_background(_lifespan_tracking_app, config)

        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            assert "startup" in _lifespan_events
        finally:
            stop.set()
            thread.join(timeout=3)

        assert "startup" in _lifespan_events
        assert "shutdown" in _lifespan_events
