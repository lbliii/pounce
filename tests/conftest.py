"""
Shared test fixtures for pounce.

Provides reusable ASGI app fixtures and test utilities. All integration
tests should use these fixtures instead of defining their own apps.

"""

import asyncio
import functools
import socket
import threading
import time
from collections.abc import Callable, Generator

import pytest

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker

# ---------------------------------------------------------------------------
# Lifespan decorator — DRYs the 12-line lifespan boilerplate
# ---------------------------------------------------------------------------


def with_lifespan(handler: Callable[..., object]) -> ASGIApp:
    """Wrap a simple HTTP handler with standard lifespan support.

    The *handler* receives ``(scope, receive, send)`` and is only
    called for ``type == "http"`` scopes. Lifespan startup/shutdown
    is handled automatically.

    Example::

        @with_lifespan
        async def my_app(scope, receive, send):
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    """

    @functools.wraps(handler)
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
        if scope["type"] != "http":
            # Silently ignore unknown scope types (e.g. pounce.worker.startup)
            return
        await handler(scope, receive, send)

    return _app


# ---------------------------------------------------------------------------
# ASGI test apps (using @with_lifespan to avoid boilerplate)
# ---------------------------------------------------------------------------


@with_lifespan
async def _hello_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal ASGI app that returns 'Hello, World!'."""
    await receive()
    body = b"Hello, World!"
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
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )


@with_lifespan
async def _echo_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that echoes the request path and method."""
    await receive()
    body = f"{scope['method']} {scope['path']}".encode()
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
    await send(
        {
            "type": "http.response.body",
            "body": body,
        }
    )


@with_lifespan
async def _streaming_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that streams response body in chunks."""
    await receive()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"transfer-encoding", b"chunked"),
            ],
        }
    )
    for i in range(3):
        await send(
            {
                "type": "http.response.body",
                "body": f"chunk{i}".encode(),
                "more_body": i < 2,
            }
        )


@with_lifespan
async def _error_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that raises an exception."""
    raise RuntimeError("App crashed!")


@with_lifespan
async def _sse_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that streams SSE events until the client disconnects."""
    await receive()

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream"),
                (b"cache-control", b"no-cache"),
                (b"connection", b"keep-alive"),
            ],
        }
    )

    tick = 0
    try:
        while True:
            chunk = f"data: tick {tick}\n\n".encode()
            await send(
                {
                    "type": "http.response.body",
                    "body": chunk,
                    "more_body": True,
                }
            )
            tick += 1
            await asyncio.sleep(0.05)
    except asyncio.CancelledError, ConnectionError, OSError:
        pass

    await send(
        {
            "type": "http.response.body",
            "body": b"",
            "more_body": False,
        }
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def hello_app() -> ASGIApp:
    """Minimal ASGI app that returns 'Hello, World!'."""
    return _hello_app


@pytest.fixture
def echo_app() -> ASGIApp:
    """ASGI app that echoes method and path."""
    return _echo_app


@pytest.fixture
def streaming_app() -> ASGIApp:
    """ASGI app that streams chunked responses."""
    return _streaming_app


@pytest.fixture
def error_app() -> ASGIApp:
    """ASGI app that always raises."""
    return _error_app


@pytest.fixture
def sse_app() -> ASGIApp:
    """ASGI app that streams SSE events."""
    return _sse_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_ready(addr: tuple[str, int], *, timeout: float = 3.0) -> None:
    """Retry-connect until the worker is accepting connections.

    Replaces the old ``time.sleep(0.15)`` which was flaky on slow CI.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.settimeout(0.5)
            probe.connect(addr)
            probe.close()
            return
        except ConnectionRefusedError, OSError:
            time.sleep(0.02)
    msg = f"Worker at {addr} did not become ready within {timeout}s"
    raise RuntimeError(msg)


def send_raw_request(
    addr: tuple[str, int],
    request: bytes,
    timeout: float = 2.0,
) -> bytes:
    """Send a raw HTTP request and return the full response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(addr)
        sock.sendall(request)
        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            except TimeoutError:
                break
        return response
    finally:
        sock.close()


def start_worker(
    app: ASGIApp,
    config: ServerConfig | None = None,
) -> tuple[Worker, socket.socket, threading.Thread]:
    """Start a worker in a background thread.

    Uses retry-connect instead of sleep to wait for readiness.

    Returns:
        (worker, socket, thread) — caller must shut down and close.
    """
    if config is None:
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
    sock = create_listener(config)
    addr = sock.getsockname()
    worker = Worker(config, app, sock, worker_id=0)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    _wait_for_ready(addr)
    return worker, sock, thread


@pytest.fixture
def running_worker(request: pytest.FixtureRequest) -> Generator[tuple[Worker, tuple[str, int]]]:
    """Start a worker serving the given app and yield ``(worker, addr)``.

    Mark the test with ``@pytest.mark.parametrize("running_worker", [app], indirect=True)``
    or use ``request.param`` to pass the ASGI app.

    Automatically shuts down the worker and closes the socket on teardown.
    """
    app: ASGIApp = request.param
    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()
    try:
        yield worker, addr
    finally:
        worker.shutdown()
        thread.join(timeout=2)
        sock.close()
