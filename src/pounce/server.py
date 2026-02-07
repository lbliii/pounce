"""
Server — orchestrates the full pounce lifecycle.

Manages the state machine:
    CONFIG → DETECT → BIND → LIFESPAN → SERVE → SHUTDOWN

When ``workers == 1`` the server runs a single-worker fast path with no
supervisor overhead.  When ``workers > 1`` the supervisor spawns and
monitors worker threads (nogil) or processes (GIL).

Signal handling: SIGINT/SIGTERM trigger graceful shutdown.

"""

import asyncio
import logging
import signal
import socket
import sys
import threading

from pounce._runtime import WorkerMode, detect_worker_mode, is_gil_enabled
from pounce._types import ASGIApp
from pounce.asgi.lifespan import run_lifespan
from pounce.config import ServerConfig
from pounce.logging import configure_logging
from pounce.net.listener import create_listener, create_listeners
from pounce.net.tls import create_tls_context, is_tls_configured
from pounce.supervisor import Supervisor
from pounce.worker import Worker

logger = logging.getLogger("pounce")

class Server:
    """Top-level server that orchestrates the full lifecycle.

    Creates the socket(s), runs lifespan events, starts workers (via the
    supervisor when multi-worker), and handles shutdown signals.

    Args:
        config: Immutable server configuration.
        app: The ASGI application to serve.

    Example:
        >>> from pounce.config import ServerConfig
        >>> server = Server(ServerConfig(), app)
        >>> server.run()

    """

    __slots__ = (
        "_config",
        "_app",
        "_ssl_context",
        "_shutdown_event",
        "_loop",
        "_async_shutdown",
        "_supervisor",
    )

    def __init__(self, config: ServerConfig, app: ASGIApp) -> None:
        self._config = config
        self._app = app
        self._ssl_context = None
        self._shutdown_event = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._async_shutdown: asyncio.Event | None = None
        self._supervisor: Supervisor | None = None

    def run(self) -> None:
        """Start the server (blocking).

        Lifecycle:
        1. Configure logging
        2. Resolve effective worker count and detect worker mode
        3. Create TLS context (if configured)
        4. Print startup banner
        5. Bind socket(s)
        6. Run ASGI lifespan startup (once, in the main thread)
        7. Start worker(s) — single-worker fast path or supervisor
        8. Wait for shutdown signal
        9. Run ASGI lifespan shutdown
        10. Close socket(s)

        """
        configure_logging(self._config)

        effective_workers = self._config.resolve_workers()
        mode = detect_worker_mode()

        # Create TLS context if certificate is configured
        if is_tls_configured(self._config):
            self._ssl_context = create_tls_context(self._config)
            logger.info("TLS enabled")

        self._print_banner(effective_workers, mode)

        if effective_workers == 1:
            self._run_single()
        else:
            self._run_multi(effective_workers, mode)

    def shutdown(self) -> None:
        """Request graceful shutdown. Thread-safe and idempotent.

        Can be called from any thread to stop a running server. In
        single-worker mode this wakes the asyncio event loop via
        ``call_soon_threadsafe``. In multi-worker mode this delegates
        to the supervisor's shutdown.

        Safe to call before ``run()`` — the server will exit immediately
        on startup. Safe to call multiple times.

        """
        self._shutdown_event.set()

        # Multi-worker: delegate to supervisor
        supervisor = self._supervisor
        if supervisor is not None:
            supervisor.shutdown()

        # Single-worker: bridge to the asyncio event
        loop = self._loop
        async_shutdown = self._async_shutdown
        if loop is not None and async_shutdown is not None:
            try:
                loop.call_soon_threadsafe(async_shutdown.set)
            except RuntimeError:
                pass  # Loop already closed

    # ------------------------------------------------------------------
    # Single-worker fast path (no supervisor overhead)
    # ------------------------------------------------------------------

    def _run_single(self) -> None:
        """Run with a single worker — no supervisor, minimal overhead."""
        if self._config.reload:
            self._run_single_with_reload()
            return

        sock = create_listener(self._config)
        actual_addr = sock.getsockname()

        logger.info(
            "Pounce server starting on %s:%d (single worker)",
            actual_addr[0],
            actual_addr[1],
        )

        try:
            asyncio.run(self._run_single_async(sock))
        except KeyboardInterrupt:
            pass
        finally:
            sock.close()
            logger.info("Pounce server stopped")

    def _run_single_with_reload(self) -> None:
        """Single-worker mode with auto-reload on source changes.

        Runs the worker in a loop. When the file watcher detects changes
        it sets ``_reload_requested`` and signals shutdown, causing the
        asyncio loop to exit. The outer loop then restarts the worker
        with a fresh socket and event loop.

        """
        from pathlib import Path

        from pounce._reload import watch_for_changes

        reload_requested = threading.Event()
        stop_watcher = threading.Event()

        def _on_change() -> None:
            reload_requested.set()
            self.shutdown()

        watcher = threading.Thread(
            target=watch_for_changes,
            args=([Path.cwd()], _on_change),
            kwargs={"stop_event": stop_watcher},
            daemon=True,
        )
        watcher.start()

        try:
            while True:
                # Reset state for each iteration
                self._shutdown_event.clear()
                reload_requested.clear()
                self._loop = None
                self._async_shutdown = None

                sock = create_listener(self._config)
                actual_addr = sock.getsockname()

                logger.info(
                    "Pounce server starting on %s:%d (single worker, reload)",
                    actual_addr[0],
                    actual_addr[1],
                )

                try:
                    asyncio.run(self._run_single_async(sock))
                except KeyboardInterrupt:
                    break
                finally:
                    sock.close()

                if reload_requested.is_set():
                    logger.info("Reloading...")
                    continue
                break
        finally:
            stop_watcher.set()
            logger.info("Pounce server stopped")

    async def _run_single_async(self, sock: socket.socket) -> None:
        """Async entry point for single-worker mode."""
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._async_shutdown = asyncio.Event()

        # If shutdown() was called before the loop started, honour it
        if self._shutdown_event.is_set():
            self._async_shutdown.set()

        def _on_signal() -> None:
            """Set both events so shutdown() and signal paths converge."""
            self._shutdown_event.set()
            if self._async_shutdown is not None:
                self._async_shutdown.set()

        # Install signal handlers (main thread only).
        # NotImplementedError: Windows.
        # RuntimeError: non-main thread or non-main interpreter (Python 3.14t).
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _on_signal)
            except (NotImplementedError, RuntimeError):
                pass

        worker = Worker(
            self._config, self._app, sock,
            worker_id=0,
            ssl_context=self._ssl_context,
        )

        async with run_lifespan(self._app, self._config):
            server = await asyncio.start_server(
                worker._handle_connection,
                sock=sock,
                ssl=self._ssl_context,
            )

            logger.info("Ready to accept connections")

            try:
                await self._async_shutdown.wait()
            finally:
                logger.info("Shutting down...")
                server.close()
                await server.wait_closed()
                self._loop = None

    # ------------------------------------------------------------------
    # Multi-worker path (supervisor)
    # ------------------------------------------------------------------

    def _run_multi(self, effective_workers: int, mode: WorkerMode) -> None:
        """Run with multiple workers managed by the supervisor.

        Lifespan runs once in the main process/thread before workers
        are spawned.  Workers do not run lifespan.

        When ``--reload`` is active, a file watcher thread runs alongside
        the supervisor and triggers ``restart_workers()`` on changes.

        """
        sockets = create_listeners(self._config, effective_workers)

        # Figure out the actual bind address from the first socket
        actual_addr = sockets[0].getsockname()
        logger.info(
            "Pounce server starting on %s:%d (%d %s workers)",
            actual_addr[0],
            actual_addr[1],
            effective_workers,
            mode,
        )

        self._supervisor = Supervisor(
            self._config, self._app, mode=mode, ssl_context=self._ssl_context,
        )

        # Start file watcher for reload mode
        stop_watcher: threading.Event | None = None
        if self._config.reload:
            from pathlib import Path

            from pounce._reload import watch_for_changes

            stop_watcher = threading.Event()
            supervisor_ref = self._supervisor

            def _on_change() -> None:
                supervisor_ref.restart_workers()

            watcher = threading.Thread(
                target=watch_for_changes,
                args=([Path.cwd()], _on_change),
                kwargs={"stop_event": stop_watcher},
                daemon=True,
            )
            watcher.start()

        # Run lifespan once in the main thread, then start supervisor
        try:
            asyncio.run(self._run_lifespan_then_supervise(self._supervisor, sockets))
        except KeyboardInterrupt:
            pass
        finally:
            if stop_watcher is not None:
                stop_watcher.set()
            self._close_sockets(sockets)
            logger.info("Pounce server stopped")

    async def _run_lifespan_then_supervise(
        self,
        supervisor: Supervisor,
        sockets: list[socket.socket],
    ) -> None:
        """Run lifespan in the main thread, then hand off to supervisor."""
        async with run_lifespan(self._app, self._config):
            # The supervisor blocks (it runs its own watchdog loop), so
            # we run it in a thread executor to keep the asyncio loop
            # alive for lifespan shutdown.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, supervisor.run, sockets)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _print_banner(self, effective_workers: int, mode: WorkerMode) -> None:
        """Print the startup banner to stderr."""
        scheme = "https" if self._config.ssl_certfile else "http"
        url = f"{scheme}://{self._config.host}:{self._config.port}"

        gil_status = "nogil" if not is_gil_enabled() else "GIL"
        mode_label = f"{mode}s" if effective_workers > 1 else "single"

        lines = [
            "",
            f"  pounce v{_get_version()} (Python {sys.version.split()[0]}, {gil_status})",
            f"  -> {url}",
            f"  -> workers: {effective_workers} ({mode_label})",
        ]
        if self._ssl_context is not None:
            lines.append("  -> tls: enabled")
        if self._config.compression:
            from pounce._compression import _ENCODING_PRIORITY

            enc_list = ", ".join(_ENCODING_PRIORITY)
            lines.append(f"  -> compression: enabled ({enc_list})")
        if self._config.server_timing:
            lines.append("  -> server-timing: enabled")
        if self._config.root_path:
            lines.append(f"  -> root_path: {self._config.root_path}")
        if self._config.reload:
            lines.append("  -> reload: enabled (watching for changes)")
        if self._config.keep_alive_timeout != 5.0:
            lines.append(f"  -> keep-alive: {self._config.keep_alive_timeout}s")
        if self._config.max_requests_per_connection > 0:
            lines.append(
                f"  -> max-requests/conn: {self._config.max_requests_per_connection}"
            )
        lines.append("")

        sys.stderr.write("\n".join(lines) + "\n")

    @staticmethod
    def _close_sockets(sockets: list[socket.socket]) -> None:
        """Close all sockets, deduplicating shared-fd sockets.

        On macOS (no SO_REUSEPORT) all workers share the same socket fd.
        Deduplicate by fd and guard against already-closed fds to avoid
        ``ValueError: Invalid file descriptor: -1`` on shutdown.

        """
        closed: set[int] = set()
        for sock in sockets:
            try:
                fd = sock.fileno()
            except OSError:
                continue  # socket already closed
            if fd != -1 and fd not in closed:
                closed.add(fd)
                try:
                    sock.close()
                except OSError:
                    pass  # already closed by another worker

def _get_version() -> str:
    """Get the pounce version string."""
    try:
        from pounce import __version__

        return __version__
    except ImportError:
        return "0.0.0"
