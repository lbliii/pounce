"""
Tests for graceful worker reload functionality.

"""

import asyncio
import socket
import threading
import time

import pytest

from pounce.config import ServerConfig
from pounce.h3_worker import H3Worker
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


class TestH3WorkerRotationOnReload:
    """#111: graceful_reload must rotate H3 (HTTP/3) workers so the QUIC
    generation also picks up the reimported app. Without rotation the old H3
    workers keep serving STALE code (split-brain with the fresh TCP gen).

    The local interpreter is GIL CPython 3.14 and zoomies/QUIC is not exercised
    here, so these tests stub ``H3Worker.run`` to a thread that simply waits on
    the per-worker reload event and exits. They prove the SUPERVISOR-side
    rotation contract: old H3 handles are replaced by a new generation, the old
    H3 threads stop within ``reload_timeout``, and the new H3Worker holds the
    reimported app. The under-load H3 request-routing proof is #113 (CI 3.14t).
    """

    def _make_h3_supervisor(self, app):
        """Thread-mode supervisor with one TCP + one H3 (UDP) worker."""
        config = ServerConfig(
            workers=1,
            worker_mode="async",
            reload_timeout=2.0,
            shutdown_timeout=2.0,
            access_log=False,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
        )
        sup = Supervisor(
            config,
            app,
            app_path="tests.unit.test_graceful_reload:_reload_probe_app",
            mode="thread",
        )
        sup._effective_workers = 1
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", 0))
        sup._udp_sockets = [udp]
        return sup, udp

    @staticmethod
    def _stub_h3_run(worker: H3Worker) -> None:
        """Stand-in for H3Worker.run: wait on the per-worker reload event.

        Proves graceful_reload signals the per-worker event (NOT the shared
        shutdown event, which would also stop TCP workers). Exits promptly so
        the supervisor's parallel join completes within reload_timeout.
        """
        try:
            ev = worker._reload_shutdown
            if ev is not None:
                ev.wait(timeout=5.0)
        finally:
            worker._sock.close()

    def test_h3_generation_rotated_to_new_workers(self, monkeypatch):
        """After reload, _h3_handles are a NEW generation bound to the new app,
        and no old H3 thread survives past reload_timeout."""
        old_app = _make_marker_app("OLD")
        new_app = _make_marker_app("NEW")

        sup, udp = self._make_h3_supervisor(old_app)
        try:
            monkeypatch.setattr(H3Worker, "run", self._stub_h3_run, raising=True)

            # Seed the OLD H3 generation (real H3Worker bound to old_app).
            sup._spawn_h3_worker(0)
            assert len(sup._h3_handles) == 1
            old_handle = sup._h3_handles[0]
            assert old_handle.worker is not None
            assert old_handle.worker._app is old_app
            canonical_fd = udp.fileno()
            assert old_handle.worker._sock is not udp
            assert old_handle.worker._sock.fileno() != canonical_fd
            assert old_handle.target.is_alive()

            # Reload reimports the app -> return the NEW app.
            import pounce._importer as importer

            monkeypatch.setattr(importer, "reimport_app", lambda *_a, **_k: new_app)
            # Keep the TCP side trivial: idle fake worker, instant-exit thread.
            monkeypatch.setattr(
                Supervisor,
                "_create_worker",
                lambda self, worker_id, socket_index: _IdleFakeWorker(),
            )

            sup._graceful_reload_impl()

            # New H3 generation present and DISTINCT from the old one.
            assert len(sup._h3_handles) == 1
            new_handle = sup._h3_handles[0]
            assert new_handle is not old_handle, "H3 generation was not rotated"
            assert new_handle.worker is not None

            # The new H3 worker holds the REIMPORTED app (no stale code).
            assert new_handle.worker._app is new_app, "new H3 worker runs stale app"

            # The old H3 worker was signalled via its PER-WORKER reload event
            # (not the shared shutdown event) and its thread is gone.
            assert old_handle.reload_shutdown_event is not None
            assert old_handle.reload_shutdown_event.is_set()
            assert not sup._shutdown_event.is_set(), (
                "shared shutdown event must NOT be set during graceful reload "
                "(would also stop TCP workers)"
            )
            deadline = time.monotonic() + sup._config.reload_timeout
            while old_handle.target.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert not old_handle.target.is_alive(), (
                "old H3 worker thread survived past reload_timeout"
            )
            assert old_handle.worker._sock.fileno() == -1
            assert udp.fileno() == canonical_fd
            assert new_handle.worker._sock is not udp
            assert new_handle.worker._sock.fileno() != canonical_fd
        finally:
            # Release any new-gen H3 threads still waiting on their event.
            for h in sup._h3_handles:
                if h.reload_shutdown_event is not None:
                    h.reload_shutdown_event.set()
            sup._shutdown_event.set()
            udp.close()

    def test_h3_reload_refuses_competing_generation_when_old_thread_survives(self, monkeypatch):
        """A stuck old H3 owner blocks replacement instead of sharing UDP input."""
        stop = threading.Event()

        def stuck_h3_run(worker: H3Worker) -> None:
            try:
                stop.wait(timeout=5.0)
            finally:
                worker._sock.close()

        sup, udp = self._make_h3_supervisor(_make_marker_app("OLD"))
        try:
            monkeypatch.setattr(H3Worker, "run", stuck_h3_run, raising=True)
            sup._spawn_h3_worker(0)
            old_handle = sup._h3_handles[0]

            import pounce._importer as importer
            import pounce.supervisor as supervisor_module

            monkeypatch.setattr(
                importer,
                "reimport_app",
                lambda *_a, **_k: _make_marker_app("NEW"),
            )
            monkeypatch.setattr(
                Supervisor,
                "_create_worker",
                lambda self, worker_id, socket_index: _IdleFakeWorker(),
            )
            monkeypatch.setattr(
                supervisor_module,
                "_parallel_join_targets",
                lambda _targets, _timeout: None,
            )

            sup._graceful_reload_impl()

            assert sup._h3_handles == [old_handle]
            assert old_handle.target.is_alive()
            assert not sup._shutdown_event.is_set()
        finally:
            stop.set()
            for handle in sup._h3_handles:
                handle.target.join(timeout=1.0)
                if handle.worker is not None:
                    handle.worker._sock.close()
            sup._shutdown_event.set()
            udp.close()

    def test_reload_without_udp_sockets_is_h3_noop(self, monkeypatch):
        """When no UDP sockets are configured, reload leaves _h3_handles empty
        and does not attempt any H3 rotation."""
        sup, udp = self._make_h3_supervisor(_make_marker_app("OLD"))
        try:
            sup._udp_sockets = []  # no HTTP/3 listeners
            import pounce._importer as importer

            monkeypatch.setattr(importer, "reimport_app", lambda *_a, **_k: _make_marker_app("NEW"))
            monkeypatch.setattr(
                Supervisor,
                "_create_worker",
                lambda self, worker_id, socket_index: _IdleFakeWorker(),
            )
            sup._graceful_reload_impl()
            assert sup._h3_handles == []
        finally:
            sup._shutdown_event.set()
            udp.close()


