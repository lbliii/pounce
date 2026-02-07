"""
Server — orchestrates the full pounce lifecycle.

Manages the state machine:
    CONFIG → BIND → LIFESPAN → SERVE → SHUTDOWN

Phase 1 runs a single worker. Phase 2 adds the supervisor for multiple
worker threads.

Signal handling: SIGINT/SIGTERM trigger graceful shutdown.

"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from pounce._types import ASGIApp
from pounce.asgi.lifespan import run_lifespan
from pounce.config import ServerConfig
from pounce.logging import configure_logging
from pounce.net.listener import create_listener
from pounce.worker import Worker

logger = logging.getLogger("pounce")


class Server:
    """Top-level server that orchestrates the full lifecycle.

    Creates the socket, runs lifespan events, starts the worker, and
    handles shutdown signals.

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

        Runs the full lifecycle:
        1. Configure logging
        2. Print startup banner
        3. Create and bind socket
        4. Run lifespan startup
        5. Start worker
        6. Wait for shutdown signal
        7. Run lifespan shutdown
        8. Close socket

        """
        configure_logging(self._config)
        self._print_banner()

        # Bind socket
        sock = create_listener(self._config)
        actual_addr = sock.getsockname()

        logger.info(
            "Pounce server starting on %s:%d",
            actual_addr[0],
            actual_addr[1],
        )

        # Run lifespan + worker in asyncio
        try:
            asyncio.run(self._run_async(sock))
        except KeyboardInterrupt:
            pass
        finally:
            sock.close()
            logger.info("Pounce server stopped")

    async def _run_async(self, sock) -> None:  # noqa: ANN001
        """Async entry point — runs lifespan and worker."""
        loop = asyncio.get_running_loop()
        shutdown_event = asyncio.Event()

        # Install signal handlers
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, shutdown_event.set)
            except NotImplementedError:
                # Windows doesn't support add_signal_handler
                pass

        worker = Worker(self._config, self._app, sock)

        async with run_lifespan(self._app, self._config):
            # Start the worker's server
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

    def _print_banner(self) -> None:
        """Print the startup banner to stderr."""
        scheme = "https" if self._config.ssl_certfile else "http"
        url = f"{scheme}://{self._config.host}:{self._config.port}"

        lines = [
            "",
            f"  pounce v{_get_version()} (Python {sys.version.split()[0]})",
            f"  → {url}",
            f"  → workers: {self._config.workers}",
        ]
        if self._config.compression:
            lines.append("  → compression: enabled (zstd, gzip)")
        if self._config.server_timing:
            lines.append("  → server-timing: enabled")
        if self._config.root_path:
            lines.append(f"  → root_path: {self._config.root_path}")
        lines.append("")

        sys.stderr.write("\n".join(lines) + "\n")


def _get_version() -> str:
    """Get the pounce version string."""
    try:
        from pounce import __version__
        return __version__
    except ImportError:
        return "0.0.0"
