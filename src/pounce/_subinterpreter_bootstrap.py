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
import importlib
import logging
import os
import socket
import sys
from concurrent.futures import ThreadPoolExecutor
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
    worker_id: int,
    parent_sys_path: tuple[str, ...],
) -> None:
    """Bootstrap a Worker inside a subinterpreter.

    All arguments are IIC-safe types injected by the supervisor via
    ``interp.prepare_main()``.
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
            socket.AF_INET,
            socket.SOCK_STREAM,
            fileno=sock_fd,
        )

        # --- Create Worker (no shutdown_event — IIC bridge replaces it) ---
        from pounce.worker import Worker

        per_worker_max = (
            config.max_connections // config.resolve_workers() if config.max_connections > 0 else 0
        )

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
            timeout=30.0,
        )
    except Exception:
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
            await server.wait_closed()
        except ValueError, OSError:
            pass  # FD already closed

        # Per-worker shutdown hook
        try:
            await asyncio.wait_for(
                worker._app(
                    {"type": "pounce.worker.shutdown", "worker_id": worker._worker_id},
                    _noop_receive,
                    _noop_send,
                ),
                timeout=10.0,
            )
        except Exception:
            logger.debug("Worker shutdown hook raised (expected for most apps)")

        executor.shutdown(wait=False)


async def _iic_bridge(
    worker: Any,
    ctrl_queue: Any,
    status_queue: Any,
) -> None:
    """Poll the IIC ctrl_queue and translate commands to Worker state changes."""
    while True:
        await asyncio.sleep(0.05)
        msg = _try_get(ctrl_queue)
        if msg is None:
            continue

        cmd = msg[0]
        if cmd == CMD_SHUTDOWN:
            status_queue.put((STATUS_DRAINING,))
            worker._async_shutdown.set()
            return
        elif cmd == CMD_DRAIN:
            status_queue.put((STATUS_DRAINING,))
            worker._draining = True
            # Wait until all connections finish
            while not worker.is_idle():
                await asyncio.sleep(0.05)
            status_queue.put((STATUS_IDLE,))
            # Don't shutdown yet — keep interpreter alive so supervisor
            # can read the ("idle",) status.  Wait for explicit ("shutdown",).


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
