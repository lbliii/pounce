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

import asyncio
import contextlib
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

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


class RoundRobinTestProxy:
    """Pin each incoming TCP connection to the next test server.

    This intentionally small raw-TCP proxy preserves streaming responses and
    is suitable for multi-instance SSE integration tests. It is not a
    production reverse proxy and does not add forwarding headers.
    """

    __test__ = False

    def __init__(
        self,
        backends: Sequence[TestServer],
        *,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if not backends:
            raise ValueError(
                "RoundRobinTestProxy requires at least one backend TestServer configured "
                "before startup."
            )
        self._backends = tuple(backends)
        self._host = host
        self._port = port
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._started = threading.Event()
        self._bound_addr: tuple[str, int] | None = None
        self._start_error: BaseException | None = None
        self._next_backend = 0
        self._client_tasks: set[asyncio.Task[None]] = set()

    async def _proxy_connection(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._client_tasks.add(task)
        backend_writer: asyncio.StreamWriter | None = None
        try:
            backend = self._backends[self._next_backend % len(self._backends)]
            self._next_backend += 1
            backend_reader, backend_writer = await asyncio.open_connection(
                backend.host,
                backend.port,
            )

            async def copy(
                reader: asyncio.StreamReader,
                writer: asyncio.StreamWriter,
            ) -> None:
                try:
                    while data := await reader.read(65536):
                        writer.write(data)
                        await writer.drain()
                    if writer.can_write_eof():
                        writer.write_eof()
                except (ConnectionError, OSError):  # fmt: skip
                    return

            await asyncio.gather(
                copy(client_reader, backend_writer),
                copy(backend_reader, client_writer),
            )
        except (ConnectionError, OSError, RuntimeError):  # fmt: skip
            pass
        finally:
            if backend_writer is not None:
                backend_writer.close()
                with contextlib.suppress(ConnectionError, OSError):
                    await backend_writer.wait_closed()
            client_writer.close()
            with contextlib.suppress(ConnectionError, OSError):
                await client_writer.wait_closed()
            if task is not None:
                self._client_tasks.discard(task)

    async def _serve_proxy(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        server = await asyncio.start_server(
            self._proxy_connection,
            self._host,
            self._port,
        )
        address = server.sockets[0].getsockname()
        self._bound_addr = (str(address[0]), int(address[1]))
        self._started.set()
        await self._stop_event.wait()
        server.close()
        await server.wait_closed()
        tasks = [task for task in self._client_tasks if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _run_proxy(self) -> None:
        try:
            asyncio.run(self._serve_proxy())
        except BaseException as exc:
            self._start_error = exc
            self._started.set()

    def start(self, *, timeout: float = 5.0) -> None:
        """Start the proxy and wait until its listener is ready."""
        if self._thread is not None:
            raise RuntimeError("RoundRobinTestProxy is already running; stop it before restarting.")
        if not all(backend.is_running for backend in self._backends):
            raise RuntimeError(
                "RoundRobinTestProxy backends must be running before the proxy starts."
            )
        self._started.clear()
        self._start_error = None
        self._next_backend = 0
        self._thread = threading.Thread(target=self._run_proxy, daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=timeout):
            self.stop(timeout=timeout)
            raise RuntimeError(
                f"RoundRobinTestProxy did not start within {timeout}s; check listener availability."
            )
        if self._start_error is not None:
            error = self._start_error
            self._thread.join(timeout=timeout)
            self._thread = None
            raise RuntimeError(
                "RoundRobinTestProxy could not start; inspect the chained listener error."
            ) from error

    def stop(self, *, timeout: float = 5.0) -> None:
        """Stop accepting connections and cancel active proxy streams."""
        if self._thread is None:
            return
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise RuntimeError(
                f"RoundRobinTestProxy did not stop within {timeout}s; close active clients."
            )
        self._thread = None
        self._loop = None
        self._stop_event = None
        self._bound_addr = None

    @property
    def host(self) -> str:
        if self._bound_addr is None:
            raise RuntimeError(
                "RoundRobinTestProxy is not started; call start() before reading host."
            )
        return self._bound_addr[0]

    @property
    def port(self) -> int:
        if self._bound_addr is None:
            raise RuntimeError(
                "RoundRobinTestProxy is not started; call start() before reading port."
            )
        return self._bound_addr[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def __enter__(self) -> RoundRobinTestProxy:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
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
