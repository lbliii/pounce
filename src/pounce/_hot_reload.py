"""
Hot reload without connection drops for pounce.

Implements zero-downtime code reloads by gracefully replacing workers
without dropping active connections.

"""

import logging
import os
import socket
import time
from typing import Any

logger = logging.getLogger("pounce.reload")


class WorkerGeneration:
    """Tracks worker generations for hot reload.

    Each reload creates a new generation of workers. Old generations
    are drained gracefully while new generations handle new requests.

    """

    __slots__ = ("_generation", "_start_time", "_pid")

    def __init__(self, generation: int = 1) -> None:
        """Initialize worker generation.

        Args:
            generation: Generation number (increments with each reload)

        """
        self._generation = generation
        self._start_time = time.monotonic()
        self._pid = os.getpid()

    @property
    def generation(self) -> int:
        """Get generation number.

        Returns:
            Generation number

        """
        return self._generation

    @property
    def start_time(self) -> float:
        """Get worker start time.

        Returns:
            Start time (monotonic)

        """
        return self._start_time

    @property
    def pid(self) -> int:
        """Get worker process ID.

        Returns:
            Process ID

        """
        return self._pid

    @property
    def uptime(self) -> float:
        """Get worker uptime in seconds.

        Returns:
            Uptime in seconds

        """
        return time.monotonic() - self._start_time

    def is_old_generation(self, current_generation: int) -> bool:
        """Check if this worker is from an old generation.

        Args:
            current_generation: Current generation number

        Returns:
            True if worker is old generation

        """
        return self._generation < current_generation


def enable_socket_reuse(sock: socket.socket) -> None:
    """Enable SO_REUSEADDR and SO_REUSEPORT on socket.

    This allows multiple workers to bind to the same address/port,
    enabling zero-downtime reloads.

    Args:
        sock: Socket to configure

    Note:
        SO_REUSEPORT is required for hot reload. It's available on
        Linux 3.9+, macOS 10.9+, and FreeBSD 12+.

    """
    # Enable SO_REUSEADDR (standard reuse)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # Enable SO_REUSEPORT (port sharing for hot reload)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            logger.debug("SO_REUSEPORT enabled (hot reload supported)")
        except OSError as e:
            logger.warning("Failed to enable SO_REUSEPORT: %s (hot reload may fail)", e)
    else:
        logger.warning("SO_REUSEPORT not available (hot reload may fail)")


def is_hot_reload_supported() -> bool:
    """Check if hot reload is supported on this platform.

    Returns:
        True if SO_REUSEPORT is available

    """
    return hasattr(socket, "SO_REUSEPORT")


class ReloadCoordinator:
    """Coordinates hot reload across supervisor and workers.

    Manages the reload process:
    1. Supervisor increments generation number
    2. New workers start with new generation
    3. Old workers drain gracefully
    4. Supervisor waits for old workers to finish

    """

    __slots__ = ("_current_generation", "_reload_requested", "_reload_in_progress")

    def __init__(self) -> None:
        """Initialize reload coordinator."""
        self._current_generation = 1
        self._reload_requested = False
        self._reload_in_progress = False

    @property
    def current_generation(self) -> int:
        """Get current generation number.

        Returns:
            Current generation

        """
        return self._current_generation

    @property
    def reload_requested(self) -> bool:
        """Check if reload was requested.

        Returns:
            True if reload requested

        """
        return self._reload_requested

    @property
    def reload_in_progress(self) -> bool:
        """Check if reload is in progress.

        Returns:
            True if reload in progress

        """
        return self._reload_in_progress

    def request_reload(self) -> None:
        """Request a hot reload.

        This sets a flag that the supervisor checks. When detected,
        the supervisor starts the reload process.

        """
        self._reload_requested = True
        logger.info("Hot reload requested (generation %d -> %d)", self._current_generation, self._current_generation + 1)

    def start_reload(self) -> int:
        """Start reload process and increment generation.

        Returns:
            New generation number

        """
        self._reload_in_progress = True
        self._reload_requested = False
        self._current_generation += 1
        logger.info("Hot reload started: generation %d", self._current_generation)
        return self._current_generation

    def finish_reload(self) -> None:
        """Mark reload as finished."""
        self._reload_in_progress = False
        logger.info("Hot reload completed: generation %d", self._current_generation)

    def cancel_reload(self) -> None:
        """Cancel pending reload request."""
        self._reload_requested = False
        logger.info("Hot reload cancelled")


def create_reloadable_socket(
    host: str,
    port: int,
    *,
    backlog: int = 2048,
    family: socket.AddressFamily = socket.AF_INET,
) -> socket.socket:
    """Create a socket configured for hot reload.

    Args:
        host: Host address to bind
        port: Port to bind
        backlog: Listen backlog
        family: Socket family (AF_INET or AF_INET6)

    Returns:
        Configured socket ready for hot reload

    Example:
        sock = create_reloadable_socket("0.0.0.0", 8000)
        # Socket can be shared across worker generations

    """
    sock = socket.socket(family, socket.SOCK_STREAM)

    # Enable socket reuse for hot reload
    enable_socket_reuse(sock)

    # Bind and listen
    sock.bind((host, port))
    sock.listen(backlog)

    logger.debug("Created reloadable socket: %s:%d", host, port)
    return sock


def get_reload_status() -> dict[str, Any]:
    """Get status information about reload capability.

    Returns:
        Dictionary with reload status

    Example:
        status = get_reload_status()
        if status["supported"]:
            print("Hot reload is supported!")

    """
    return {
        "supported": is_hot_reload_supported(),
        "so_reuseport_available": hasattr(socket, "SO_REUSEPORT"),
        "platform": os.uname().sysname if hasattr(os, "uname") else "unknown",
    }


def should_drain_worker(
    worker_generation: WorkerGeneration,
    current_generation: int,
    drain_timeout: float,
) -> bool:
    """Determine if worker should start draining.

    Workers from old generations should drain after new workers start.

    Args:
        worker_generation: Worker's generation info
        current_generation: Current generation number
        drain_timeout: Maximum time to wait before forcing drain

    Returns:
        True if worker should drain

    """
    # If worker is from old generation, drain
    if worker_generation.is_old_generation(current_generation):
        return True

    # If worker has been running longer than drain timeout, drain
    if worker_generation.uptime > drain_timeout:
        return True

    return False


def wait_for_workers_to_drain(
    workers: list[Any],
    timeout: float,
    *,
    check_interval: float = 0.5,
) -> bool:
    """Wait for old workers to drain gracefully.

    Args:
        workers: List of worker handles
        timeout: Maximum time to wait (seconds)
        check_interval: How often to check worker status (seconds)

    Returns:
        True if all workers drained, False if timeout

    """
    deadline = time.monotonic() + timeout
    remaining = [w for w in workers if w.target.is_alive()]

    logger.info("Waiting for %d old worker(s) to drain...", len(remaining))

    while remaining and time.monotonic() < deadline:
        time.sleep(check_interval)

        # Check which workers have finished
        still_alive = []
        for worker in remaining:
            if worker.target.is_alive():
                still_alive.append(worker)
            else:
                logger.debug("Worker %d drained successfully", worker.worker_id)

        remaining = still_alive

        if remaining:
            logger.debug("%d worker(s) still draining...", len(remaining))

    if remaining:
        logger.warning(
            "%d worker(s) did not drain within timeout (%.1fs)",
            len(remaining),
            timeout,
        )
        return False

    logger.info("All old workers drained successfully")
    return True
