"""
Subinterpreter worker bootstrap.

This module provides :func:`bootstrap`, the entry point executed inside
each subinterpreter worker.  The supervisor injects IIC-safe values via
``interp.prepare_main()`` and then runs::

    from pounce._subinterpreter_bootstrap import bootstrap
    bootstrap(ctrl_queue, status_queue, config_json,
              app_import_path, sock_fd, worker_id, parent_sys_path)

Design
------
Rather than duplicating Worker internals, the bootstrap:

1. Reconstructs ``ServerConfig`` from JSON
2. Imports the ASGI app by module path
3. Reconstructs the socket from a dup'd file descriptor
4. Creates a standard ``Worker`` with ``shutdown_event=None``
5. Monkey-patches ``Worker._serve`` to inject an IIC shutdown bridge
   that polls ``ctrl_queue`` and sets ``_async_shutdown`` when commanded

This keeps Worker as the single source of truth for request handling.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from typing import Any

logger = logging.getLogger("pounce.subinterpreter")

# ---------------------------------------------------------------------------
# IIC protocol constants
# ---------------------------------------------------------------------------
CMD_SHUTDOWN = "shutdown"
CMD_DRAIN = "drain"

STATUS_STARTED = "started"
STATUS_SERVING = "serving"
STATUS_DRAINING = "draining"
STATUS_IDLE = "idle"
STATUS_STOPPED = "stopped"
STATUS_ERROR = "error"


def bootstrap(
    ctrl_queue: Any,
    status_queue: Any,
    config_json: str,
    lifespan_state_json: str,
    app_import_path: str,
    sock_fd: int,
    sock_family: int,
    worker_id: int,
    parent_sys_path: tuple[str, ...],
) -> None:
    """Bootstrap a Worker inside a subinterpreter.

    All arguments are IIC-safe types injected by the supervisor via
    ``interp.prepare_main()``. ``sock_family`` is the parent socket's
    address family (AF_INET / AF_INET6 / AF_UNIX, as an int) so the
    reconstructed socket matches the bound listener.
    """
    import json

    # --- Inherit sys.path from parent ---
    sys.path[:] = list(parent_sys_path)

    status_queue.put((STATUS_STARTED,))

    try:
        # --- Reconstruct config ---
        from pounce.config import ServerConfig

        config = ServerConfig.from_json(config_json)

        # --- Reconstruct lifespan state (IIC-safe subset) ---
        lifespan_state: dict[str, Any] = json.loads(lifespan_state_json)

        # --- Import ASGI app ---
        app = _import_app(app_import_path)

        # --- Reconstruct socket from dup'd FD ---
        server_sock = socket.socket(
            socket.AddressFamily(sock_family),
            socket.SOCK_STREAM,
            fileno=sock_fd,
        )

        # Own the reconstructed FD for the whole worker lifetime. If anything
        # below raises BEFORE the worker's normal ``server.close()`` runs
        # (asyncio.start_server, the ``worker_startup_failure='shutdown'``
        # startup hook, or asyncio.run itself can raise), the FD would otherwise
        # leak — abnormal crashes/reloads then accumulate dup'd listener FDs
        # (issue #106). The finally closes it; ``suppress(OSError)`` covers the
        # normal drain path, where ``server.close()`` already closed this same
        # FD (#106).
        try:
            # --- Create Worker (no shutdown_event — IIC bridge replaces it) ---
            from pounce.worker import Worker

            if config.max_connections > 0:
                base, remainder = divmod(config.max_connections, config.resolve_workers())
                per_worker_max = base + (1 if worker_id < remainder else 0)
            else:
                per_worker_max = 0

            worker = Worker(
                config,
                app,
                server_sock,
                worker_id=worker_id,
                shutdown_event=None,
                max_connections=per_worker_max,
            )

            # --- Set lifespan state (IIC-safe keys from main interpreter) ---
            if lifespan_state:
                worker.set_lifespan_state(lifespan_state)

            # --- Run with IIC bridge ---
            asyncio.run(_run_worker_with_iic(worker, ctrl_queue, status_queue))
        finally:
            with contextlib.suppress(OSError):
                server_sock.close()

    except Exception as exc:
        status_queue.put((STATUS_ERROR, str(exc)))
        raise

    status_queue.put((STATUS_STOPPED,))


async def _run_worker_with_iic(
    worker: Any,
    ctrl_queue: Any,
    status_queue: Any,
) -> None:
    """Run Worker._serve() with an IIC-based shutdown bridge.

    This replicates the setup that Worker._serve() does, then injects
    an IIC polling task alongside the normal accept loop.
    """
    loop = asyncio.get_running_loop()
    worker._loop = loop
    worker._async_shutdown = asyncio.Event()

    # Per-worker executor (mirrors Worker._serve)
    pool_size = worker._config.executor_threads_per_worker
    if pool_size == 0:
        pool_size = min(32, (os.cpu_count() or 1) + 4)
    executor = ThreadPoolExecutor(
        max_workers=pool_size,
        thread_name_prefix=f"pounce-subinterp-{worker._worker_id}",
    )
    loop.set_default_executor(executor)

    # Per-worker startup hook
    try:
        await asyncio.wait_for(
            worker._app(
                {"type": "pounce.worker.startup", "worker_id": worker._worker_id},
                _noop_receive,
                _noop_send,
            ),
            timeout=worker._config.startup_timeout,
        )
    except Exception:
        if worker._config.worker_startup_failure == "shutdown":
            # Fail-loud opt-in (issue #65): treat as a fatal startup failure.
            # Re-raise so the worker refuses to serve; bootstrap reports
            # STATUS_ERROR and the supervisor applies its restart budget.
            logger.error(
                "Subinterpreter worker %d startup hook failed and "
                "worker_startup_failure='shutdown' — refusing to serve",
                worker._worker_id,
                exc_info=True,
            )
            raise
        logger.debug("Worker startup hook raised (expected for most apps)")

    # Start accepting connections
    server = await asyncio.start_server(
        worker._handle_connection,
        sock=worker._sock,
        ssl=worker._ssl_context,
    )

    status_queue.put((STATUS_SERVING,))

    # IIC bridge task — polls ctrl_queue and signals worker shutdown
    bridge_task = asyncio.create_task(_iic_bridge(worker, ctrl_queue, status_queue))

    try:
        await worker._async_shutdown.wait()
    finally:
        bridge_task.cancel()

        # Drain active connections
        with worker._conn_lock:
            active = worker._active_connections
        if active > 0:
            logger.info(
                "Subinterpreter worker %d draining %d connection(s)...",
                worker._worker_id,
                active,
            )

        try:
            server.close()
            # Bound the wait so a lingering keep-alive/streaming connection
            # cannot pin teardown forever; stragglers are aborted below.
            try:
                await asyncio.wait_for(
                    server.wait_closed(), timeout=worker._config.shutdown_timeout
                )
            except TimeoutError:
                server.abort_clients()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(server.wait_closed(), timeout=2.0)
        except (ValueError, OSError):  # fmt: skip
            pass  # FD already closed

        # Per-worker shutdown hook
        try:
            await asyncio.wait_for(
                worker._app(
                    {"type": "pounce.worker.shutdown", "worker_id": worker._worker_id},
                    _noop_receive,
                    _noop_send,
                ),
                timeout=worker._config.shutdown_timeout,
            )
        except Exception:
            logger.debug("Worker shutdown hook raised (expected for most apps)")

        # Fully retire the per-worker executor BEFORE this coroutine (and the
        # subinterpreter) unwinds (#104). ``interp.close()`` runs once this
        # ``asyncio.run`` returns; on a free-threaded build, closing an
        # interpreter that still owns running executor threads can crash the
        # process (intermittent SIGSEGV during drain). Wait — bounded by
        # shutdown_timeout — for the pool's threads to exit, cancelling any
        # queued work, so the interpreter has no live worker threads at close.
        # ``run_in_executor`` on a dedicated single-thread pool avoids using the
        # default executor we are tearing down.
        def _shutdown_sync() -> None:
            executor.shutdown(wait=True, cancel_futures=True)

        shutdown_helper = ThreadPoolExecutor(max_workers=1)
        try:
            await asyncio.wait_for(
                loop.run_in_executor(shutdown_helper, _shutdown_sync),
                timeout=worker._config.shutdown_timeout,
            )
        except TimeoutError:
            logger.warning(
                "Subinterpreter worker %d: executor pool did not drain within "
                "%.1fs — abandoning wait (a stuck handler may keep threads alive)",
                worker._worker_id,
                worker._config.shutdown_timeout,
            )
            executor.shutdown(wait=False, cancel_futures=True)
        finally:
            shutdown_helper.shutdown(wait=False)


async def _iic_bridge(
    worker: Any,
    ctrl_queue: Any,
    status_queue: Any,
) -> None:
    """Poll the IIC ctrl_queue and translate commands to Worker state changes.

    A single bounded poll loop (issue #103). The supervisor queues
    ``('drain',)`` then ``('shutdown',)`` back-to-back on SIGTERM, so the
    bridge must keep reading the queue *while* draining — it can never block
    inside a ``while not worker.is_idle()`` spin or the queued ``shutdown``
    becomes unreachable and the subinterpreter thread wedges forever.

    Behaviour per tick (every ``poll_interval``):

    - ``CMD_SHUTDOWN``  -> set ``_async_shutdown`` and return immediately.
    - ``CMD_DRAIN``     -> mark the worker draining and arm a deadline of
      ``config.shutdown_timeout`` from now; keep polling.
    - while draining and idle -> emit ``STATUS_IDLE`` once so the supervisor's
      reload poll can observe it, then keep polling for the explicit shutdown.
    - while draining and past the deadline -> emit a final ``STATUS_IDLE`` and
      set ``_async_shutdown`` so the worker proceeds to its finally-block drain
      rather than spinning forever on a long-lived connection.
    """
    poll_interval = 0.05
    draining = False
    drain_deadline: float | None = None
    idle_announced = False

    while True:
        await asyncio.sleep(poll_interval)
        msg = _try_get(ctrl_queue)
        if msg is not None:
            cmd = msg[0]
            if cmd == CMD_SHUTDOWN:
                status_queue.put((STATUS_DRAINING,))
                worker._async_shutdown.set()
                return
            if cmd == CMD_DRAIN and not draining:
                status_queue.put((STATUS_DRAINING,))
                worker._draining = True
                draining = True
                drain_deadline = monotonic() + worker._config.shutdown_timeout

        if not draining:
            continue

        # Bound the drain wait: once the deadline elapses, signal shutdown so
        # the worker's finally block can run even if a connection outlives us.
        if drain_deadline is not None and monotonic() >= drain_deadline:
            if not idle_announced:
                status_queue.put((STATUS_IDLE,))
            worker._async_shutdown.set()
            return

        # Announce idle exactly once so the supervisor's reload poll unblocks,
        # but keep running so a queued ('shutdown',) is still observed.
        if not idle_announced and worker.is_idle():
            status_queue.put((STATUS_IDLE,))
            idle_announced = True


def _import_app(app_path: str) -> Any:
    """Import an ASGI app by dotted path (e.g. ``'myapp.main:app'``).

    Supports factory syntax: ``'myapp.main:create_app()'``.
    """
    module_path, _, attr = app_path.rpartition(":")
    if not module_path or not attr:
        msg = f"Invalid app path {app_path!r} — expected 'module:attribute'"
        raise ValueError(msg)

    mod = importlib.import_module(module_path)

    if attr.endswith("()"):
        factory_name = attr[:-2]
        factory = getattr(mod, factory_name)
        try:
            return factory()
        except Exception as exc:
            msg = f"App factory {module_path}:{factory_name}() raised: {exc}"
            raise RuntimeError(msg) from exc

    return getattr(mod, attr)


def _try_get(queue: Any) -> tuple[Any, ...] | None:
    """Non-blocking get from an IIC queue. Returns None if empty."""
    try:
        return queue.get_nowait()
    except Exception:  # QueueEmpty from _interpqueues
        return None


async def _noop_receive() -> dict[str, str]:
    """Noop receive for lifecycle hooks."""
    return {"type": "http.disconnect"}


async def _noop_send(message: dict[str, Any]) -> None:
    """Noop send for lifecycle hooks."""
