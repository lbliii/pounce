"""
Tests for hot reload functionality.

"""

import socket
import time
from unittest.mock import MagicMock

import pytest

from pounce._hot_reload import (
    ReloadCoordinator,
    WorkerGeneration,
    create_reloadable_socket,
    enable_socket_reuse,
    get_reload_status,
    is_hot_reload_supported,
    should_drain_worker,
    wait_for_workers_to_drain,
)


class TestWorkerGeneration:
    """Tests for WorkerGeneration."""

    def test_initial_generation(self):
        """Test worker starts at generation 1."""
        gen = WorkerGeneration(generation=1)
        assert gen.generation == 1

    def test_custom_generation(self):
        """Test worker can start at custom generation."""
        gen = WorkerGeneration(generation=5)
        assert gen.generation == 5

    def test_start_time_recorded(self):
        """Test that start time is recorded."""
        gen = WorkerGeneration()
        assert gen.start_time > 0

    def test_pid_recorded(self):
        """Test that PID is recorded."""
        gen = WorkerGeneration()
        assert gen.pid > 0

    def test_uptime_increases(self):
        """Test that uptime increases over time."""
        gen = WorkerGeneration()
        uptime1 = gen.uptime
        time.sleep(0.01)
        uptime2 = gen.uptime
        assert uptime2 > uptime1

    def test_is_old_generation(self):
        """Test old generation detection."""
        gen = WorkerGeneration(generation=1)

        # Same generation - not old
        assert gen.is_old_generation(1) is False

        # Newer generation - old
        assert gen.is_old_generation(2) is True

        # Even newer - still old
        assert gen.is_old_generation(10) is True


class TestReloadCoordinator:
    """Tests for ReloadCoordinator."""

    def test_initial_generation(self):
        """Test coordinator starts at generation 1."""
        coord = ReloadCoordinator()
        assert coord.current_generation == 1

    def test_no_reload_initially(self):
        """Test no reload is requested initially."""
        coord = ReloadCoordinator()
        assert coord.reload_requested is False
        assert coord.reload_in_progress is False

    def test_request_reload(self):
        """Test requesting reload sets flag."""
        coord = ReloadCoordinator()
        coord.request_reload()
        assert coord.reload_requested is True
        assert coord.reload_in_progress is False

    def test_start_reload(self):
        """Test starting reload increments generation."""
        coord = ReloadCoordinator()
        old_gen = coord.current_generation

        new_gen = coord.start_reload()

        assert new_gen == old_gen + 1
        assert coord.current_generation == new_gen
        assert coord.reload_in_progress is True
        assert coord.reload_requested is False

    def test_finish_reload(self):
        """Test finishing reload clears in_progress flag."""
        coord = ReloadCoordinator()
        coord.start_reload()
        coord.finish_reload()

        assert coord.reload_in_progress is False

    def test_cancel_reload(self):
        """Test cancelling reload clears request flag."""
        coord = ReloadCoordinator()
        coord.request_reload()
        coord.cancel_reload()

        assert coord.reload_requested is False

    def test_multiple_reloads(self):
        """Test multiple reload cycles."""
        coord = ReloadCoordinator()

        # First reload
        coord.request_reload()
        coord.start_reload()
        assert coord.current_generation == 2
        coord.finish_reload()

        # Second reload
        coord.request_reload()
        coord.start_reload()
        assert coord.current_generation == 3
        coord.finish_reload()


class TestSocketReuse:
    """Tests for socket reuse configuration."""

    def test_enable_socket_reuse_sets_reuseaddr(self):
        """Test that SO_REUSEADDR is set."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            enable_socket_reuse(sock)

            # Check SO_REUSEADDR is set (non-zero means enabled)
            reuseaddr = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
            assert reuseaddr != 0
        finally:
            sock.close()

    @pytest.mark.skipif(
        not hasattr(socket, "SO_REUSEPORT"),
        reason="SO_REUSEPORT not available",
    )
    def test_enable_socket_reuse_sets_reuseport(self):
        """Test that SO_REUSEPORT is set when available."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            enable_socket_reuse(sock)

            # Check SO_REUSEPORT is set (non-zero means enabled)
            reuseport = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT)
            assert reuseport != 0
        finally:
            sock.close()

    def test_is_hot_reload_supported(self):
        """Test hot reload support detection."""
        supported = is_hot_reload_supported()

        # Should match SO_REUSEPORT availability
        expected = hasattr(socket, "SO_REUSEPORT")
        assert supported == expected


