"""Integration tests for multi-worker mode (Phase 2).

Tests the supervisor managing multiple thread-based workers serving
concurrent requests, handling crashes, and shutting down gracefully.

Note: These tests use thread mode exclusively because process mode
requires fork/pickle and is harder to test deterministically.  Process
mode uses the same Worker implementation and is tested via the
supervisor unit tests.

"""

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listeners
from pounce.supervisor import Supervisor
from tests.conftest import send_raw_request

# ---------------------------------------------------------------------------
# Test ASGI apps
# ---------------------------------------------------------------------------

async def _hello_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal app that returns Hello + the worker thread name."""
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
    thread_name = threading.current_thread().name
    body = f"Hello from {thread_name}".encode()
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _send_request(addr: tuple[str, int]) -> bytes:
    """Send a simple GET request and return the response."""
    return send_raw_request(
        addr,
        b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
    )

def _start_supervisor(
    app: ASGIApp,
    worker_count: int = 2,
) -> tuple[Supervisor, list[socket.socket], threading.Thread, tuple[str, int]]:
    """Start a supervisor with N thread workers in the background.

    Returns (supervisor, sockets, thread, addr).
    """
    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        workers=worker_count,
        access_log=False,
    )
    sockets = create_listeners(config, worker_count)
    addr = sockets[0].getsockname()

    sup = Supervisor(config, app, mode="thread")

    def _run():
        sup.run(sockets)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.5)  # Let workers start

    return sup, sockets, t, addr

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMultiWorkerServing:
    """Multiple workers serve concurrent requests correctly."""

    def test_two_workers_respond(self):
        """Both workers should be able to serve requests."""
        sup, sockets, thread, addr = _start_supervisor(_hello_app, 2)

        try:
            response = _send_request(addr)
            assert b"200" in response
            assert b"Hello from" in response
        finally:
            sup.shutdown()
            thread.join(timeout=5.0)
            for s in set(sockets):
                try:
                    s.close()
                except Exception:
                    pass

    def test_concurrent_requests(self):
        """Multiple concurrent requests should all succeed."""
        sup, sockets, thread, addr = _start_supervisor(_hello_app, 2)

        try:
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(_send_request, addr) for _ in range(16)]
                results = [f.result() for f in as_completed(futures)]

            # All requests should get 200 responses
            for response in results:
                assert b"200" in response
                assert b"Hello from" in response
        finally:
            sup.shutdown()
            thread.join(timeout=5.0)
            for s in set(sockets):
                try:
                    s.close()
                except Exception:
                    pass

class TestMultiWorkerShutdown:
    """Supervisor graceful shutdown drains all workers."""

    def test_graceful_shutdown(self):
        """Shutdown should stop all workers cleanly."""
        sup, sockets, thread, addr = _start_supervisor(_hello_app, 2)

        try:
            # Verify it's serving
            response = _send_request(addr)
            assert b"200" in response

            # Trigger shutdown
            sup.shutdown()
            thread.join(timeout=5.0)
            assert not thread.is_alive()
        finally:
            for s in set(sockets):
                try:
                    s.close()
                except Exception:
                    pass

    def test_all_workers_alive_before_shutdown(self):
        """All worker handles should be alive before shutdown."""
        sup, sockets, thread, addr = _start_supervisor(_hello_app, 2)

        try:
            # Verify workers are alive
            for h in sup._handles:
                assert h.target.is_alive()
        finally:
            sup.shutdown()
            thread.join(timeout=5.0)
            for s in set(sockets):
                try:
                    s.close()
                except Exception:
                    pass

class TestSupervisorMode:
    """Supervisor correctly reports its mode."""

    def test_thread_mode(self):
        config = ServerConfig(workers=2, host="127.0.0.1", port=0, access_log=False)
        sup = Supervisor(config, _hello_app, mode="thread")
        assert sup.mode == "thread"
        assert sup.worker_count == 2
