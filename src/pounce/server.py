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
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pounce.logging as pounce_logging
from pounce._errors import WorkerError
from pounce._runtime import WorkerMode, detect_worker_mode, is_gil_enabled
from pounce._state import (
    BANNER,
    READY,
    RELOAD_FAILED,
    RELOAD_START,
    SHUTDOWN_COMPLETE,
    SHUTDOWN_DRAINED,
    SHUTDOWN_START,
    SHUTDOWN_TIMEOUT,
    dispatch,
)
from pounce._types import ASGIApp
from pounce.asgi.lifespan import run_lifespan
from pounce.config import ServerConfig
from pounce.display import CliDisplayOverrides, resolve_display_config
from pounce.lifecycle import LifecycleCollector
from pounce.logging import configure_logging
from pounce.net.listener import (
    cleanup_unix_socket,
    create_listener,
    create_listeners,
    create_udp_listener,
    create_udp_listeners,
)
from pounce.net.tls import create_tls_context, is_tls_configured
from pounce.supervisor import Supervisor
from pounce.sync_protocol import SyncApp
from pounce.worker import Worker, _worker_lifecycle_receive, _worker_lifecycle_send

logger = logging.getLogger("pounce")


def _is_loopback_bind(host: str) -> bool:
    """Return True if *host* refers to a loopback address.

    Recognises ``localhost``, IPv4 ``127.0.0.0/8``, and IPv6 ``::1``. An
    unparseable host (a name we can't resolve here) is treated as
    non-loopback so the public-bind warning errs on the loud side.
    """
    import ipaddress

    if host.lower() in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


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
        "_bound_addr",
        "_config",
        "_lifecycle_collector",
        "_loop",
        "_shutdown_event",
        "_ssl_context",
        "_started_event",
        "_supervisor",
        "_sync_app",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        *,
        app_path: str | None = None,
        lifecycle_collector: LifecycleCollector | None = None,
        sync_app: SyncApp | None = None,
        cli_display: CliDisplayOverrides | None = None,
    ) -> None:
        resolved_display = resolve_display_config(
            cli_name=cli_display.name if cli_display else None,
            cli_tagline=cli_display.tagline if cli_display else None,
            cli_version=cli_display.version if cli_display else None,
            cli_signage=cli_display.signage if cli_display else None,
            config_display=config.display,
            app=app,
            pyproject_path=os.environ.get("POUNCE_APP_PYPROJECT"),
        )
        self._config = replace(config, display=resolved_display)
        self._app = app
        self._app_path = app_path
        self._lifecycle_collector = lifecycle_collector
        self._sync_app = sync_app
        self._ssl_context = None
        self._shutdown_event = threading.Event()
        self._started_event = threading.Event()
        self._bound_addr: tuple[str, int] | None = None
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
        self._apply_integrations()

        effective_workers = self._config.resolve_workers()
        mode = detect_worker_mode()
        if self._config.worker_mode == "subinterpreter":
            mode = WorkerMode.SUBINTERPRETER
        self._log_worker_mode_notice(mode)

        # Create TLS context if certificate is configured
        if is_tls_configured(self._config):
            self._ssl_context = create_tls_context(self._config)

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

    @property
    def bound_addr(self) -> tuple[str, int] | None:
        """The actual (host, port) the server bound to, or ``None`` if not yet started."""
        return self._bound_addr

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
        self._bound_addr = (actual_addr[0], actual_addr[1])
        udp_sock = self._create_udp_listener_if_h3(actual_addr)

        try:
            asyncio.run(self._run_single_async(sock, udp_sock))
        except KeyboardInterrupt:
            pass
        finally:
            sock.close()
            if udp_sock is not None:
                udp_sock.close()
            cleanup_unix_socket(self._config)
            dispatch(SHUTDOWN_COMPLETE)

    def _reload_watch_dirs(self) -> list[Path]:
        """Directories the file watcher should scan in ``--reload`` mode.

        Always includes the current working directory and any explicit
        ``reload_dirs``. Also includes configured static-mount directories
        (``static_files`` is ``{url_path: directory}``) so that editing
        content/assets served from *outside* cwd still triggers a reload.
        Duplicates and missing directories are tolerated by the watcher.
        """
        dirs: list[Path] = [Path.cwd()]
        dirs.extend(Path(d).resolve() for d in self._config.reload_dirs)
        dirs.extend(Path(directory).resolve() for directory in self._config.static_files.values())
        # De-duplicate while preserving order.
        seen: set[Path] = set()
        unique: list[Path] = []
        for d in dirs:
            if d not in seen:
                seen.add(d)
                unique.append(d)
        return unique

    def _run_single_with_reload(self) -> None:
        """Single-worker mode with auto-reload on source changes.

        Runs the worker in a loop. When the file watcher detects changes
        it sets ``_reload_requested`` and signals shutdown, causing the
        asyncio loop to exit. The outer loop then restarts the worker
        with a fresh socket and event loop.

        """
        from pounce._reload import watch_for_changes

        reload_requested = threading.Event()
        stop_watcher = threading.Event()

        def _on_change() -> None:
            reload_requested.set()
            self.shutdown()

        watch_dirs = self._reload_watch_dirs()
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
                udp_sock = self._create_udp_listener_if_h3(actual_addr)

                dispatch(READY, host=actual_addr[0], port=actual_addr[1], uds=self._config.uds)

                try:
                    asyncio.run(self._run_single_async(sock, udp_sock))
                except KeyboardInterrupt:
                    break
                finally:
                    sock.close()
                    if udp_sock is not None:
                        udp_sock.close()

                if reload_requested.is_set():
                    dispatch(RELOAD_START)
                    if self._app_path:
                        try:
                            from pounce._importer import reimport_app

                            self._app = reimport_app(self._app_path)
                        except Exception:
                            logger.exception("Reload failed — serving previous version")
                            dispatch(RELOAD_FAILED, error="import error")
                    continue
                break
        finally:
            stop_watcher.set()
            cleanup_unix_socket(self._config)
            dispatch(SHUTDOWN_COMPLETE)

    async def _run_single_async(
        self,
        sock: socket.socket,
        udp_sock: socket.socket | None = None,
    ) -> None:
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
            startup_ok = True
            try:
                await asyncio.wait_for(
                    self._app(
                        {"type": "pounce.worker.startup", "worker_id": 0},
                        _worker_lifecycle_receive,
                        _worker_lifecycle_send,
                    ),
                    timeout=self._config.startup_timeout,
                )
            except TimeoutError:
                startup_ok = False
                logger.warning(
                    "Worker startup hook timed out after %.1fs"
                    " — the pounce.worker.startup hook did not complete in time",
                    self._config.startup_timeout,
                )
            except Exception:
                startup_ok = False
                logger.warning(
                    "Worker startup hook raised — if this is unexpected, check your app",
                    exc_info=True,
                )

            # Fail-loud opt-in (issue #65): refuse to serve if the hook failed.
            # The lifespan context still unwinds cleanly on return, so
            # lifespan.shutdown runs and run() exits without ever serving.
            if not startup_ok and self._config.worker_startup_failure == "shutdown":
                logger.error(
                    "Worker startup hook failed and worker_startup_failure='shutdown' "
                    "— not accepting connections"
                )
                self._shutdown_event.set()
                if self._async_shutdown is not None:
                    self._async_shutdown.set()
                self._loop = None
                raise WorkerError(
                    "Worker 0 startup hook failed; server did not become ready",
                    code="POUNCE_WORKER_STARTUP_FAILED",
                    hint=(
                        "Fix the pounce.worker.startup hook, or set "
                        "worker_startup_failure='ignore' to preserve compatibility."
                    ),
                )

            server = await asyncio.start_server(
                worker._handle_connection,
                sock=sock,
                ssl=self._ssl_context,
            )

            h3_task: asyncio.Task[None] | None = None
            if udp_sock is not None:
                h3_task = asyncio.create_task(self._run_single_h3(udp_sock, lifespan_state))

            dispatch(READY, host=self._config.host, port=self._config.port, uds=self._config.uds)
            self._started_event.set()

            try:
                await self._async_shutdown.wait()
            finally:
                if h3_task is not None:
                    h3_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await h3_task
                dispatch(SHUTDOWN_START)
                server.close()  # Stop accepting new connections

                # Grace period: wait for in-flight connections to complete
                timeout = self._config.shutdown_timeout
                try:
                    await asyncio.wait_for(
                        server.wait_closed(),
                        timeout=timeout,
                    )
                    dispatch(SHUTDOWN_DRAINED)
                except TimeoutError:
                    dispatch(SHUTDOWN_TIMEOUT, timeout=timeout)

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
        sockets = create_listeners(self._config, effective_workers, shared=(mode == "thread"))
        actual_addr = sockets[0].getsockname()
        udp_sockets = self._create_udp_listeners_if_h3(actual_addr, effective_workers)

        self._supervisor = Supervisor(
            self._config,
            self._app,
            mode=mode,
            ssl_context=self._ssl_context,
            lifecycle_collector=self._lifecycle_collector,
            app_path=self._app_path,
            sync_app=self._sync_app,
        )

        # Start file watcher for reload mode
        stop_watcher: threading.Event | None = None
        if self._config.reload:
            from pounce._reload import watch_for_changes

            stop_watcher = threading.Event()
            supervisor_ref = self._supervisor

            def _on_change() -> None:
                supervisor_ref.restart_workers()

            watch_dirs = self._reload_watch_dirs()
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
            asyncio.run(
                self._run_lifespan_then_supervise(
                    self._supervisor,
                    sockets,
                    udp_sockets,
                ),
            )
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
            self._close_sockets(udp_sockets)
            cleanup_unix_socket(self._config)
            dispatch(SHUTDOWN_COMPLETE)

    async def _run_lifespan_then_supervise(
        self,
        supervisor: Supervisor,
        sockets: list[socket.socket],
        udp_sockets: list[socket.socket] | None = None,
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
            await loop.run_in_executor(
                None,
                supervisor.run,
                sockets,
                udp_sockets,
            )

    async def _run_single_h3(
        self,
        udp_sock: socket.socket,
        lifespan_state: dict[str, Any],
    ) -> None:
        """Run HTTP/3 datagram endpoint in single-worker mode."""
        try:
            from pounce.protocols.h3 import is_h3_available

            if not is_h3_available():
                logger.warning("zoomies not installed; HTTP/3 disabled")
                return
        except ImportError:
            return

        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import (
            _make_zero_rtt_policy,
            create_zoomies_datagram_protocol_factory,
        )

        loop = asyncio.get_running_loop()
        logger_h3 = logging.getLogger("pounce.h3_worker.0")

        cert_path = self._config.ssl_certfile or ""
        key_path = self._config.ssl_keyfile or ""
        try:
            with open(cert_path, "rb") as f:
                cert_bytes = f.read()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            logger.error("HTTP/3 setup failed: cannot read certificate at %s: %s", cert_path, exc)
            return
        try:
            with open(key_path, "rb") as f:
                key_bytes = f.read()
        except (FileNotFoundError, PermissionError, OSError) as exc:
            logger.error("HTTP/3 setup failed: cannot read private key at %s: %s", key_path, exc)
            return

        zero_rtt_policy = _make_zero_rtt_policy() if self._config.http3_zero_rtt_enabled else None
        quic_config = QuicConfiguration(
            certificate=cert_bytes,
            private_key=key_bytes,
            idle_timeout=self._config.http3_idle_timeout,
            zero_rtt_policy=zero_rtt_policy,
        )

        server_addr = udp_sock.getsockname()
        server = (str(server_addr[0]), int(server_addr[1]))

        protocol_factory = create_zoomies_datagram_protocol_factory(
            self._app,
            self._config,
            logger_h3,
            server,
            quic_config,
            lifespan_state=lifespan_state,
        )

        transport, _protocol = await loop.create_datagram_endpoint(
            protocol_factory,
            sock=udp_sock,
        )

        try:
            if self._async_shutdown is not None:
                await self._async_shutdown.wait()
        finally:
            transport.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _effective_worker_mode(self) -> WorkerMode:
        """Return the worker spawning strategy that ``run()`` will use.

        Mirrors the resolution in :meth:`run`: an explicit
        ``worker_mode="subinterpreter"`` wins, otherwise the GIL state
        decides between thread (3.14t) and process (GIL build) mode. Used to
        reason about whether in-process backpressure state is shared (thread
        mode) or copied per worker (process/subinterpreter mode).
        """
        if self._config.worker_mode == "subinterpreter":
            return WorkerMode.SUBINTERPRETER
        return detect_worker_mode()

    def _log_worker_mode_notice(self, mode: WorkerMode) -> None:
        """Emit a one-line notice when the beta subinterpreter mode is used.

        Subinterpreter workers (PEP 734) are a beta path with limited
        lifecycle proof; operators should see at startup that they opted into
        it rather than the stable thread/process modes.
        """
        if mode is WorkerMode.SUBINTERPRETER:
            logger.info(
                "Worker mode: subinterpreter (beta) — PEP 734 isolated "
                "interpreters; limited lifecycle proof, expect rough edges"
            )

    def _apply_integrations(self) -> None:
        """Configure optional integrations and wrap the app.

        Wrapping order (outermost first):
        1. Sentry (catches exceptions from all inner layers)
        2. Request queue (load shedding)
        3. Rate limiter (per-IP throttling)
        4. User middleware
        5. Static files
        6. Metrics endpoint (intercepts /metrics)
        7. App (innermost)

        """
        self._apply_otel()
        self._apply_lifecycle_logging()
        self._apply_metrics()
        self._apply_static_files()
        self._apply_middleware()
        self._apply_rate_limiter()
        self._apply_request_queue()
        self._apply_sentry()
        self._warn_if_introspection_public()

    def _apply_otel(self) -> None:
        """Configure OpenTelemetry tracing if endpoint is set."""
        if not self._config.otel_endpoint:
            return
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

    def _apply_lifecycle_logging(self) -> None:
        """Configure lifecycle event logging if enabled."""
        if not (self._config.lifecycle_logging and self._lifecycle_collector is None):
            return
        from pounce.lifecycle import LoggingCollector

        self._lifecycle_collector = LoggingCollector(
            slow_request_threshold_ms=self._config.log_slow_requests_threshold * 1000,
            log_format=self._config.log_format,
            health_check_path=self._config.health_check_path,
        )
        logger.debug("Lifecycle event logging enabled")

    def _apply_metrics(self) -> None:
        """Configure Prometheus metrics endpoint if enabled."""
        if not (self._config.metrics_enabled and self._lifecycle_collector is None):
            return
        from pounce._metrics_handler import wrap_app_with_metrics
        from pounce.metrics import PrometheusCollector

        self._lifecycle_collector = PrometheusCollector()
        self._app = cast(
            "ASGIApp",
            wrap_app_with_metrics(
                self._app,
                self._lifecycle_collector,
                self._config.metrics_path,
            ),
        )
        logger.info("Prometheus metrics enabled at %s", self._config.metrics_path)

    def _apply_static_files(self) -> None:
        """Wrap app with configured static file mounts."""
        if not self._config.static_files:
            return
        from pounce._static import StaticFiles, StaticMount

        mounts = [
            StaticMount(
                url_path=url_path,
                directory=Path(directory),
                cache_control=self._config.static_cache_control,
                precompressed=self._config.static_precompressed,
                follow_symlinks=self._config.static_follow_symlinks,
                index_file=self._config.static_index_file,
            )
            for url_path, directory in self._config.static_files.items()
        ]
        self._app = cast("ASGIApp", StaticFiles(self._app, mounts=mounts))
        logger.info("Static file serving enabled for %d mount(s)", len(mounts))

    def _apply_middleware(self) -> None:
        """Wrap app with middleware stack if configured."""
        if not self._config.middleware:
            return
        from pounce._middleware import MiddlewareStack

        self._app = cast("ASGIApp", MiddlewareStack(self._config.middleware, self._app))

    def _apply_rate_limiter(self) -> None:
        """Configure per-IP rate limiting if enabled."""
        if not self._config.rate_limit_enabled:
            return
        from pounce._rate_limiter import RateLimiter, create_rate_limit_wrapper

        rate_limiter = RateLimiter(
            rate=self._config.rate_limit_requests_per_second,
            burst=self._config.rate_limit_burst,
            max_tracked_ips=self._config.rate_limit_max_tracked_ips,
        )
        self._app = cast("ASGIApp", create_rate_limit_wrapper(self._app, rate_limiter))
        logger.info(
            "Rate limiting enabled: %.1f req/s per IP (burst: %d)",
            self._config.rate_limit_requests_per_second,
            self._config.rate_limit_burst,
        )
        # The token bucket is in-process with no IPC. In thread mode (3.14t) a
        # single limiter is genuinely shared, so the configured value is the
        # aggregate. In process/subinterpreter mode each worker inherits an
        # independent copy, so the real per-IP ceiling is rate x workers.
        rate = self._config.rate_limit_requests_per_second
        burst = self._config.rate_limit_burst
        if self._effective_worker_mode() is WorkerMode.THREAD:
            logger.info(
                "Rate limiting: %.1f req/s per IP shared across workers "
                "(thread mode; aggregate = configured)",
                rate,
            )
        else:
            workers = self._config.resolve_workers()
            logger.info(
                "Rate limiting: %.1f req/s per IP per worker x %d workers "
                "= ~%.1f req/s aggregate per IP (burst ~%d)",
                rate,
                workers,
                rate * workers,
                burst * workers,
            )

    def _apply_request_queue(self) -> None:
        """Configure request queueing (load shedding) if enabled.

        Each worker thread gets its own RequestQueue/QueueMetrics on first
        use — asyncio.Lock and asyncio.Semaphore are single-event-loop
        primitives, so a shared instance would break under free-threading
        where multiple workers run independent loops.
        """
        if not self._config.request_queue_enabled:
            return
        from pounce._request_queue import QueueMetrics, RequestQueue, create_queue_wrapper

        max_depth = self._config.request_queue_max_depth
        inner_app = self._app
        tls = threading.local()

        async def per_worker_wrapper(scope: dict, receive: object, send: object) -> None:
            wrapped = getattr(tls, "wrapped", None)
            if wrapped is None:
                queue = RequestQueue(max_depth=max_depth)
                metrics = QueueMetrics()
                wrapped = create_queue_wrapper(inner_app, queue, metrics)
                tls.queue = queue
                tls.metrics = metrics
                tls.wrapped = wrapped
            await wrapped(scope, receive, send)

        self._app = cast("ASGIApp", per_worker_wrapper)
        logger.info(
            "Request queueing enabled: max depth %d",
            max_depth if max_depth > 0 else -1,
        )
        # The queue uses per-event-loop asyncio primitives, so every worker
        # gets its own RequestQueue in ALL worker modes (including thread
        # mode). The aggregate load-shed depth is therefore depth x workers.
        if max_depth > 0:
            workers = self._config.resolve_workers()
            logger.info(
                "Request queueing: max depth %d per worker x %d workers = ~%d aggregate queued",
                max_depth,
                workers,
                max_depth * workers,
            )

    def _apply_sentry(self) -> None:
        """Configure Sentry error tracking if DSN is set."""
        if not self._config.sentry_dsn:
            return
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

    def _warn_if_introspection_public(self) -> None:
        """Warn at startup if the introspection endpoint is publicly reachable.

        The ``/_pounce/info`` endpoint exposes a redacted view of the running
        config. The redaction allowlist is fail-closed, but a misconfigured
        deploy that binds pounce to a public interface still hands strangers
        a free runtime probe. Surface the risk loudly with a code agents can
        catalog-link to.
        """
        if not self._config.introspection_enabled:
            return
        if _is_loopback_bind(self._config.host) and _is_loopback_bind(
            self._config.introspection_bind
        ):
            return
        logger.warning(
            "POUNCE_CONFIG_INTROSPECTION_PUBLIC: introspection endpoint enabled "
            "with non-loopback bind (host=%r, introspection_bind=%r). The "
            "%s response includes a redacted config view; verify INFO_ALLOWLIST "
            "before exposing publicly. See "
            "docs/troubleshooting.md#POUNCE_CONFIG_INTROSPECTION_PUBLIC",
            self._config.host,
            self._config.introspection_bind,
            self._config.introspection_path,
        )

    def _create_udp_listener_if_h3(self, actual_addr: tuple[str, int]) -> socket.socket | None:
        """Create a single UDP listener for HTTP/3 if configured and available."""
        if not (
            self._config.http3_enabled
            and is_tls_configured(self._config)
            and self._config.ssl_certfile
            and self._config.ssl_keyfile
        ):
            return None

        from pounce.protocols.h3 import is_h3_available

        if is_h3_available():
            udp_config = replace(self._config, port=actual_addr[1])
            udp_sock = create_udp_listener(udp_config)
            logger.debug("HTTP/3 UDP listener on %s:%d", actual_addr[0], actual_addr[1])
            return udp_sock

        logger.warning(
            "http3_enabled but HTTP/3 stack unavailable (zoomies not installed) — "
            "install with: pip install bengal-pounce[h3]"
        )
        self._config = replace(self._config, http3_enabled=False)
        return None

    def _create_udp_listeners_if_h3(
        self, actual_addr: tuple[str, int], count: int
    ) -> list[socket.socket]:
        """Create multiple UDP listeners for HTTP/3 if configured and available."""
        if not (
            self._config.http3_enabled
            and is_tls_configured(self._config)
            and self._config.ssl_certfile
            and self._config.ssl_keyfile
        ):
            return []

        from pounce.protocols.h3 import is_h3_available

        if is_h3_available():
            udp_config = replace(self._config, port=actual_addr[1])
            udp_sockets = create_udp_listeners(udp_config, count)
            logger.debug(
                "HTTP/3 UDP listeners on %s:%d (%d workers)",
                actual_addr[0],
                actual_addr[1],
                count,
            )
            return udp_sockets

        logger.warning(
            "http3_enabled but HTTP/3 stack unavailable (zoomies not installed) — "
            "install with: pip install bengal-pounce[h3]; disabling HTTP/3"
        )
        self._config = replace(self._config, http3_enabled=False)
        return []

    def _print_banner(self, effective_workers: int, mode: WorkerMode) -> None:
        """Print the startup banner to stderr.

        In JSON mode, emits a single structured JSON log line.
        In text/pretty mode, prints a human-friendly ASCII banner.
        """
        scheme = "https" if self._config.ssl_certfile else "http"
        url = f"{scheme}://{self._config.host}:{self._config.port}"

        gil_status = "nogil" if not is_gil_enabled() else "GIL"
        mode_label = f"{mode}s" if effective_workers > 1 else "single"

        if pounce_logging.is_json():
            import json as json_module
            from datetime import UTC, datetime

            banner: dict[str, object] = {
                "ts": datetime.now(tz=UTC).isoformat(),
                "level": "info",
                "event": "startup",
                "version": _get_version(),
                "python": sys.version.split()[0],
                "gil": gil_status,
                "url": url,
                "pid": os.getpid(),
                "workers": effective_workers,
                "worker_mode": mode_label,
            }
            if self._ssl_context is not None:
                banner["tls"] = True
            if self._config.http3_enabled:
                banner["http3"] = True
            if self._config.compression:
                banner["compression"] = True
            if self._config.root_path:
                banner["root_path"] = self._config.root_path
            disp = self._config.display
            if disp is not None and disp.name:
                app_payload: dict[str, str] = {"name": disp.name}
                if disp.tagline:
                    app_payload["tagline"] = disp.tagline
                if disp.version:
                    app_payload["version"] = disp.version
                banner["app"] = app_payload
            sys.stderr.write(json_module.dumps(banner, default=str) + "\n")
            return

        dispatch(
            BANNER,
            config=self._config,
            effective_workers=effective_workers,
            mode_label=mode_label,
            gil_status=gil_status,
        )

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
