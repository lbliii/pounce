"""
Server — orchestrates the full pounce lifecycle.

Manages the state machine:
    CONFIG → DETECT → BIND → LIFESPAN → SERVE → SHUTDOWN

When ``workers == 1`` the server runs a single-worker fast path with no
supervisor overhead.  When ``workers > 1`` the supervisor spawns and
monitors worker threads (nogil) or processes (GIL).

Signal handling: SIGINT/SIGTERM trigger graceful shutdown.

"""

from __future__ import annotations

import asyncio
import logging
import signal
import socket
import sys

from pounce._runtime import WorkerMode, detect_worker_mode, is_gil_enabled
from pounce._types import ASGIApp
from pounce.asgi.lifespan import run_lifespan
from pounce.config import ServerConfig
from pounce.logging import configure_logging
from pounce.net.listener import create_listener, create_listeners
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

    __slots__ = ("_config", "_app")

    def __init__(self, config: ServerConfig, app: ASGIApp) -> None:
        self._config = config
        self._app = app

    def run(self) -> None:
        """Start the server (blocking).

        Lifecycle:
        1. Configure logging
        2. Resolve effective worker count and detect worker mode
        3. Print startup banner
        4. Bind socket(s)
        5. Run ASGI lifespan startup (once, in the main thread)
        6. Start worker(s) — single-worker fast path or supervisor
        7. Wait for shutdown signal
        8. Run ASGI lifespan shutdown
        9. Close socket(s)

        """
        configure_logging(self._config)

        effective_workers = self._config.resolve_workers()
        mode = detect_worker_mode()

        self._print_banner(effective_workers, mode)

        if effective_workers == 1:
            self._run_single()
        else:
            self._run_multi(effective_workers, mode)

    # ------------------------------------------------------------------
    # Single-worker fast path (no supervisor overhead)
    # ------------------------------------------------------------------

    def _run_single(self) -> None:
        """Run with a single worker — no supervisor, minimal overhead."""
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

    async def _run_single_async(self, sock: socket.socket) -> None:
        """Async entry point for single-worker mode."""
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        # Install signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, shutdown_event.set)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        worker = Worker(self._config, self._app, sock, worker_id=0)

        async with run_lifespan(self._app, self._config):
            server = await asyncio.start_server(
                worker._handle_connection,
                sock=sock,
            )

            logger.info("Ready to accept connections")

            try:
                await shutdown_event.wait()
            finally:
                logger.info("Shutting down...")
                server.close()
                await server.wait_closed()

    # ------------------------------------------------------------------
    # Multi-worker path (supervisor)
    # ------------------------------------------------------------------

    def _run_multi(self, effective_workers: int, mode: WorkerMode) -> None:
        """Run with multiple workers managed by the supervisor.

        Lifespan runs once in the main process/thread before workers
        are spawned.  Workers do not run lifespan.

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

        supervisor = Supervisor(self._config, self._app, mode=mode)

        # Run lifespan once in the main thread, then start supervisor
        try:
            asyncio.run(self._run_lifespan_then_supervise(supervisor, sockets))
        except KeyboardInterrupt:
            pass
        finally:
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
        if self._config.compression:
            lines.append("  -> compression: enabled (zstd, gzip)")
        if self._config.server_timing:
            lines.append("  -> server-timing: enabled")
        if self._config.root_path:
            lines.append(f"  -> root_path: {self._config.root_path}")
        lines.append("")

        sys.stderr.write("\n".join(lines) + "\n")

    @staticmethod
    def _close_sockets(sockets: list[socket.socket]) -> None:
        """Close all sockets, deduplicating shared-fd sockets."""
        closed: set[int] = set()
        for sock in sockets:
            fd = sock.fileno()
            if fd != -1 and fd not in closed:
                closed.add(fd)
                sock.close()


def _get_version() -> str:
    """Get the pounce version string."""
    try:
        from pounce import __version__

        return __version__
    except ImportError:
        return "0.0.0"
