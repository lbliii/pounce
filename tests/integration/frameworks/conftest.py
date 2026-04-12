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

import socket
import threading
import time
from collections.abc import Callable, Generator

import httpx
import pytest

from pounce._types import ASGIApp
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker


def _wait_for_ready(addr: tuple[str, int], *, timeout: float = 5.0) -> None:
    """Retry-connect until the worker is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.settimeout(0.5)
            probe.connect(addr)
            probe.close()
            return
        except (ConnectionRefusedError, OSError):
            time.sleep(0.02)
    msg = f"Worker at {addr} did not become ready within {timeout}s"
    raise RuntimeError(msg)


class PounceTestServer:
    """Manages a pounce Worker serving a framework app."""

    def __init__(self) -> None:
        self._workers: list[tuple[Worker, socket.socket, threading.Thread]] = []

    def start(
        self,
        app: ASGIApp,
        *,
        config: ServerConfig | None = None,
    ) -> tuple[str, int]:
        """Start a pounce worker serving *app* and return ``(host, port)``."""
        if config is None:
            config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
        sock = create_listener(config)
        addr = sock.getsockname()
        worker = Worker(config, app, sock, worker_id=0)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        _wait_for_ready(addr)
        self._workers.append((worker, sock, thread))
        return addr[0], addr[1]

    def shutdown_all(self) -> None:
        """Shut down all started workers."""
        for worker, sock, thread in self._workers:
            worker.shutdown()
            thread.join(timeout=3)
            sock.close()
        self._workers.clear()


@pytest.fixture
def pounce_server() -> Generator[Callable[..., tuple[str, int]]]:
    """Fixture that provides a callable to start a pounce server.

    Returns a function: ``start(app, *, config=None) -> (host, port)``.
    All started servers are shut down automatically after the test.

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
