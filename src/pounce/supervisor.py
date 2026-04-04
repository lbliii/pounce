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

import concurrent.futures
import contextlib
import logging
import multiprocessing
import queue
import signal
import socket
import ssl
import threading
import time
from typing import Any, Final, Protocol

from pounce._errors import SupervisorError
from pounce._runtime import (
    WorkerMode,
    detect_worker_mode,
    resolve_worker_execution_mode,
)
from pounce._state import (
    RELOAD_COMPLETE,
    RELOAD_FAILED,
    RELOAD_START,
    SUPERVISOR_ALL_STOPPED,
    SUPERVISOR_SHUTDOWN,
    SUPERVISOR_STARTING,
    WORKER_CRASHED,
    WORKER_MAX_RESTARTS,
    WORKER_STARTED,
    dispatch,
)
from pounce._types import ASGIApp
from pounce.accept_distributor import AcceptDistributor, is_shared_socket
from pounce.async_pool import AsyncPool
from pounce.config import ServerConfig
from pounce.h3_worker import H3Worker
from pounce.lifecycle import LifecycleCollector
from pounce.sync_protocol import SyncApp
from pounce.sync_worker import SyncWorker
from pounce.worker import Worker

logger = logging.getLogger("pounce.supervisor")


def _parallel_join_targets(
    targets: list[threading.Thread | multiprocessing.Process],
    timeout_per: float,
) -> None:
    """Join each worker thread/process in parallel with its own timeout.

    ``shutdown_timeout`` is applied **per worker** (not split across N workers).
    Wall-clock time is roughly ``timeout_per`` when all workers finish together,
    instead of one shared deadline that starved later workers in the join order.

    Args:
        targets: Threads or processes to ``join``.
        timeout_per: Maximum seconds to wait for each target.

    """

    if not targets:
        return

    def join_one(target: threading.Thread | multiprocessing.Process) -> None:
        target.join(timeout=timeout_per)

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(targets)) as pool:
        list(pool.map(join_one, targets))


# Maximum worker restarts within `_RESTART_WINDOW` seconds before giving up
_MAX_RESTARTS: Final = 5
_RESTART_WINDOW: Final = 60.0  # seconds
_HEALTH_CHECK_INTERVAL: Final = 1.0  # seconds


class TCPWorker(Protocol):
    """Structural contract for TCP workers (Worker and SyncWorker).

    Both async and sync workers implement this interface. The supervisor
    uses it for lifecycle management: spawning, draining, and idle checks.

    """

    def run(self) -> None: ...
    def set_lifespan_state(self, state: dict[str, Any]) -> None: ...
    def start_draining(self) -> None: ...
    def is_idle(self) -> bool: ...


class _WorkerHandle:
    """Metadata about a running worker (thread or process)."""

    __slots__ = (
        "generation",
        "restart_count",
        "restarts",
        "started_at",
        "target",
        "worker",
        "worker_id",
    )

    def __init__(
        self,
        worker_id: int,
        target: threading.Thread | multiprocessing.Process,
        worker: TCPWorker | None,
        generation: int = 0,
    ) -> None:
        self.worker_id = worker_id
        self.target = target
        self.worker = worker  # Store Worker instance for drain control (None in process mode)
        self.started_at = time.monotonic()
        self.restart_count = 0
        self.restarts: list[float] = []  # timestamps of recent restarts
        self.generation = generation  # Used for rolling restart


