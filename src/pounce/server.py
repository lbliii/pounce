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
import contextlib
import logging
import os
import signal
import socket
import sys
import threading

from pounce._runtime import WorkerMode, detect_worker_mode, is_gil_enabled
from pounce._types import ASGIApp
from pounce.asgi.lifespan import run_lifespan
from pounce.config import ServerConfig
from pounce.lifecycle import LifecycleCollector
from pounce.logging import configure_logging
from pounce.net.listener import cleanup_unix_socket, create_listener, create_listeners
from pounce.net.tls import create_tls_context, is_tls_configured
from pounce.supervisor import Supervisor
from pounce.worker import Worker, _worker_lifecycle_receive, _worker_lifecycle_send

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
        "_app",
        "_app_path",
        "_async_shutdown",
        "_config",
        "_lifecycle_collector",
        "_loop",
        "_shutdown_event",
        "_ssl_context",
        "_supervisor",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        *,
        app_path: str | None = None,
        lifecycle_collector: LifecycleCollector | None = None,
    ) -> None:
        self._config = config
        self._app = app
        self._app_path = app_path
        self._lifecycle_collector = lifecycle_collector
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

        # Configure OpenTelemetry if endpoint is set
        if self._config.otel_endpoint:
            try:
                from pounce._otel import configure_otel, is_otel_available

                if is_otel_available():
                    configure_otel(
                        endpoint=self._config.otel_endpoint,
                        service_name=self._config.otel_service_name,
                    )
                else:
                    logger.warning(
                        "OpenTelemetry endpoint configured but opentelemetry package not installed. "
                        "Install with: pip install opentelemetry-api opentelemetry-sdk "
                        "opentelemetry-exporter-otlp-proto-http"
                    )
            except Exception:
                logger.exception("Failed to configure OpenTelemetry")

        # Configure lifecycle logging if enabled
        if self._config.lifecycle_logging and self._lifecycle_collector is None:
            from pounce.lifecycle import LoggingCollector

            self._lifecycle_collector = LoggingCollector(
                slow_request_threshold_ms=self._config.log_slow_requests_threshold * 1000,
                log_format=self._config.log_format,
                health_check_path=self._config.health_check_path,
            )
            logger.debug("Lifecycle event logging enabled")

        # Configure Prometheus metrics if enabled
        if self._config.metrics_enabled and self._lifecycle_collector is None:
            from pounce.metrics import PrometheusCollector
            from pounce._metrics_handler import wrap_app_with_metrics

            self._lifecycle_collector = PrometheusCollector()
            # Wrap app to intercept /metrics requests
            self._app = wrap_app_with_metrics(
                self._app,
                self._lifecycle_collector,
                self._config.metrics_path,
            )
            logger.info("Prometheus metrics enabled at %s", self._config.metrics_path)

        # Configure rate limiting if enabled
        if self._config.rate_limit_enabled:
            from pounce._rate_limiter import RateLimiter, create_rate_limit_wrapper

            rate_limiter = RateLimiter(
                rate=self._config.rate_limit_requests_per_second,
                burst=self._config.rate_limit_burst,
            )
            self._app = create_rate_limit_wrapper(self._app, rate_limiter)
            logger.info(
                "Rate limiting enabled: %.1f req/s per IP (burst: %d)",
                self._config.rate_limit_requests_per_second,
                self._config.rate_limit_burst,
            )

        # Configure request queueing if enabled
        if self._config.request_queue_enabled:
            from pounce._request_queue import QueueMetrics, RequestQueue, create_queue_wrapper

            request_queue = RequestQueue(max_depth=self._config.request_queue_max_depth)
            queue_metrics = QueueMetrics()
            self._app = create_queue_wrapper(self._app, request_queue, queue_metrics)
            logger.info(
                "Request queueing enabled: max depth %d",
                self._config.request_queue_max_depth if self._config.request_queue_max_depth > 0 else -1,
            )

        # Configure Sentry error tracking if enabled
        if self._config.sentry_dsn:
            from pounce._sentry import create_sentry_wrapper, init_sentry, is_sentry_available

            if is_sentry_available():
                try:
                    init_sentry(
                        dsn=self._config.sentry_dsn,
                        environment=self._config.sentry_environment,
                        release=self._config.sentry_release,
                        traces_sample_rate=self._config.sentry_traces_sample_rate,
                        profiles_sample_rate=self._config.sentry_profiles_sample_rate,
                        debug=self._config.debug,
                    )
                    self._app = create_sentry_wrapper(self._app)
                    logger.info(
                        "Sentry error tracking enabled: environment=%s release=%s",
                        self._config.sentry_environment or "none",
                        self._config.sentry_release or "none",
                    )
                except Exception:
                    logger.exception("Failed to initialize Sentry")
            else:
                logger.warning(
                    "Sentry DSN configured but sentry-sdk not installed. "
                    "Install with: pip install sentry-sdk"
                )

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
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(async_shutdown.set)

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
            cleanup_unix_socket(self._config)
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

        watch_dirs = [Path.cwd(), *(Path(d).resolve() for d in self._config.reload_dirs)]
        watcher = threading.Thread(
            target=watch_for_changes,
            args=(watch_dirs, _on_change),
            kwargs={
                "stop_event": stop_watcher,
                "extra_extensions": self._config.reload_include,
            },
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
                    if self._app_path:
                        try:
                            from pounce._importer import reimport_app

                            self._app = reimport_app(self._app_path)
                        except Exception:
                            logger.exception(
                                "Reload failed — serving previous version"
                            )
                    continue
                break
        finally:
            stop_watcher.set()
            cleanup_unix_socket(self._config)
            logger.info("Pounce server stopped")

    async def _run_single_async(self, sock: socket.socket) -> None:
        """Async entry point for single-worker mode."""
        loop = asyncio.get_running_loop()
        self._loop = loop
        self._async_shutdown = asyncio.Event()

        # If shutdown() was called before the loop started, honour it
        if self._shutdown_event.is_set():
            self._async_shutdown.set()

        def _force_exit() -> None:
            """Second signal — exit immediately without waiting for drain."""
            logger.info("Second signal received — forcing immediate exit")
            os._exit(1)

        def _on_signal() -> None:
            """Set both events so shutdown() and signal paths converge."""
            self._shutdown_event.set()
            if self._async_shutdown is not None:
                self._async_shutdown.set()
            # Replace with force-exit handler so second Ctrl+C exits immediately
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.remove_signal_handler(sig)
                    loop.add_signal_handler(sig, _force_exit)

        # Install signal handlers (main thread only).
        # NotImplementedError: Windows.
        # RuntimeError: non-main thread or non-main interpreter (Python 3.14t).
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, _on_signal)

        # Note: SIGHUP not applicable in single-worker mode (no reload needed)

        worker = Worker(
            self._config,
            self._app,
            sock,
            worker_id=0,
            ssl_context=self._ssl_context,
            lifecycle_collector=self._lifecycle_collector,
        )

        async with run_lifespan(self._app, self._config) as lifespan_state:
            # Set lifespan state on worker for request scope injection
            worker.set_lifespan_state(lifespan_state)

            # Per-worker startup — in single-worker mode there's one
            # "worker" sharing the main event loop.  Send the scope so
            # @app.on_worker_startup hooks fire just like multi-worker.
            try:
                await asyncio.wait_for(
                    self._app(
                        {"type": "pounce.worker.startup", "worker_id": 0},
                        _worker_lifecycle_receive,
                        _worker_lifecycle_send,
                    ),
                    timeout=30.0,
                )
            except TimeoutError:
                pass  # App doesn't handle this scope type
            except Exception:
                logger.exception("Worker startup hook failed")
                return

            server = await asyncio.start_server(
                worker._handle_connection,
                sock=sock,
                ssl=self._ssl_context,
            )

            logger.info("Ready to accept connections")

            try:
                await self._async_shutdown.wait()
            finally:
                logger.info("Shutting down — draining connections...")
                server.close()  # Stop accepting new connections

                # Grace period: wait for in-flight connections to complete
                timeout = self._config.shutdown_timeout
                try:
                    await asyncio.wait_for(
                        server.wait_closed(),
                        timeout=timeout,
                    )
                    logger.info("All connections drained")
                except TimeoutError:
                    logger.warning(
                        "Shutdown timeout (%.1fs) — forcing remaining connections closed",
                        timeout,
                    )

                # Per-worker shutdown — clean up worker-scoped resources
                try:
                    await asyncio.wait_for(
                        self._app(
                            {"type": "pounce.worker.shutdown", "worker_id": 0},
                            _worker_lifecycle_receive,
                            _worker_lifecycle_send,
                        ),
                        timeout=10.0,
                    )
                except TimeoutError:
                    pass
                except Exception:
                    logger.exception("Worker shutdown hook failed")

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
            self._config,
            self._app,
            mode=mode,
            ssl_context=self._ssl_context,
            lifecycle_collector=self._lifecycle_collector,
            app_path=self._app_path,
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

            watch_dirs = [Path.cwd(), *(Path(d).resolve() for d in self._config.reload_dirs)]
            watcher = threading.Thread(
                target=watch_for_changes,
                args=(watch_dirs, _on_change),
                kwargs={
                    "stop_event": stop_watcher,
                    "extra_extensions": self._config.reload_include,
                },
                daemon=True,
            )
            watcher.start()

        # Run lifespan once in the main thread, then start supervisor
        try:
            asyncio.run(self._run_lifespan_then_supervise(self._supervisor, sockets))
        except KeyboardInterrupt:
            # Signal handler couldn't be installed (Windows, non-main
            # thread) — ensure the supervisor knows about the interrupt.
            self._supervisor.shutdown()
        finally:
            # Idempotent — no-op if the supervisor already finished.
            self._supervisor.shutdown()
            if stop_watcher is not None:
                stop_watcher.set()
            self._close_sockets(sockets)
            cleanup_unix_socket(self._config)
            logger.info("Pounce server stopped")

    async def _run_lifespan_then_supervise(
        self,
        supervisor: Supervisor,
        sockets: list[socket.socket],
    ) -> None:
        """Run lifespan in the main thread, then hand off to supervisor.

        Installs asyncio signal handlers so SIGINT/SIGTERM trigger a
        coordinated graceful shutdown through the supervisor instead of
        a raw ``KeyboardInterrupt``.  On the first signal the supervisor
        drains workers; a second signal removes the handler so the
        default ``KeyboardInterrupt`` forces an immediate exit.

        """
        loop = asyncio.get_running_loop()

        def _force_exit() -> None:
            """Second signal — exit immediately without waiting for drain."""
            logger.info("Second signal received — forcing immediate exit")
            os._exit(1)

        def _on_signal() -> None:
            supervisor.shutdown()
            # Replace with force-exit handler so second Ctrl+C exits immediately
            for sig in (signal.SIGINT, signal.SIGTERM):
                with contextlib.suppress(NotImplementedError, RuntimeError):
                    loop.remove_signal_handler(sig)
                    loop.add_signal_handler(sig, _force_exit)

        def _on_reload_signal() -> None:
            """Trigger graceful reload on SIGHUP."""
            logger.info("Received SIGHUP — triggering graceful reload")
            # Run reload in executor to avoid blocking event loop
            loop.run_in_executor(None, supervisor.graceful_reload)

        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, _on_signal)

        # Install SIGHUP handler for graceful reload (POSIX only)
        if hasattr(signal, "SIGHUP"):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(signal.SIGHUP, _on_reload_signal)

        async with run_lifespan(self._app, self._config) as lifespan_state:
            # Set lifespan state on supervisor for worker injection
            supervisor.set_lifespan_state(lifespan_state)

            # The supervisor blocks (it runs its own watchdog loop), so
            # we run it in a thread executor to keep the asyncio loop
            # alive for lifespan shutdown.
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
            lines.append(f"  -> max-requests/conn: {self._config.max_requests_per_connection}")
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
                with contextlib.suppress(OSError):
                    sock.close()


def _get_version() -> str:
    """Get the pounce version string."""
    try:
        from pounce import __version__

        return __version__
    except ImportError:
        return "0.0.0"
