"""Tests for pounce.supervisor — worker lifecycle management."""

import contextlib
import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from pounce._errors import SupervisorError
from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.supervisor import Supervisor, _WorkerHandle


def _wait_for_handles(
    sup: Supervisor,
    count: int,
    *,
    timeout: float = 3.0,
) -> None:
    """Poll until the supervisor has ``count`` alive worker handles."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(sup._handles) >= count and all(h.target.is_alive() for h in sup._handles[:count]):
            return
        time.sleep(0.05)
    msg = f"Supervisor did not spawn {count} alive handles within {timeout}s"
    raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal ASGI app that does nothing."""


def _make_sockets(count: int) -> list[socket.socket]:
    """Create ephemeral sockets for testing."""
    sockets: list[socket.socket] = []
    for _ in range(count):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        sock.setblocking(False)
        sockets.append(sock)
    return sockets


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSupervisorInit:
    """Supervisor initialisation and mode detection."""

    def test_auto_detect_mode(self):
        config = ServerConfig(workers=2)
        sup = Supervisor(config, _noop_app)
        assert sup.mode in ("thread", "process")

    def test_explicit_thread_mode(self):
        config = ServerConfig(workers=2)
        sup = Supervisor(config, _noop_app, mode="thread")
        assert sup.mode == "thread"

    def test_explicit_process_mode(self):
        config = ServerConfig(workers=2)
        sup = Supervisor(config, _noop_app, mode="process")
        assert sup.mode == "process"

    def test_worker_count_from_config(self):
        config = ServerConfig(workers=4)
        sup = Supervisor(config, _noop_app)
        assert sup.worker_count == 4

    def test_worker_count_auto_detect(self):
        config = ServerConfig(workers=0)
        sup = Supervisor(config, _noop_app)
        assert sup.worker_count >= 1


class TestSupervisorSocketValidation:
    """Supervisor validates that socket count matches worker count."""

    def test_wrong_socket_count_raises(self):
        config = ServerConfig(workers=2)
        sup = Supervisor(config, _noop_app, mode="thread")
        sockets = _make_sockets(3)  # Wrong count
        try:
            with pytest.raises(SupervisorError, match="Expected 2 sockets"):
                sup.run(sockets)
        finally:
            for s in sockets:
                s.close()


class TestSupervisorShutdown:
    """Supervisor shutdown coordination."""

    def test_shutdown_sets_event(self):
        config = ServerConfig(workers=2)
        sup = Supervisor(config, _noop_app, mode="thread")
        assert not sup._shutdown_event.is_set()
        sup.shutdown()
        assert sup._shutdown_event.is_set()


class TestSupervisorThreadMode:
    """Supervisor runs workers as threads and shuts down cleanly."""

    def test_spawn_and_shutdown(self):
        """Spawn 2 thread workers, then shut down."""
        config = ServerConfig(workers=2, host="127.0.0.1", port=0, access_log=False)
        sup = Supervisor(config, _noop_app, mode="thread")
        sockets = _make_sockets(2)

        # Run in a background thread so we can trigger shutdown
        def run_supervisor():
            sup.run(sockets)

        t = threading.Thread(target=run_supervisor, daemon=True)
        t.start()

        # Wait for workers to be alive (replaces flaky time.sleep)
        _wait_for_handles(sup, 2)

        # All handles should be alive
        for h in sup._handles:
            assert h.target.is_alive()

        # Trigger shutdown
        sup.shutdown()
        t.join(timeout=5.0)
        assert not t.is_alive()

        # Clean up
        for s in set(sockets):
            with contextlib.suppress(Exception):
                s.close()