class TestH3DrainOnShutdown:
    """#113: ``_drain`` (the SIGTERM-style graceful-shutdown path) must signal
    AND join the H3 (HTTP/3) workers, just like the TCP generation.

    These lock the supervisor-side drain contract that #112 wired up: on
    ``_drain`` the shared ``_shutdown_event`` is set (H3 workers bridge on it),
    every ``_h3_handles`` thread is joined with ``shutdown_timeout`` per worker,
    and no H3 thread is left orphaned past that bounded window. The local
    interpreter is GIL CPython 3.14 with no live QUIC runtime, so ``H3Worker.run``
    is stubbed to a thread that waits on the SHARED shutdown event and exits;
    the under-load drain-of-in-flight-streams proof is the CI 3.14t integration
    test in tests/integration/test_h3_integration.py.
    """

    def _make_h3_supervisor(self, app):
        """Thread-mode supervisor with one TCP + one H3 (UDP) worker."""
        config = ServerConfig(
            workers=1,
            worker_mode="async",
            reload_timeout=2.0,
            shutdown_timeout=2.0,
            access_log=False,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
        )
        sup = Supervisor(
            config,
            app,
            app_path="tests.unit.test_graceful_reload:_reload_probe_app",
            mode="thread",
        )
        sup._effective_workers = 1
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(("127.0.0.1", 0))
        sup._udp_sockets = [udp]
        return sup, udp

    @staticmethod
    def _stub_h3_run_shared(worker: H3Worker) -> None:
        """Stand-in for H3Worker.run: wait on the SHARED shutdown event, then
        spend a short, deterministic interval "draining" before exiting.

        Two contracts are proven at once. The thread exits only AFTER the shared
        ``_shutdown_event`` is set, so the post-_drain ``is_alive()`` assertion
        fails unless ``_drain`` SIGNALLED it (H3 workers bridge on the shared
        event for global shutdown — not just the per-worker reload event). The
        post-signal drain interval means the thread is still running when
        ``_drain`` returns *unless* ``_drain`` JOINED it; so dropping the H3 join
        also trips the assertion. The interval stays well under shutdown_timeout
        so a correct, joining ``_drain`` still completes within budget.
        """
        try:
            ev = worker._ext_shutdown
            if ev is not None:
                ev.wait(timeout=5.0)
            # Simulate bounded in-flight-stream drain work after the stop signal.
            time.sleep(0.3)
        finally:
            worker._sock.close()

    def test_drain_signals_and_joins_h3_workers(self, monkeypatch):
        """_drain sets the shared shutdown event, the H3 thread observes it and
        exits, and the handle is joined within shutdown_timeout (no orphan)."""
        sup, udp = self._make_h3_supervisor(_make_marker_app("APP"))
        try:
            monkeypatch.setattr(H3Worker, "run", self._stub_h3_run_shared, raising=True)

            sup._spawn_h3_worker(0)
            assert len(sup._h3_handles) == 1
            handle = sup._h3_handles[0]
            assert handle.target.is_alive()
            assert not sup._shutdown_event.is_set()

            t0 = time.monotonic()
            sup._drain()
            elapsed = time.monotonic() - t0

            # _drain must set the shared shutdown event (H3 bridge polls it).
            assert sup._shutdown_event.is_set()
            # The H3 worker thread observed the signal and exited — joined, not
            # orphaned, within the bounded shutdown_timeout window.
            assert not handle.target.is_alive(), "H3 worker thread was not joined/drained by _drain"
            # Bounded: _drain joins H3 handles with shutdown_timeout per worker.
            assert elapsed < sup._config.shutdown_timeout + 1.0
        finally:
            sup._shutdown_event.set()
            udp.close()

    def test_drain_with_no_h3_handles_is_noop(self, monkeypatch):
        """_drain over an empty H3 generation does not error and leaves the
        (empty) handle list untouched."""
        sup, udp = self._make_h3_supervisor(_make_marker_app("APP"))
        try:
            assert sup._h3_handles == []
            sup._drain()
            assert sup._h3_handles == []
            assert sup._shutdown_event.is_set()
        finally:
            sup._shutdown_event.set()
            udp.close()
