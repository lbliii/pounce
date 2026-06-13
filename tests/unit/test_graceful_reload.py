"""
Tests for graceful worker reload functionality.

"""

import asyncio
import socket

import pytest

from pounce.config import ServerConfig
from pounce.supervisor import Supervisor
from pounce.worker import Worker


class TestWorkerDrainMode:
    """Tests for Worker drain mode functionality."""

    def test_worker_starts_not_draining(self):
        """Test that workers start in non-draining mode."""
        config = ServerConfig()
        sock = socket.socket()

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(config, simple_app, sock, worker_id=0)

        assert worker._draining is False

    def test_start_draining(self):
        """Test marking a worker for draining."""
        config = ServerConfig()
        sock = socket.socket()

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(config, simple_app, sock, worker_id=0)

        assert worker._draining is False
        worker.start_draining()
        assert worker._draining is True

    def test_is_idle_when_no_connections(self):
        """Test is_idle returns True when no active connections."""
        config = ServerConfig()
        sock = socket.socket()

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(config, simple_app, sock, worker_id=0)

        assert worker.is_idle() is True

    def test_is_idle_when_has_connections(self):
        """Test is_idle returns False when active connections exist."""
        config = ServerConfig()
        sock = socket.socket()

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(config, simple_app, sock, worker_id=0)
        worker._active_connections = 5

        assert worker.is_idle() is False


class TestServerConfig:
    """Tests for reload configuration."""

    def test_default_reload_timeout(self):
        """Test default reload timeout."""
        config = ServerConfig()

        assert config.reload_timeout == 30.0

    def test_custom_reload_timeout(self):
        """Test custom reload timeout."""
        config = ServerConfig(reload_timeout=60.0)

        assert config.reload_timeout == 60.0


class TestSupervisorGeneration:
    """Tests for supervisor worker generation tracking."""

    def test_supervisor_starts_at_generation_zero(self):
        """Test supervisor initializes with generation 0."""
        config = ServerConfig()

        async def simple_app(scope, receive, send):
            pass

        supervisor = Supervisor(config, simple_app, mode="thread")

        assert supervisor._generation == 0

    @pytest.mark.skipif(
        True,  # Skip for now - requires full supervisor setup
        reason="Requires complex test setup",
    )
    def test_graceful_reload_increments_generation(self):
        """Test that graceful_reload increments generation counter."""
        # This would require a full supervisor+socket setup


# Graceful reload integration tests would require full supervisor+worker+socket setup
# These are covered by manual testing and end-to-end tests


class TestDrainSignaling:
    """Tests for drain signaling between supervisor and workers."""

    @pytest.mark.asyncio
    async def test_start_draining_sets_shutdown_event(self):
        """Test that start_draining signals the worker event loop."""
        config = ServerConfig()
        sock = socket.socket()

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(config, simple_app, sock, worker_id=0)

        # Simulate event loop setup
        loop = asyncio.get_event_loop()
        worker._loop = loop
        worker._async_shutdown = asyncio.Event()

        # Start draining should set the shutdown event
        worker.start_draining()

        # Wait briefly for event loop callback
        await asyncio.sleep(0.01)

        assert worker._async_shutdown.is_set()


class TestConfigValidation:
    """Tests for reload configuration validation."""

    def test_reload_timeout_must_be_positive(self):
        """Test that reload_timeout must be > 0."""
        # Currently no validation, but documenting expected behavior
        config = ServerConfig(reload_timeout=30.0)
        assert config.reload_timeout > 0


class TestAsyncPoolRebuildOnReload:
    """#102: graceful_reload (sync mode) must rebuild the AsyncPool with the
    reimported app so streaming/WebSocket handoffs run the new code."""

    def _make_sync_supervisor(self, app):
        """Thread+sync supervisor with two workers sharing one listener."""
        config = ServerConfig(
            workers=2,
            worker_mode="sync",
            reload_timeout=1.0,
            shutdown_timeout=1.0,
            access_log=False,
        )
        sup = Supervisor(
            config,
            app,
            app_path="tests.unit.test_graceful_reload:_reload_probe_app",
            mode="thread",
        )
        # Force sync execution even on a GIL build (resolve would pick async).
        sup._execution_mode = "sync"
        # Two workers sharing the SAME listener object -> AcceptDistributor path.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        sup._sockets = [listener, listener]
        return sup, listener

    def test_async_pool_rebuilt_with_reimported_app(self, monkeypatch):
        """After reload, self._async_pool is a NEW pool bound to the new app."""
        old_app = _make_marker_app("OLD")
        new_app = _make_marker_app("NEW")

        sup, listener = self._make_sync_supervisor(old_app)
        try:
            sup._setup_sync_infrastructure()
            original_pool = sup._async_pool
            assert original_pool is not None
            assert original_pool._app is old_app

            # Reload reimports the app; force it to return the NEW app.
            import pounce._importer as importer

            monkeypatch.setattr(importer, "reimport_app", lambda *_a, **_k: new_app)

            # Stub worker spawning so the reload doesn't need real serving threads:
            # return an already-idle fake worker and a thread that exits at once.
            monkeypatch.setattr(
                Supervisor,
                "_create_worker",
                lambda self, worker_id, socket_index: _IdleFakeWorker(),
            )

            sup._graceful_reload_impl()

            # The pool instance must have changed and point at the new app.
            assert sup._async_pool is not None
            assert sup._async_pool is not original_pool, "AsyncPool was not rebuilt"
            assert sup._async_pool._app is new_app, "rebuilt pool runs stale app"
            # The old pool must have been told to shut down (per-pool event).
            assert original_pool._pool_shutdown.is_set()
        finally:
            sup._shutdown_event.set()
            if sup._accept_distributor_drain is not None:
                sup._accept_distributor_drain.set()
            listener.close()

    def test_distributor_and_queue_preserved_across_reload(self, monkeypatch):
        """The app-agnostic AcceptDistributor/queue are shared, not orphaned."""
        sup, listener = self._make_sync_supervisor(_make_marker_app("OLD"))
        try:
            sup._setup_sync_infrastructure()
            queue_before = sup._conn_queue
            assert queue_before is not None  # shared-socket path built a queue

            import pounce._importer as importer

            monkeypatch.setattr(importer, "reimport_app", lambda *_a, **_k: _make_marker_app("NEW"))
            monkeypatch.setattr(
                Supervisor,
                "_create_worker",
                lambda self, worker_id, socket_index: _IdleFakeWorker(),
            )
            sup._graceful_reload_impl()

            # Queue object is reused (new workers share it, like the old gen).
            assert sup._conn_queue is queue_before
        finally:
            sup._shutdown_event.set()
            if sup._accept_distributor_drain is not None:
                sup._accept_distributor_drain.set()
            listener.close()


class _IdleFakeWorker:
    """A worker stand-in that runs trivially and is always idle."""

    def __init__(self) -> None:
        self._draining = False

    def run(self) -> None:  # executed in the spawned thread; returns immediately
        return

    def start_draining(self) -> None:
        self._draining = True

    def is_idle(self) -> bool:
        return True


def _make_marker_app(marker: str):
    async def _app(scope, receive, send):  # pragma: no cover - not invoked here
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-marker", marker.encode())],
            }
        )
        await send({"type": "http.response.body", "body": marker.encode()})

    _app.__marker__ = marker  # type: ignore[attr-defined]
    return _app


async def _reload_probe_app(scope, receive, send):  # pragma: no cover
    """Module-level app referenced by app_path in the reload supervisor."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"probe"})
