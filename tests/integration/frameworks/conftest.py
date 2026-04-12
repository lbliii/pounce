"""
Shared fixtures for framework compatibility tests.

Provides a real pounce Worker serving a framework app, plus an httpx
client pointed at it.  Framework tests import these fixtures and call
``pytest.importorskip()`` for their framework dependency.

Usage::

    starlette = pytest.importorskip("starlette")

    def test_basic_route(pounce_server, http_client):
        app = make_starlette_app()
        host, port = pounce_server(app)
        resp = http_client.get(f"http://{host}:{port}/")
        assert resp.status_code == 200

"""

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import Callable, Generator

import httpx
import pytest

from pounce._types import ASGIApp
from pounce.asgi.lifespan import run_lifespan
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker


def _wait_for_ready(addr: tuple[str, int], *, timeout: float = 5.0) -> None:
    """Retry-connect until the worker is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(addr, timeout=0.5):
                return
        except ConnectionRefusedError, OSError:
            time.sleep(0.02)
    msg = f"Worker at {addr} did not become ready within {timeout}s"
    raise RuntimeError(msg)


def _run_lifespan_startup(
    app: ASGIApp, config: ServerConfig
) -> tuple[dict, contextlib.AbstractAsyncContextManager, asyncio.AbstractEventLoop]:
    """Run ASGI lifespan startup synchronously and return startup resources.

    Returns a 3-tuple ``(state, ctx, loop)`` where ``state`` is the lifespan
    state populated by the app during startup, ``ctx`` is the open lifespan
    context manager, and ``loop`` is the event loop used to run startup.
    """
    loop = asyncio.new_event_loop()
    state = {}

    async def _startup():
        ctx = run_lifespan(app, config)
        lifespan_state = await ctx.__aenter__()
        state.update(lifespan_state)
        return ctx

    ctx = loop.run_until_complete(_startup())
    return state, ctx, loop


class PounceTestServer:
    """Manages a pounce Worker serving a framework app."""

    def __init__(self) -> None:
        self._workers: list[tuple[Worker, socket.socket, threading.Thread]] = []
        self._lifespans: list[tuple[object, asyncio.AbstractEventLoop]] = []

    def start(
        self,
        app: ASGIApp,
        *,
        config: ServerConfig | None = None,
    ) -> tuple[str, int]:
        """Start a pounce worker serving *app* and return ``(host, port)``.

        Runs ASGI lifespan startup before accepting connections, matching
        the real Server behavior.
        """
        if config is None:
            config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)

        # Run lifespan startup to populate state
        lifespan_state, ctx, loop = _run_lifespan_startup(app, config)
        self._lifespans.append((ctx, loop))

        sock = create_listener(config)
        addr = sock.getsockname()
        worker = Worker(config, app, sock, worker_id=0)
        worker.set_lifespan_state(lifespan_state)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        _wait_for_ready(addr)
        self._workers.append((worker, sock, thread))
        return addr[0], addr[1]

    def shutdown_all(self) -> None:
        """Shut down all started workers and run lifespan shutdown."""
        for worker, sock, thread in self._workers:
            worker.shutdown()
            thread.join(timeout=3)
            sock.close()
        self._workers.clear()

        for ctx, loop in self._lifespans:
            with contextlib.suppress(Exception):
                loop.run_until_complete(ctx.__aexit__(None, None, None))
            loop.close()
        self._lifespans.clear()


@pytest.fixture
def pounce_server() -> Generator[Callable[..., tuple[str, int]]]:
    """Fixture that provides a callable to start a pounce server.

    Runs ASGI lifespan startup before the worker begins accepting
    connections, matching real Server behavior. Lifespan shutdown
    runs automatically after the test.

    Returns a function: ``start(app, *, config=None) -> (host, port)``.

    Example::

        def test_my_app(pounce_server):
            host, port = pounce_server(my_asgi_app)
            # ... test against http://{host}:{port}

    """
    server = PounceTestServer()
    yield server.start
    server.shutdown_all()


@pytest.fixture
def http_client() -> Generator[httpx.Client]:
    """Synchronous httpx client for framework tests.

    Configured with short timeouts appropriate for local testing.
    """
    with httpx.Client(timeout=httpx.Timeout(5.0, connect=2.0)) as client:
        yield client
