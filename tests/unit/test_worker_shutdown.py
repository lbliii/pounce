"""Tests for Worker shutdown coordination and backpressure (Phase 2)."""

import asyncio
import socket
import threading

import pytest

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker
from tests.conftest import _wait_for_ready

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _hello_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal ASGI app for testing."""
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
    body = b"Hello, World!"
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})

def _start_worker(
    app: ASGIApp,
    config: ServerConfig | None = None,
    *,
    worker_id: int = 0,
    shutdown_event: threading.Event | None = None,
    max_connections: int = 0,
) -> tuple[Worker, socket.socket, threading.Thread]:
    """Start a worker in a background thread with Phase 2 params."""
    if config is None:
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
    sock = create_listener(config)
    worker = Worker(
        config,
        app,
        sock,
        worker_id=worker_id,
        shutdown_event=shutdown_event,
        max_connections=max_connections,
    )
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    _wait_for_ready(sock.getsockname())
    return worker, sock, thread

def _send_request(addr: tuple[str, int]) -> bytes:
    """Send a simple GET request and return the response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect(addr)
        sock.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
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

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWorkerExternalShutdown:
    """Worker responds to an external threading.Event for shutdown."""

    def test_external_event_shuts_down_worker(self):
        ext_event = threading.Event()
        worker, sock, thread = _start_worker(
            _hello_app, shutdown_event=ext_event,
        )
        addr = sock.getsockname()

        try:
            # Worker should be serving
            response = _send_request(addr)
            assert b"200" in response

            # Trigger shutdown via external event
            ext_event.set()
            thread.join(timeout=3.0)
            assert not thread.is_alive()
        finally:
            sock.close()

    def test_internal_shutdown_still_works(self):
        """Without external event, worker.shutdown() works as before."""
        worker, sock, thread = _start_worker(_hello_app)
        addr = sock.getsockname()

        try:
            response = _send_request(addr)
            assert b"200" in response

            worker.shutdown()
            thread.join(timeout=3.0)
            assert not thread.is_alive()
        finally:
            sock.close()

class TestWorkerIdentity:
    """Worker ID is used for log differentiation."""

    def test_worker_id_default(self):
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        try:
            worker = Worker(config, _hello_app, sock, worker_id=0)
            assert worker._worker_id == 0
            assert "0" in worker._logger.name
        finally:
            sock.close()

    def test_worker_id_custom(self):
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        try:
            worker = Worker(config, _hello_app, sock, worker_id=7)
            assert worker._worker_id == 7
            assert "7" in worker._logger.name
        finally:
            sock.close()

class TestWorkerBackpressure:
    """Worker rejects connections when at capacity."""

    def test_accepts_under_limit(self):
        worker, sock, thread = _start_worker(
            _hello_app, max_connections=100,
        )
        addr = sock.getsockname()

        try:
            response = _send_request(addr)
            assert b"200" in response
        finally:
            worker.shutdown()
            thread.join(timeout=3.0)
            sock.close()

    def test_no_limit_when_zero(self):
        """max_connections=0 means unlimited."""
        worker, sock, thread = _start_worker(
            _hello_app, max_connections=0,
        )
        addr = sock.getsockname()

        try:
            response = _send_request(addr)
            assert b"200" in response
        finally:
            worker.shutdown()
            thread.join(timeout=3.0)
            sock.close()

class TestWorkerBridgeShutdown:
    """The bridge task translates threading.Event to asyncio shutdown."""

    def test_bridge_detects_external_event(self):
        """Worker stops when external threading.Event is set."""
        ext_event = threading.Event()
        worker, sock, thread = _start_worker(
            _hello_app, shutdown_event=ext_event,
        )
        addr = sock.getsockname()

        try:
            # Confirm it's serving
            response = _send_request(addr)
            assert b"200" in response

            # Set the external event (simulates supervisor shutdown)
            ext_event.set()
            thread.join(timeout=3.0)
            assert not thread.is_alive()
        finally:
            sock.close()

    def test_bridge_not_created_without_external_event(self):
        """Without an external event, no bridge task is created."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        try:
            worker = Worker(config, _hello_app, sock, worker_id=0)
            assert worker._ext_shutdown is None
        finally:
            sock.close()
