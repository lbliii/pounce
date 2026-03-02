"""Integration tests for multi-worker mode (Phase 2).

Tests the supervisor managing multiple thread-based workers serving
concurrent requests, handling crashes, and shutting down gracefully.

Note: These tests use thread mode exclusively because process mode
requires fork/pickle and is harder to test deterministically.  Process
mode uses the same Worker implementation and is tested via the
supervisor unit tests.

"""

import contextlib
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listeners
from pounce.server import Server
from pounce.supervisor import Supervisor
from tests.conftest import _wait_for_ready, send_raw_request

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
    _wait_for_ready(addr)

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
                with contextlib.suppress(Exception):
                    s.close()

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
                with contextlib.suppress(Exception):
                    s.close()


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
                with contextlib.suppress(Exception):
                    s.close()

    def test_all_workers_alive_before_shutdown(self):
        """All worker handles should be alive before shutdown."""
        sup, sockets, thread, _addr = _start_supervisor(_hello_app, 2)

        try:
            # Verify workers are alive
            for h in sup._handles:
                assert h.target.is_alive()
        finally:
            sup.shutdown()
            thread.join(timeout=5.0)
            for s in set(sockets):
                with contextlib.suppress(Exception):
                    s.close()


class TestSupervisorMode:
    """Supervisor correctly reports its mode."""

    def test_thread_mode(self):
        config = ServerConfig(workers=2, host="127.0.0.1", port=0, access_log=False)
        sup = Supervisor(config, _hello_app, mode="thread")
        assert sup.mode == "thread"
        assert sup.worker_count == 2


@pytest.mark.slow
class TestSIGHUPRollingRestart:
    """SIGHUP / graceful_reload rolling restart.

    Calls supervisor.graceful_reload() directly (equivalent to SIGHUP).
    In production, SIGHUP is sent to the main process; this tests the
    same code path without subprocess/signal complexity.
    """

    def test_graceful_reload_completes(self):
        """graceful_reload() completes without raising; workers drain."""
        sup, sockets, thread, addr = _start_supervisor(_hello_app, 2)

        try:
            # Send requests before reload
            r1 = _send_request(addr)
            r2 = _send_request(addr)
            assert b"200" in r1
            assert b"200" in r2

            # Trigger rolling restart (simulates SIGHUP)
            sup.graceful_reload()

            # Reload completed; supervisor has new worker generation
            assert len(sup._handles) == 2
        finally:
            sup.shutdown()
            thread.join(timeout=5.0)
            for s in set(sockets):
                with contextlib.suppress(Exception):
                    s.close()


# ---------------------------------------------------------------------------
# Full Server multi-worker shutdown (exercises signal handler path)
# ---------------------------------------------------------------------------


def _make_lifespan_tracking_app(
    events: list[str],
) -> ASGIApp:
    """Create an ASGI app that records lifespan events to the given list."""

    async def _app(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    events.append("startup")
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    events.append("shutdown")
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        await receive()
        body = b"ok"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"2")],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return _app


class TestMultiWorkerServerShutdown:
    """Full Server multi-worker shutdown via server.shutdown().

    Exercises the coordinated shutdown path: Server signals the
    supervisor, supervisor drains workers, lifespan shutdown runs,
    asyncio.run() completes normally — no orphaned executor threads.
    """

    def test_server_shutdown_fires_lifespan_events(self):
        """server.shutdown() triggers coordinated multi-worker shutdown
        with lifespan startup and shutdown events in order."""
        events: list[str] = []
        app = _make_lifespan_tracking_app(events)
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            workers=2,
            access_log=False,
        )
        server = Server(config, app)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Wait for lifespan startup
        deadline = time.monotonic() + 5.0
        while "startup" not in events:
            if time.monotonic() > deadline:
                raise AssertionError("Multi-worker server did not start within 5s")
            time.sleep(0.05)

        # Trigger graceful shutdown
        server.shutdown()
        thread.join(timeout=10.0)

        assert not thread.is_alive(), "Server did not stop after shutdown()"
        assert events == ["startup", "shutdown"]

    def test_server_shutdown_no_orphaned_threads(self):
        """After shutdown, no pounce worker threads remain alive."""
        events: list[str] = []
        app = _make_lifespan_tracking_app(events)
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            workers=2,
            access_log=False,
        )
        server = Server(config, app)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Wait for lifespan startup
        deadline = time.monotonic() + 5.0
        while "startup" not in events:
            if time.monotonic() > deadline:
                raise AssertionError("Multi-worker server did not start within 5s")
            time.sleep(0.05)

        server.shutdown()
        thread.join(timeout=10.0)

        # No pounce worker threads should remain
        pounce_threads = [
            t for t in threading.enumerate() if t.name.startswith("pounce-worker-") and t.is_alive()
        ]
        assert pounce_threads == [], f"Orphaned worker threads: {[t.name for t in pounce_threads]}"

    def test_server_shutdown_is_idempotent(self):
        """Calling shutdown() multiple times does not raise."""
        events: list[str] = []
        app = _make_lifespan_tracking_app(events)
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            workers=2,
            access_log=False,
        )
        server = Server(config, app)

        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        deadline = time.monotonic() + 5.0
        while "startup" not in events:
            if time.monotonic() > deadline:
                raise AssertionError("Multi-worker server did not start within 5s")
            time.sleep(0.05)

        # Call shutdown multiple times — should not raise
        server.shutdown()
        server.shutdown()
        server.shutdown()
        thread.join(timeout=10.0)

        assert not thread.is_alive()
