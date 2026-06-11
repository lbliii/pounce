"""
First-class testing utilities for pounce.

Provides ``TestServer`` for running a real pounce server in tests — works with
httpx, Playwright, Selenium, or any HTTP client.

Usage::

    from pounce.testing import TestServer

    def test_homepage():
        with TestServer(app) as server:
            resp = httpx.get(f"{server.url}/")
            assert resp.status_code == 200

When pounce is installed, the ``pounce_server`` pytest fixture is auto-registered
via the ``pytest11`` entry point.

"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from pounce._types import ASGIApp
from pounce.config import ServerConfig
from pounce.server import Server


class TestServer:
    """Run a real pounce server in a background thread for testing.

    Args:
        app: ASGI application callable.
        host: Bind address (default ``"127.0.0.1"``).
        port: Bind port (default ``0`` for ephemeral).
        **config_kwargs: Extra :class:`~pounce.config.ServerConfig` overrides.

    Example::

        with TestServer(app) as server:
            print(server.url)  # http://127.0.0.1:XXXXX

    """

    __test__ = False  # Prevent pytest from collecting this as a test class

    def __init__(
        self,
        app: ASGIApp,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        **config_kwargs: Any,
    ) -> None:
        # Force test-friendly defaults
        config_kwargs.update(
            host=host,
            port=port,
            workers=1,
            reload=False,
            access_log=False,
            log_level="warning",
        )
        config = ServerConfig(**config_kwargs)
        self._server = Server(config, app)
        self._thread: threading.Thread | None = None

    def start(self, *, timeout: float = 5.0) -> None:
        """Start the server in a background thread. Blocks until ready.

        Raises:
            RuntimeError: If the server fails to start within *timeout* seconds.

        """
        if self._thread is not None:
            raise RuntimeError("TestServer is already running")

        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

        if not self._server._started_event.wait(timeout=timeout):
            # Server didn't start — clean up
            self._server.shutdown()
            self._thread.join(timeout=2.0)
            self._thread = None
            raise RuntimeError(f"TestServer failed to start within {timeout}s")

    def stop(self, *, timeout: float = 5.0) -> None:
        """Gracefully stop the server and join the background thread."""
        if self._thread is None:
            return
        self._server.shutdown()
        self._thread.join(timeout=timeout)
        self._thread = None

    @property
    def host(self) -> str:
        """The host the server is bound to."""
        addr = self._server.bound_addr
        if addr is None:
            raise RuntimeError("Server has not started yet")
        return addr[0]

    @property
    def port(self) -> int:
        """The actual port the server is listening on (useful with ``port=0``)."""
        addr = self._server.bound_addr
        if addr is None:
            raise RuntimeError("Server has not started yet")
        return addr[1]

    @property
    def url(self) -> str:
        """Base URL for the running server (e.g. ``http://127.0.0.1:54321``)."""
        return f"http://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        """Whether the server background thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def __enter__(self) -> TestServer:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()

    async def __aenter__(self) -> TestServer:
        self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.stop()


@asynccontextmanager
async def serve(app: ASGIApp, **kwargs: Any) -> AsyncIterator[TestServer]:
    """Async context manager that starts and stops a :class:`TestServer`.

    Example::

        async with serve(app) as server:
            async with httpx.AsyncClient() as client:
                resp = await client.get(server.url)

    """
    server = TestServer(app, **kwargs)
    server.start()
    try:
        yield server
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# pytest plugin (auto-registered via pytest11 entry point)
# ---------------------------------------------------------------------------

try:
    import pytest

    @pytest.fixture
    def pounce_server():
        """Factory fixture — create test servers for different apps.

        Example::

            def test_api(pounce_server):
                with pounce_server(my_app) as server:
                    resp = httpx.get(f"{server.url}/health")

        """
        servers: list[TestServer] = []

        def factory(app: ASGIApp, **kwargs: Any) -> TestServer:
            s = TestServer(app, **kwargs)
            s.start()
            servers.append(s)
            return s

        yield factory

        for s in reversed(servers):
            s.stop()

except ImportError:
    pass
