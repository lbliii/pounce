"""Integration tests for multi-worker mode (Phase 2).

Tests the supervisor managing multiple thread-based workers serving
concurrent requests, handling crashes, and shutting down gracefully.

Note: These tests use thread mode exclusively because process mode
requires fork/pickle and is harder to test deterministically.  Process
mode uses the same Worker implementation and is tested via the
supervisor unit tests.

"""

import asyncio
import contextlib
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from pounce._runtime import WorkerMode
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


_LARGE_BUFFERED_BODY = bytes(range(256)) * 4096  # 1 MiB, deterministic content


async def _large_buffered_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Return a buffered response larger than a constrained socket send buffer."""
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
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(_LARGE_BUFFERED_BODY)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": _LARGE_BUFFERED_BODY})


def _make_slow_app(started: threading.Event) -> ASGIApp:
    async def _app(scope: Scope, receive: Receive, send: Send) -> None:
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
        if scope["path"] == "/slow":
            started.set()
            await asyncio.sleep(0.3)
            body = b"slow ok"
        else:
            body = b"ok"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

    return _app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _send_request(addr: tuple[str, int]) -> bytes:
    """Send a simple GET request and return the response."""
    return send_raw_request(
        addr,
        b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
    )


def _send_path(addr: tuple[str, int], path: bytes) -> bytes:
    return send_raw_request(
        addr,
        b"GET " + path + b" HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
        timeout=5.0,
    )


def _wait_for_ok(addr: tuple[str, int], path: bytes, timeout: float = 5.0) -> bytes:
    deadline = time.monotonic() + timeout
    last_response = b""
    while time.monotonic() < deadline:
        last_response = _send_path(addr, path)
        if b"HTTP/1.1 200" in last_response:
            return last_response
        time.sleep(0.05)
    return last_response


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
    sockets = create_listeners(config, worker_count, shared=True)
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

    @pytest.mark.issue(312)
    def test_sync_worker_completes_large_buffered_response_under_backpressure(self):
        """A real partial ``sendmsg`` delivers every buffered response byte."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            workers=1,
            worker_mode="sync",
            access_log=False,
        )
        sockets = create_listeners(config, 1, shared=True)
        for listener in sockets:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        addr = sockets[0].getsockname()
        supervisor = Supervisor(config, _large_buffered_app, mode="thread")
        thread = threading.Thread(target=supervisor.run, args=(sockets,), daemon=True)
        thread.start()
        _wait_for_ready(addr)

        response = bytearray()
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(30.0)
        try:
            client.connect(addr)
            client.sendall(
                b"GET /catalog.json HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            )
            time.sleep(0.05)
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                response.extend(chunk)
                time.sleep(0.0005)
        finally:
            client.close()
            supervisor.shutdown()
            thread.join(timeout=5.0)
            for listener in set(sockets):
                with contextlib.suppress(OSError):
                    listener.close()

        head, separator, body = bytes(response).partition(b"\r\n\r\n")
        assert separator == b"\r\n\r\n"
        assert b"HTTP/1.1 200" in head
        assert f"content-length: {len(_LARGE_BUFFERED_BODY)}".encode() in head.lower()
        assert body == _LARGE_BUFFERED_BODY
        assert not thread.is_alive()


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

    def test_graceful_reload_waits_for_inflight_request(self):
        """In-flight requests finish while old workers drain during reload."""
        started = threading.Event()
        sup, sockets, thread, addr = _start_supervisor(_make_slow_app(started), 2)

        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                slow = ex.submit(_send_path, addr, b"/slow")
                assert started.wait(timeout=3.0)

                sup.graceful_reload()

                slow_response = slow.result(timeout=5.0)
                assert b"HTTP/1.1 200" in slow_response
                assert b"slow ok" in slow_response

            fresh_response = _wait_for_ok(addr, b"/")
            assert b"HTTP/1.1 200" in fresh_response
            assert b"ok" in fresh_response
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

    Thread workers are forced via monkeypatch because GIL builds
    auto-select process workers, which this module does not target.
    """

    def test_server_shutdown_fires_lifespan_events(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """server.shutdown() triggers coordinated multi-worker shutdown
        with lifespan startup and shutdown events in order."""
        monkeypatch.setattr("pounce.server.detect_worker_mode", lambda: WorkerMode.THREAD)
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

        server.shutdown()
        thread.join(timeout=10.0)

        assert not thread.is_alive(), "Server did not stop after shutdown()"
        assert events == ["startup", "shutdown"]

    def test_server_shutdown_no_orphaned_threads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After shutdown, no pounce worker threads remain alive."""
        monkeypatch.setattr("pounce.server.detect_worker_mode", lambda: WorkerMode.THREAD)
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

        server.shutdown()
        thread.join(timeout=10.0)

        pounce_threads = [
            t for t in threading.enumerate() if t.name.startswith("pounce-worker-") and t.is_alive()
        ]
        assert pounce_threads == [], f"Orphaned worker threads: {[t.name for t in pounce_threads]}"

    def test_server_shutdown_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Calling shutdown() multiple times does not raise."""
        monkeypatch.setattr("pounce.server.detect_worker_mode", lambda: WorkerMode.THREAD)
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

        server.shutdown()
        server.shutdown()
        server.shutdown()
        thread.join(timeout=10.0)

        assert not thread.is_alive()
