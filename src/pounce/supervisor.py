"""
Supervisor — spawns, monitors, and restarts workers.

The supervisor sits between the ``Server`` and the ``Worker`` layer.  It
detects the GIL state at startup and spawns workers as either threads
(on nogil / 3.14t) or processes (on GIL builds).  The worker
implementation is identical in both modes — only the spawning mechanism
differs.

Responsibilities:
- Spawn N workers with their sockets
- Monitor worker health (is_alive check on a watchdog loop)
- Restart crashed workers (up to ``max_restarts`` per window)
- Coordinate graceful shutdown via ``threading.Event``
- Forward SIGINT/SIGTERM to workers

"""

import contextlib
import logging
import multiprocessing
import signal
import socket
import ssl
import threading
import time

from pounce._errors import SupervisorError
from pounce._runtime import WorkerMode, detect_worker_mode
from pounce._types import ASGIApp
from pounce.config import ServerConfig
from pounce.lifecycle import LifecycleCollector
from pounce.worker import Worker

logger = logging.getLogger("pounce.supervisor")

# Maximum worker restarts within `_RESTART_WINDOW` seconds before giving up
_MAX_RESTARTS = 5
_RESTART_WINDOW = 60.0  # seconds
_HEALTH_CHECK_INTERVAL = 1.0  # seconds


class _WorkerHandle:
    """Metadata about a running worker (thread or process)."""

    __slots__ = ("restart_count", "restarts", "started_at", "target", "worker_id")

    def __init__(self, worker_id: int, target: threading.Thread | multiprocessing.Process) -> None:
        self.worker_id = worker_id
        self.target = target
        self.started_at = time.monotonic()
        self.restart_count = 0
        self.restarts: list[float] = []  # timestamps of recent restarts