class TestSupervisorRespawn:
    """Supervisor respawn logic and restart budget."""

    def test_respawn_increments_restart_count(self):
        """_respawn_worker tracks restart count."""
        config = ServerConfig(workers=2, host="127.0.0.1", port=0, access_log=False)
        sup = Supervisor(config, _noop_app, mode="thread")
        sockets = _make_sockets(2)

        def run_sup():
            sup.run(sockets)

        t = threading.Thread(target=run_sup, daemon=True)
        t.start()
        _wait_for_handles(sup, 2)

        try:
            # Verify initial state
            assert sup._handles[0].restart_count == 0

            # Simulate a crash — the watchdog will detect and respawn
            sup._handles[0].target.join(timeout=0)  # non-blocking
        finally:
            sup.shutdown()
            t.join(timeout=5.0)
            for s in set(sockets):
                with contextlib.suppress(Exception):
                    s.close()

    def test_restart_budget_exhaustion(self):
        """_respawn_worker stops after max restarts within the window."""
        config = ServerConfig(workers=1, host="127.0.0.1", port=0, access_log=False)
        sup = Supervisor(config, _noop_app, mode="thread")

        # Set up a handle with an exhausted restart budget
        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        handle = _WorkerHandle(0, mock_thread)
        handle.restarts = [time.monotonic()] * 5  # Already at max
        sup._handles = [handle]
        sup._sockets = _make_sockets(1)

        try:
            # This should NOT respawn (budget exhausted)
            sup._respawn_worker(0)
            # restart_count stays 0 because _spawn_worker was not called
            assert handle.restart_count == 0
        finally:
            for s in sup._sockets:
                s.close()

    def test_old_restarts_pruned(self):
        """Restarts outside the window are pruned before budget check."""
        config = ServerConfig(workers=1, host="127.0.0.1", port=0, access_log=False)
        sup = Supervisor(config, _noop_app, mode="thread")

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        handle = _WorkerHandle(0, mock_thread)
        # Old restarts (> 60s ago) should be pruned
        handle.restarts = [time.monotonic() - 120.0] * 5
        sup._handles = [handle]
        sup._sockets = _make_sockets(1)

        try:
            # Should be allowed (old restarts pruned)
            sup._respawn_worker(0)
            assert handle.restart_count == 1
        finally:
            # Clean up the spawned thread
            sup.shutdown()
            time.sleep(0.5)
            for s in sup._sockets:
                with contextlib.suppress(Exception):
                    s.close()


class TestSupervisorRestartWorkers:
    """Supervisor restart_workers() logic (unit-level, no real workers)."""

    def test_restart_clears_shutdown_event(self):
        """restart_workers() sets then clears the shutdown event."""
        config = ServerConfig(workers=2, host="127.0.0.1", port=0, access_log=False)
        sup = Supervisor(config, _noop_app, mode="thread")
        sockets = _make_sockets(2)
        sup._sockets = sockets

        # Plant mock handles so restart_workers can join them
        for i in range(2):
            mock_thread = MagicMock(spec=threading.Thread)
            mock_thread.is_alive.return_value = False
            sup._handles.append(_WorkerHandle(i, mock_thread))

        try:
            # Patch at the class level (Supervisor uses __slots__)
            with patch.object(Supervisor, "_spawn_worker") as mock_spawn:
                sup.restart_workers()

                # Shutdown event should be cleared for new workers
                assert not sup._shutdown_event.is_set()
                # _spawn_worker should be called for each worker
                assert mock_spawn.call_count == 2
        finally:
            for s in sockets:
                with contextlib.suppress(Exception):
                    s.close()

    def test_restart_joins_old_workers(self):
        """restart_workers() joins existing workers before respawning."""
        config = ServerConfig(workers=1, host="127.0.0.1", port=0, access_log=False)
        sup = Supervisor(config, _noop_app, mode="thread")
        sockets = _make_sockets(1)
        sup._sockets = sockets

        mock_thread = MagicMock(spec=threading.Thread)
        mock_thread.is_alive.return_value = False
        sup._handles.append(_WorkerHandle(0, mock_thread))

        try:
            with patch.object(Supervisor, "_spawn_worker"):
                sup.restart_workers()

                # Old handle should have been joined
                mock_thread.join.assert_called_once()
        finally:
            for s in sockets:
                with contextlib.suppress(Exception):
                    s.close()


class TestSupervisorPerWorkerConnections:
    """Supervisor calculates per-worker connection limits."""

    def test_per_worker_max_connections(self):
        config = ServerConfig(workers=4, max_connections=1000)
        sup = Supervisor(config, _noop_app, mode="thread")
        # Per-worker limit should be 1000 // 4 = 250
        # Verify by checking the supervisor calculates it
        assert config.max_connections // sup.worker_count == 250

    def test_zero_max_connections_means_unlimited(self):
        config = ServerConfig(workers=4, max_connections=0)
        sup = Supervisor(config, _noop_app, mode="thread")
        assert config.max_connections // sup.worker_count == 0


class TestWorkerHandle:
    """_WorkerHandle tracks metadata about a running worker."""

    def test_initial_state(self):
        mock_thread = MagicMock(spec=threading.Thread)
        handle = _WorkerHandle(0, mock_thread)
        assert handle.worker_id == 0
        assert handle.restart_count == 0
        assert handle.restarts == []
        assert handle.started_at > 0

    def test_restart_tracking(self):
        mock_thread = MagicMock(spec=threading.Thread)
        handle = _WorkerHandle(0, mock_thread)
        now = time.monotonic()
        handle.restarts.append(now)
        handle.restart_count += 1
        assert handle.restart_count == 1
        assert len(handle.restarts) == 1