class TestReloadableSocket:
    """Tests for reloadable socket creation."""

    def test_create_reloadable_socket(self):
        """Test creating a reloadable socket."""
        # Use port 0 to let OS assign
        sock = create_reloadable_socket("127.0.0.1", 0)
        try:
            # Socket should be bound and listening
            assert sock.fileno() > 0

            # Get actual port
            addr = sock.getsockname()
            assert addr[0] == "127.0.0.1"
            assert addr[1] > 0

            # SO_REUSEADDR should be set (non-zero means enabled)
            reuseaddr = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
            assert reuseaddr != 0
        finally:
            sock.close()

    @pytest.mark.skipif(
        not hasattr(socket, "SO_REUSEPORT"),
        reason="SO_REUSEPORT not available",
    )
    def test_reloadable_socket_has_reuseport(self):
        """Test that reloadable socket has SO_REUSEPORT."""
        sock = create_reloadable_socket("127.0.0.1", 0)
        try:
            reuseport = sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT)
            assert reuseport != 0
        finally:
            sock.close()

    @pytest.mark.skipif(
        not hasattr(socket, "SO_REUSEPORT"),
        reason="SO_REUSEPORT not available",
    )
    def test_multiple_sockets_same_port(self):
        """Test that multiple sockets can bind to same port with SO_REUSEPORT."""
        # First socket
        sock1 = create_reloadable_socket("127.0.0.1", 0)
        try:
            port = sock1.getsockname()[1]

            # Second socket on same port - should succeed with SO_REUSEPORT
            sock2 = create_reloadable_socket("127.0.0.1", port)
            try:
                assert sock2.getsockname()[1] == port
            finally:
                sock2.close()
        finally:
            sock1.close()


class TestReloadStatus:
    """Tests for reload status reporting."""

    def test_get_reload_status(self):
        """Test getting reload status."""
        status = get_reload_status()

        assert "supported" in status
        assert "so_reuseport_available" in status
        assert "platform" in status

        # supported should match SO_REUSEPORT availability
        assert status["supported"] == hasattr(socket, "SO_REUSEPORT")
        assert status["so_reuseport_available"] == hasattr(socket, "SO_REUSEPORT")

    def test_status_platform_reported(self):
        """Test that platform is reported in status."""
        status = get_reload_status()
        assert isinstance(status["platform"], str)


class TestWorkerDraining:
    """Tests for worker draining logic."""

    def test_should_drain_old_generation(self):
        """Test that old generation workers should drain."""
        gen = WorkerGeneration(generation=1)

        # Same generation - should not drain
        assert should_drain_worker(gen, 1, 30.0) is False

        # Newer generation - should drain
        assert should_drain_worker(gen, 2, 30.0) is True

    def test_should_drain_after_timeout(self):
        """Test that workers drain after timeout."""
        gen = WorkerGeneration(generation=1)

        # Immediate check - should not drain (same generation, no timeout)
        assert should_drain_worker(gen, 1, 0.01) is False

        # Wait for timeout
        time.sleep(0.02)

        # After timeout - should drain
        assert should_drain_worker(gen, 1, 0.01) is True

    def test_wait_for_workers_success(self):
        """Test waiting for workers to drain successfully."""
        # Mock workers that stop immediately
        worker1 = MagicMock()
        worker1.target.is_alive.return_value = False
        worker1.worker_id = 1

        worker2 = MagicMock()
        worker2.target.is_alive.return_value = False
        worker2.worker_id = 2

        workers = [worker1, worker2]

        # Should return True (all drained)
        result = wait_for_workers_to_drain(workers, timeout=1.0, check_interval=0.1)
        assert result is True

    def test_wait_for_workers_timeout(self):
        """Test waiting for workers times out."""
        # Mock workers that never stop
        worker1 = MagicMock()
        worker1.target.is_alive.return_value = True
        worker1.worker_id = 1

        workers = [worker1]

        # Should return False (timeout)
        result = wait_for_workers_to_drain(workers, timeout=0.2, check_interval=0.05)
        assert result is False

    def test_wait_for_workers_partial_drain(self):
        """Test waiting when some workers drain and others don't."""
        # Worker that drains after a delay
        worker1 = MagicMock()
        worker1.target.is_alive.side_effect = [True, True, False]
        worker1.worker_id = 1

        # Worker that never drains
        worker2 = MagicMock()
        worker2.target.is_alive.return_value = True
        worker2.worker_id = 2

        workers = [worker1, worker2]

        # Should return False (worker2 didn't drain)
        result = wait_for_workers_to_drain(workers, timeout=0.3, check_interval=0.05)
        assert result is False

    def test_wait_for_empty_workers(self):
        """Test waiting for empty worker list."""
        workers = []

        # Should return True immediately
        result = wait_for_workers_to_drain(workers, timeout=1.0)
        assert result is True