class Supervisor:
    """Spawn and supervise N workers as threads or processes.

    The supervisor detects the GIL state and picks the appropriate
    spawning strategy automatically.  Workers share the frozen
    ``ServerConfig`` and (in thread mode) the ASGI app reference.

    Args:
        config: Immutable server configuration.
        app: The ASGI application callable.
        mode: Override the auto-detected worker mode.  Pass ``None``
            (the default) to let the supervisor detect automatically.

    """

    __slots__ = (
        "_app",
        "_config",
        "_effective_workers",
        "_handles",
        "_lifecycle_collector",
        "_mode",
        "_shutdown_event",
        "_sockets",
        "_ssl_context",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        *,
        mode: WorkerMode | None = None,
        ssl_context: ssl.SSLContext | None = None,
        lifecycle_collector: LifecycleCollector | None = None,
    ) -> None:
        self._config = config
        self._app = app
        self._mode: WorkerMode = mode or detect_worker_mode()
        self._shutdown_event = threading.Event()
        self._handles: list[_WorkerHandle] = []
        self._sockets: list[socket.socket] = []
        self._effective_workers = config.resolve_workers()
        self._ssl_context = ssl_context
        self._lifecycle_collector = lifecycle_collector

    @property
    def mode(self) -> WorkerMode:
        """The active worker mode (``"thread"`` or ``"process"``)."""
        return self._mode

    @property
    def worker_count(self) -> int:
        """Number of workers the supervisor manages."""
        return self._effective_workers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, sockets: list[socket.socket]) -> None:
        """Start all workers and block until shutdown.

        Installs signal handlers, spawns workers, runs the health-check
        loop, then joins all workers on shutdown.

        Args:
            sockets: One socket per worker, created by
                ``create_listeners()``.

        """
        if len(sockets) != self._effective_workers:
            msg = f"Expected {self._effective_workers} sockets, got {len(sockets)}"
            raise SupervisorError(msg)

        self._sockets = sockets
        self._install_signals()

        logger.info(
            "Supervisor starting %d %s worker(s)",
            self._effective_workers,
            self._mode,
        )

        # Spawn initial workers
        for i in range(self._effective_workers):
            self._spawn_worker(i)

        # Health-check loop — blocks until shutdown
        try:
            self._watch()
        except KeyboardInterrupt:
            pass
        finally:
            self._drain()

    def shutdown(self) -> None:
        """Signal all workers to stop (non-blocking)."""
        self._shutdown_event.set()

    def restart_workers(self) -> None:
        """Gracefully restart all workers (for dev reload).

        Signals all running workers to stop, waits for them to drain,
        clears the shutdown event, and spawns fresh workers. Process-based
        workers get a fresh module import; thread-based workers reuse the
        existing app object but reinitialise their async loops.

        """
        logger.info("Restarting %d worker(s)...", self._effective_workers)

        # Signal all workers to stop
        self._shutdown_event.set()

        # Join with timeout
        deadline = time.monotonic() + self._config.shutdown_timeout
        for handle in self._handles:
            remaining = max(0.1, deadline - time.monotonic())
            handle.target.join(timeout=remaining)
            if handle.target.is_alive():
                self._force_stop(handle)

        # Clear shutdown event for new workers
        self._shutdown_event.clear()

        # Reset restart counts for fresh workers
        self._handles.clear()

        # Respawn all workers
        for i in range(self._effective_workers):
            self._spawn_worker(i)

        logger.info("All %d worker(s) restarted", self._effective_workers)

    # ------------------------------------------------------------------
    # Spawning
    # ------------------------------------------------------------------

    def _spawn_worker(self, worker_id: int) -> None:
        """Create and start a single worker."""
        per_worker_max = (
            self._config.max_connections // self._effective_workers
            if self._config.max_connections > 0
            else 0
        )

        worker = Worker(
            self._config,
            self._app,
            self._sockets[worker_id],
            worker_id=worker_id,
            shutdown_event=self._shutdown_event,
            max_connections=per_worker_max,
            ssl_context=self._ssl_context,
            lifecycle_collector=self._lifecycle_collector,
        )

        if self._mode == "thread":
            target: threading.Thread | multiprocessing.Process = threading.Thread(
                target=worker.run,
                name=f"pounce-worker-{worker_id}",
                daemon=True,
            )
        else:
            target = multiprocessing.Process(
                target=worker.run,
                name=f"pounce-worker-{worker_id}",
                daemon=True,
            )

        target.start()

        handle = _WorkerHandle(worker_id, target)
        # Replace existing handle if this is a restart
        if worker_id < len(self._handles):
            self._handles[worker_id] = handle
        else:
            self._handles.append(handle)

        logger.info(
            "Started %s worker %d (pid/tid: %s)",
            self._mode,
            worker_id,
            _target_id(target),
        )

    def _respawn_worker(self, worker_id: int) -> None:
        """Restart a crashed worker if within restart budget."""
        handle = self._handles[worker_id]
        now = time.monotonic()

        # Prune old restarts outside the window
        handle.restarts = [t for t in handle.restarts if now - t < _RESTART_WINDOW]

        if len(handle.restarts) >= _MAX_RESTARTS:
            logger.error(
                "Worker %d exceeded max restarts (%d in %.0fs) — not restarting",
                worker_id,
                _MAX_RESTARTS,
                _RESTART_WINDOW,
            )
            return

        handle.restarts.append(now)
        handle.restart_count += 1

        logger.warning(
            "Worker %d crashed, restarting (restart #%d)",
            worker_id,
            handle.restart_count,
        )

        self._spawn_worker(worker_id)

    # ------------------------------------------------------------------
    # Monitoring
    # ------------------------------------------------------------------

    def _watch(self) -> None:
        """Health-check loop — detects crashed workers and restarts them."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(timeout=_HEALTH_CHECK_INTERVAL)

            if self._shutdown_event.is_set():
                break

            for handle in self._handles:
                if not handle.target.is_alive():
                    # Check if this was an expected shutdown
                    if self._shutdown_event.is_set():
                        break

                    exit_info = ""
                    if isinstance(handle.target, multiprocessing.Process):
                        exit_info = f" (exitcode={handle.target.exitcode})"

                    logger.warning(
                        "Worker %d died%s",
                        handle.worker_id,
                        exit_info,
                    )
                    self._respawn_worker(handle.worker_id)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def _drain(self) -> None:
        """Wait for all workers to finish, then clean up."""
        logger.info("Shutting down %d worker(s)...", self._effective_workers)

        # Signal shutdown (may already be set)
        self._shutdown_event.set()

        # Join with timeout
        deadline = time.monotonic() + self._config.shutdown_timeout
        for handle in self._handles:
            remaining = max(0.1, deadline - time.monotonic())
            handle.target.join(timeout=remaining)

            if handle.target.is_alive():
                logger.warning(
                    "Worker %d did not stop within timeout — terminating",
                    handle.worker_id,
                )
                self._force_stop(handle)

        logger.info("All workers stopped")

    def _force_stop(self, handle: _WorkerHandle) -> None:
        """Force-terminate a worker that did not drain in time."""
        if isinstance(handle.target, multiprocessing.Process):
            handle.target.terminate()
            handle.target.join(timeout=2.0)
            if handle.target.is_alive():
                handle.target.kill()
        # Threads cannot be forcibly killed — they will exit when the
        # process exits since they are daemon threads.

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _install_signals(self) -> None:
        """Install SIGINT/SIGTERM handlers to trigger graceful shutdown.

        Only effective when the supervisor runs on the main thread (e.g.,
        direct testing).  In production the supervisor runs inside a
        ``run_in_executor`` thread, so ``signal.signal()`` will fail
        silently.  ``Server`` installs asyncio signal handlers that call
        ``supervisor.shutdown()`` instead.

        """

        def _handle_signal(signum: int, _frame: object) -> None:
            sig_name = signal.Signals(signum).name
            logger.info("Received %s — initiating shutdown", sig_name)
            self._shutdown_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(OSError, ValueError):
                signal.signal(sig, _handle_signal)


def _target_id(target: threading.Thread | multiprocessing.Process) -> str:
    """Return an identifier string for a thread or process."""
    if isinstance(target, multiprocessing.Process):
        return str(target.pid or "starting")
    return str(target.ident or "starting")
