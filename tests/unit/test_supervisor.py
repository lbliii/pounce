"""Tests for pounce.supervisor — worker lifecycle management."""

from __future__ import annotations

import socket
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from pounce._errors import SupervisorError
from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.supervisor import Supervisor, _WorkerHandle


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

        # Give workers time to start
        time.sleep(0.5)

        # All handles should be alive
        for h in sup._handles:
            assert h.target.is_alive()

        # Trigger shutdown
        sup.shutdown()
        t.join(timeout=5.0)
        assert not t.is_alive()

        # Clean up
        for s in set(sockets):
            try:
                s.close()
            except Exception:
                pass


class TestWorkerHandle:
    """_WorkerHandle tracks metadata about a running worker."""

    def test_initial_state(self):
        mock_thread = MagicMock(spec=threading.Thread)
        handle = _WorkerHandle(0, mock_thread)
        assert handle.worker_id == 0
        assert handle.restart_count == 0
        assert handle.restarts == []
        assert handle.started_at > 0