class _H3WorkerHandle:
    """Metadata about a running H3 worker (UDP/QUIC)."""

    __slots__ = ("target", "worker_id")

    def __init__(
        self,
        worker_id: int,
        target: threading.Thread | multiprocessing.Process,
    ) -> None:
        self.worker_id = worker_id
        self.target = target


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
        "_accept_distributor_handle",
        "_app",
        "_app_path",
        "_async_pool",
        "_async_pool_handle",
        "_config",
        "_conn_queue",
        "_effective_workers",
        "_execution_mode",
        "_generation",
        "_h3_handles",
        "_handles",
        "_lifecycle_collector",
        "_lifecycle_lock",
        "_lifespan_state",
        "_mode",
        "_per_worker_max",
        "_reload_in_progress",
        "_shutdown_event",
        "_sockets",
        "_ssl_context",
        "_sync_app",
        "_udp_sockets",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        *,
        mode: WorkerMode | None = None,
        ssl_context: ssl.SSLContext | None = None,
        lifecycle_collector: LifecycleCollector | None = None,
        app_path: str | None = None,
        sync_app: SyncApp | None = None,
    ) -> None:
        self._config = config
        self._app = app
        self._app_path = app_path
        self._sync_app = sync_app
        self._mode: WorkerMode = mode or detect_worker_mode()
        self._execution_mode = resolve_worker_execution_mode(config.worker_mode)
        # Sync workers only supported in thread mode (3.14t). On GIL/process, fall back to async.
        if self._execution_mode == "sync" and self._mode == "process":
            logger.warning(
                "worker_mode='sync' is only supported in thread mode (3.14t). "
                "Falling back to async workers."
            )
            self._execution_mode = "async"
        self._shutdown_event = threading.Event()
        self._async_pool: AsyncPool | None = None
        self._async_pool_handle: threading.Thread | None = None
        self._accept_distributor_handle: threading.Thread | None = None
        self._conn_queue: queue.Queue[tuple[socket.socket, object]] | None = None
        self._lifecycle_lock = threading.Lock()
        self._reload_in_progress = False
        self._handles: list[_WorkerHandle] = []
        self._h3_handles: list[_H3WorkerHandle] = []
        self._sockets: list[socket.socket] = []
        self._udp_sockets: list[socket.socket] = []
        self._effective_workers = config.resolve_workers()
        self._ssl_context = ssl_context
        self._lifecycle_collector = lifecycle_collector
        self._lifespan_state: dict[str, Any] = {}  # Set after lifespan startup
        self._per_worker_max = 0
        self._generation = 0  # Incremented on each reload

    @property
    def mode(self) -> WorkerMode:
        """The active worker mode (``"thread"`` or ``"process"``)."""
        return self._mode

    @property
    def worker_count(self) -> int:
        """Number of workers the supervisor manages."""
        return self._effective_workers

    def set_lifespan_state(self, state: dict[str, Any]) -> None:
        """Set the lifespan state dict to be shared with all workers.

        Args:
            state: The state dict populated during lifespan startup.

        """
        self._lifespan_state = state

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        sockets: list[socket.socket],
        udp_sockets: list[socket.socket] | None = None,
    ) -> None:
        """Start all workers and block until shutdown.

        Installs signal handlers, spawns workers, runs the health-check
        loop, then joins all workers on shutdown.

        Args:
            sockets: One TCP socket per worker, created by
                ``create_listeners()``.
            udp_sockets: Optional UDP sockets for HTTP/3 workers.

        """
        if len(sockets) != self._effective_workers:
            msg = f"Expected {self._effective_workers} sockets, got {len(sockets)}"
            raise SupervisorError(msg)

        self._sockets = sockets
        self._udp_sockets = udp_sockets or []
        self._install_signals()

        self._per_worker_max = (
            self._config.max_connections // self._effective_workers
            if self._config.max_connections > 0
            else 0
        )

        exec_label = f"{self._execution_mode}+" if self._execution_mode == "sync" else ""
        dispatch(
            SUPERVISOR_STARTING,
            count=self._effective_workers,
            mode=f"{exec_label}{self._mode}",
        )

        self._setup_sync_infrastructure()

        # Spawn initial TCP workers
        for i in range(self._effective_workers):
            self._spawn_worker(i)

        # Spawn H3 workers when UDP sockets provided
        if self._udp_sockets:
            if len(self._udp_sockets) != self._effective_workers:
                msg = (
                    f"Expected {self._effective_workers} UDP sockets, got {len(self._udp_sockets)}"
                )
                raise SupervisorError(msg)
            for i in range(self._effective_workers):
                self._spawn_h3_worker(i)

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

    def _signal_workers_start_draining(self) -> None:
        """Mark async workers as draining (503 new connections) during shutdown.

        Thread-mode workers expose a ``Worker`` / ``SyncWorker`` instance; process
        workers do not (``handle.worker`` is None).
        """
        for handle in self._handles:
            if handle.worker is not None:
                handle.worker.start_draining()

    def restart_workers(self) -> None:
        """Gracefully restart all workers (for dev reload).

        Signals all running workers to stop, waits for them to drain,
        clears the shutdown event, and spawns fresh workers.

        Serialized with graceful_reload and watch-loop respawns via
        _lifecycle_lock. Skips if a reload is already in progress.

        When an ``app_path`` was provided and workers run as threads,
        the app module is reimported so that code changes on disk take
        effect.  Process-based workers get fresh imports automatically
        on fork and don't need explicit reimport.

        """
        with self._lifecycle_lock:
            if self._reload_in_progress:
                logger.debug("Restart already in progress — skipping")
                return
            self._reload_in_progress = True
        try:
            self._restart_workers_impl()
        finally:
            with self._lifecycle_lock:
                self._reload_in_progress = False

    def _restart_workers_impl(self) -> None:
        """Internal implementation of restart_workers (no lock)."""
        dispatch(RELOAD_START)

        # Reimport the app to pick up code changes (thread mode only —
        # process mode forks a new interpreter with a clean module cache).
        if self._app_path and self._mode == "thread":
            try:
                from pounce._importer import reimport_app

                self._app = reimport_app(self._app_path)
            except Exception:
                logger.exception("Reload failed — restarting with previous version")
                dispatch(RELOAD_FAILED, error="import error")

        # Signal all workers to stop
        self._shutdown_event.set()
        self._signal_workers_start_draining()

        # Join with timeout (AcceptDistributor, AsyncPool, TCP and H3 workers).
        # ``shutdown_timeout`` is per auxiliary thread and per worker (parallel joins).
        per = self._config.shutdown_timeout
        if self._accept_distributor_handle is not None:
            self._accept_distributor_handle.join(timeout=per)
            self._accept_distributor_handle = None
        if self._async_pool_handle is not None:
            self._async_pool_handle.join(timeout=per)
            if self._async_pool_handle.is_alive():
                logger.warning(
                    "AsyncPool thread still alive after shutdown timeout during restart."
                )
            self._async_pool_handle = None
        if self._handles:
            _parallel_join_targets([h.target for h in self._handles], per)
        for handle in self._handles:
            if handle.target.is_alive():
                self._force_stop(handle, per)
        if self._h3_handles:
            _parallel_join_targets([h.target for h in self._h3_handles], per)
        for handle in self._h3_handles:
            if handle.target.is_alive():
                logger.warning(
                    "H3 worker %d thread did not finish join within %.1fs — "
                    "OS threads cannot be killed; remaining work may run until process exit",
                    handle.worker_id,
                    per,
                )

        # Thread mode: cannot force-kill threads. If any old worker still alive,
        # do not spawn replacements — would cause split-brain (old + new serving).
        if self._mode == "thread":
            still_alive: list[_WorkerHandle | _H3WorkerHandle] = [
                h for h in self._handles if h.target.is_alive()
            ]
            still_alive += [h for h in self._h3_handles if h.target.is_alive()]
            if still_alive:
                logger.warning(
                    "%d thread worker(s) still alive after shutdown — not respawning "
                    "(would cause split-brain). Wait for them to drain or restart process.",
                    len(still_alive),
                )
                return

        # Clear shutdown event for new workers
        self._shutdown_event.clear()

        # Reset restart counts for fresh workers
        self._handles.clear()
        self._h3_handles.clear()

        self._setup_sync_infrastructure()

        # Respawn TCP workers
        for i in range(self._effective_workers):
            self._spawn_worker(i)
        # Respawn H3 workers (symmetric with TCP)
        for i in range(self._effective_workers):
            if self._udp_sockets and i < len(self._udp_sockets):
                self._spawn_h3_worker(i)

        dispatch(RELOAD_COMPLETE, workers=self._effective_workers)

    def graceful_reload(self) -> None:
        """Perform zero-downtime rolling restart of all workers.

        This method implements a rolling restart strategy:
        1. Reimport the app (thread mode only)
        2. Spawn new worker generation
        3. Mark old workers for draining (finish existing, reject new connections)
        4. Wait for old workers to become idle
        5. Shut down old workers

        This ensures zero dropped requests during code reload.

        Note: Only works in thread mode. In process mode, falls back to
        restart_workers() which has brief downtime.

        """
        if self._mode != "thread":
            logger.warning(
                "Graceful reload only supported in thread mode. Falling back to restart_workers()."
            )
            self.restart_workers()
            return

        with self._lifecycle_lock:
            if self._reload_in_progress:
                logger.debug("Reload already in progress — skipping graceful_reload")
                return
            self._reload_in_progress = True
        try:
            self._graceful_reload_impl()
        finally:
            with self._lifecycle_lock:
                self._reload_in_progress = False

    def _graceful_reload_impl(self) -> None:
        """Internal implementation of graceful_reload (no lock)."""
        dispatch(RELOAD_START)

        # Reimport the app to pick up code changes (thread mode only)
        if self._app_path:
            try:
                from pounce._importer import reimport_app

                self._app = reimport_app(self._app_path)
                logger.info("Successfully reimported app from %s", self._app_path)
            except Exception:
                logger.exception("Reload failed — continuing with previous version")
                dispatch(RELOAD_FAILED, error="import error")

        # Keep track of old workers
        old_handles = list(self._handles)
        old_generation = self._generation

        # Increment generation for new workers
        self._generation += 1

        # Spawn new workers (same number as before)
        logger.info(
            "Spawning %d new worker(s) (generation %d)...",
            self._effective_workers,
            self._generation,
        )
        new_handles: list[_WorkerHandle] = []
        for i in range(self._effective_workers):
            worker = self._create_worker(
                worker_id=i + self._effective_workers,
                socket_index=i,
            )

            target = threading.Thread(
                target=worker.run,
                name=f"pounce-worker-gen{self._generation}-{i}",
                daemon=True,
            )
            target.start()

            handle = _WorkerHandle(
                worker_id=i + self._effective_workers,
                target=target,
                worker=worker,
                generation=self._generation,
            )
            new_handles.append(handle)

        logger.info("New workers spawned. Draining old workers (generation %d)...", old_generation)

        # Mark old workers for draining
        for handle in old_handles:
            if handle.worker is not None:
                handle.worker.start_draining()

        # Wait for old workers to finish existing connections
        reload_timeout = self._config.reload_timeout
        deadline = time.monotonic() + reload_timeout

        for handle in old_handles:
            if handle.worker is None:
                continue

            # Poll until worker is idle or timeout
            while time.monotonic() < deadline:
                if handle.worker.is_idle():
                    logger.info(
                        "Worker %d (generation %d) is idle", handle.worker_id, handle.generation
                    )
                    break
                time.sleep(0.1)  # Poll every 100ms
            else:
                # Timeout reached
                logger.warning(
                    "Worker %d (generation %d) did not become idle after %.1fs — forcing shutdown",
                    handle.worker_id,
                    handle.generation,
                    reload_timeout,
                )

        # Join old workers — ``shutdown_timeout`` per worker (parallel joins).
        join_per = self._config.shutdown_timeout
        if old_handles:
            _parallel_join_targets([h.target for h in old_handles], join_per)
        for handle in old_handles:
            if handle.target.is_alive():
                self._force_stop(handle, join_per)

        # Replace handles with new generation
        self._handles = new_handles

        dispatch(RELOAD_COMPLETE, workers=len(new_handles), generation=self._generation)

    # ------------------------------------------------------------------
    # Spawning
    # ------------------------------------------------------------------

    def _setup_sync_infrastructure(self) -> None:
        """Create AsyncPool and AcceptDistributor for sync worker mode."""
        use_sync = self._mode == "thread" and self._execution_mode == "sync"
        use_accept_distributor = (
            use_sync and self._effective_workers > 1 and is_shared_socket(self._sockets)
        )
        if use_sync:
            self._async_pool = AsyncPool(
                self._config,
                self._app,
                shutdown_event=self._shutdown_event,
                ssl_context=self._ssl_context,
                lifecycle_collector=self._lifecycle_collector,
            )
            self._async_pool.set_lifespan_state(self._lifespan_state)
            self._async_pool_handle = threading.Thread(
                target=self._async_pool.run,
                name="pounce-async-pool",
                daemon=True,
            )
            self._async_pool_handle.start()
            logger.debug("AsyncPool started for streaming/WebSocket handoffs")

        if use_accept_distributor:
            shared_queue: queue.Queue[tuple[socket.socket, object]] = queue.Queue()
            distributor = AcceptDistributor(
                self._sockets[0],
                shared_queue,
                shutdown_event=self._shutdown_event,
                ssl_context=self._ssl_context,
            )
            self._accept_distributor_handle = threading.Thread(
                target=distributor.run,
                name="pounce-accept-distributor",
                daemon=True,
            )
            self._accept_distributor_handle.start()
            logger.debug(
                "AcceptDistributor started (shared queue, %d workers)",
                self._effective_workers,
            )
            self._conn_queue = shared_queue
        else:
            self._conn_queue = None

    def _create_worker(self, worker_id: int, socket_index: int) -> Worker | SyncWorker:
        """Create a Worker or SyncWorker based on the execution mode."""
        use_sync = self._mode == "thread" and self._execution_mode == "sync"
        if use_sync:
            worker_sock: socket.socket | None = self._sockets[socket_index]
            if self._conn_queue is not None:
                worker_sock = None
            worker: Worker | SyncWorker = SyncWorker(
                self._config,
                self._app,
                worker_sock,
                worker_id=worker_id,
                shutdown_event=self._shutdown_event,
                ssl_context=self._ssl_context,
                lifecycle_collector=self._lifecycle_collector,
                async_pool=self._async_pool,
                conn_queue=self._conn_queue,
                sync_app=self._sync_app,
            )
        else:
            worker = Worker(
                self._config,
                self._app,
                self._sockets[socket_index],
                worker_id=worker_id,
                shutdown_event=self._shutdown_event,
                max_connections=self._per_worker_max,
                ssl_context=self._ssl_context,
                lifecycle_collector=self._lifecycle_collector,
            )
        worker.set_lifespan_state(self._lifespan_state)
        return worker

    def _spawn_worker(self, worker_id: int) -> None:
        """Create and start a single worker."""
        worker = self._create_worker(worker_id, socket_index=worker_id)

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

        # For graceful reload: store worker instance in thread mode (needed for drain control)
        # In process mode, worker runs in different process so we can't control it directly
        worker_ref = worker if self._mode == "thread" else None

        handle = _WorkerHandle(worker_id, target, worker_ref, generation=self._generation)
        # Replace existing handle if this is a restart
        if worker_id < len(self._handles):
            self._handles[worker_id] = handle
        else:
            self._handles.append(handle)

        dispatch(WORKER_STARTED, worker_id=worker_id, mode=self._mode, generation=self._generation)

    def _spawn_h3_worker(self, worker_id: int) -> None:
        """Create and start a single H3 (HTTP/3) worker."""
        if not self._udp_sockets or worker_id >= len(self._udp_sockets):
            return
        if self._config.ssl_certfile is None or self._config.ssl_keyfile is None:
            return

        worker = H3Worker(
            self._config,
            self._app,
            self._udp_sockets[worker_id],
            worker_id=worker_id,
            shutdown_event=self._shutdown_event,
            ssl_certfile=self._config.ssl_certfile,
            ssl_keyfile=self._config.ssl_keyfile,
        )

        target = threading.Thread(
            target=worker.run,
            name=f"pounce-h3-worker-{worker_id}",
            daemon=True,
        )
        target.start()

        handle = _H3WorkerHandle(worker_id=worker_id, target=target)
        if worker_id < len(self._h3_handles):
            self._h3_handles[worker_id] = handle
        else:
            self._h3_handles.append(handle)

        logger.debug(
            "Started H3 worker %d (tid: %s)",
            worker_id,
            target.ident or "starting",
        )

    def _respawn_worker(self, worker_id: int) -> None:
        """Restart a crashed worker if within restart budget.

        Serialized with restart_workers/graceful_reload via _lifecycle_lock.
        Skips if a reload is in progress (avoids overlapping restarts).
        """
        with self._lifecycle_lock:
            if self._reload_in_progress:
                logger.debug("Reload in progress — skipping respawn of worker %d", worker_id)
                return
            if worker_id >= len(self._handles):
                return
            handle = self._handles[worker_id]
        now = time.monotonic()

        # Prune old restarts outside the window
        handle.restarts = [t for t in handle.restarts if now - t < _RESTART_WINDOW]

        if len(handle.restarts) >= _MAX_RESTARTS:
            dispatch(WORKER_MAX_RESTARTS, worker_id=worker_id, max_restarts=_MAX_RESTARTS)
            return

        handle.restarts.append(now)
        handle.restart_count += 1

        dispatch(WORKER_CRASHED, worker_id=worker_id, restart_count=handle.restart_count)

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
        """Wait for all workers to finish draining connections, then clean up.

        Signals shutdown to all workers, waits for them to finish processing
        active connections (see ``shutdown_timeout`` on ``ServerConfig``), then
        force-terminates **process** workers that haven't stopped.

        Workers will reject new connections but finish existing ones for
        clean shutdown (important for Kubernetes graceful termination).

        """
        total_workers = self._effective_workers + len(self._h3_handles)
        dispatch(SUPERVISOR_SHUTDOWN, count=total_workers)

        # Signal shutdown (may already be set)
        self._shutdown_event.set()
        self._signal_workers_start_draining()

        # ``shutdown_timeout`` is per auxiliary thread and per worker (parallel joins).
        per = self._config.shutdown_timeout
        if self._accept_distributor_handle is not None:
            self._accept_distributor_handle.join(timeout=per)
            if self._accept_distributor_handle.is_alive():
                logger.debug("AcceptDistributor still draining (will exit with process)")
        if self._async_pool_handle is not None:
            self._async_pool_handle.join(timeout=per)
            if self._async_pool_handle.is_alive():
                logger.debug("AsyncPool still draining (will exit with process)")

        if self._handles:
            _parallel_join_targets([h.target for h in self._handles], per)
        for handle in self._handles:
            if handle.target.is_alive():
                self._force_stop(handle, per)
            else:
                logger.debug("Worker %d stopped cleanly", handle.worker_id)

        if self._h3_handles:
            _parallel_join_targets([h.target for h in self._h3_handles], per)
        for handle in self._h3_handles:
            if handle.target.is_alive():
                logger.warning(
                    "H3 worker %d thread did not finish join within %.1fs — "
                    "OS threads cannot be killed; remaining work may run until process exit",
                    handle.worker_id,
                    per,
                )
            else:
                logger.debug("H3 worker %d stopped cleanly", handle.worker_id)

        dispatch(SUPERVISOR_ALL_STOPPED)

    def _force_stop(self, handle: _WorkerHandle, join_timeout: float) -> None:
        """Force-terminate a worker that did not drain in time.

        Process workers receive SIGTERM then SIGKILL. Thread workers cannot be
        terminated from Python; they are daemon threads and may outlive this join.
        """
        if isinstance(handle.target, multiprocessing.Process):
            logger.warning(
                "Worker %d (process) did not exit after %.1fs — sending SIGTERM",
                handle.worker_id,
                join_timeout,
            )
            handle.target.terminate()
            handle.target.join(timeout=2.0)
            if handle.target.is_alive():
                handle.target.kill()
        else:
            logger.warning(
                "Worker %d (thread) did not exit after %.1fs join — cannot force-kill; "
                "daemon thread may still run until process exit",
                handle.worker_id,
                join_timeout,
            )

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
