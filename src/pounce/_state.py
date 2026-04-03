"""Server lifecycle state machine — Elm Architecture via milo.

Centralizes the server lifecycle (init → startup → ready → serving →
reloading → shutting_down → stopped) into an immutable state model with a
pure reducer.  A render middleware produces branded output on each dispatch,
replacing the procedural ``if _is_pretty(): _write(_render(...))`` pattern
with a single dispatch-driven view layer.

Usage from server.py / supervisor.py::

    from pounce._state import dispatch

    dispatch("BANNER", config=config, effective_workers=4, ...)
    dispatch("READY", host="127.0.0.1", port=8000)
    dispatch("SHUTDOWN_COMPLETE")

"""

import logging
import os
import sys
import threading
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Final

from milo._types import Action
from milo.state import Store

from pounce.config import ServerConfig
from pounce.display import DisplayConfig

logger = logging.getLogger("pounce")


# ── Action type constants ────────────────────────────────

BANNER: Final = "BANNER"
READY: Final = "READY"
SHUTDOWN_START: Final = "SHUTDOWN_START"
SHUTDOWN_DRAINED: Final = "SHUTDOWN_DRAINED"
SHUTDOWN_TIMEOUT: Final = "SHUTDOWN_TIMEOUT"
SHUTDOWN_COMPLETE: Final = "SHUTDOWN_COMPLETE"
RELOAD_DETECTED: Final = "RELOAD_DETECTED"
RELOAD_START: Final = "RELOAD_START"
RELOAD_COMPLETE: Final = "RELOAD_COMPLETE"
RELOAD_FAILED: Final = "RELOAD_FAILED"
SUPERVISOR_STARTING: Final = "SUPERVISOR_STARTING"
WORKER_STARTED: Final = "WORKER_STARTED"
WORKER_CRASHED: Final = "WORKER_CRASHED"
WORKER_MAX_RESTARTS: Final = "WORKER_MAX_RESTARTS"
SUPERVISOR_SHUTDOWN: Final = "SUPERVISOR_SHUTDOWN"
SUPERVISOR_ALL_STOPPED: Final = "SUPERVISOR_ALL_STOPPED"


# ── Phase enum ──────────────────────────────────────────


class Phase(StrEnum):
    """Server lifecycle phases."""

    INIT = "init"
    STARTUP = "startup"
    READY = "ready"
    SERVING = "serving"
    RELOADING = "reloading"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"


# ── Model ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerModel:
    """Immutable server lifecycle state."""

    phase: Phase = Phase.INIT
    effective_workers: int = 0
    mode_label: str = ""
    gil_status: str = ""
    supervisor_mode: str = ""
    generation: int = 0
    connections: int = 0


# ── Reducer ──────────────────────────────────────────────


def server_reducer(state: ServerModel | None, action: Action) -> ServerModel:
    """Pure reducer — advances lifecycle state in response to actions."""
    if state is None:
        state = ServerModel()

    match action.type:
        case "@@INIT":
            return state

        case "BANNER":
            p = action.payload
            return replace(
                state,
                phase=Phase.STARTUP,
                effective_workers=p["effective_workers"],
                mode_label=p["mode_label"],
                gil_status=p["gil_status"],
            )

        case "READY":
            return replace(state, phase=Phase.READY)

        case "SUPERVISOR_STARTING":
            p = action.payload
            return replace(
                state,
                phase=Phase.SERVING,
                effective_workers=p["count"],
                supervisor_mode=p["mode"],
            )

        case "WORKER_STARTED" | "WORKER_CRASHED" | "WORKER_MAX_RESTARTS":
            return state

        case "RELOAD_DETECTED" | "RELOAD_START":
            return replace(state, phase=Phase.RELOADING)

        case "RELOAD_COMPLETE":
            p = action.payload or {}
            return replace(
                state,
                phase=Phase.SERVING,
                effective_workers=p.get("workers", state.effective_workers),
                generation=p.get("generation", state.generation),
            )

        case "RELOAD_FAILED":
            return replace(state, phase=Phase.SERVING)

        case "SHUTDOWN_START":
            p = action.payload or {}
            return replace(
                state,
                phase=Phase.SHUTTING_DOWN,
                connections=p.get("connections", 0),
            )

        case "SHUTDOWN_DRAINED":
            return replace(state, connections=0)

        case "SHUTDOWN_TIMEOUT":
            return state

        case "SHUTDOWN_COMPLETE":
            return replace(state, phase=Phase.STOPPED)

        case "SUPERVISOR_SHUTDOWN":
            return replace(state, phase=Phase.SHUTTING_DOWN)

        case "SUPERVISOR_ALL_STOPPED":
            return state

    return state


# ── View (render middleware) ─────────────────────────────


def _render_middleware(dispatch_fn, get_state):
    """Middleware that renders branded lifecycle output on each action."""

    def wrapper(action):
        result = dispatch_fn(action)
        if not action.type.startswith("@@"):
            _render_action(action)
        return result

    return wrapper


def _log_startup_banner_text(
    *,
    config: ServerConfig,
    display: DisplayConfig,
    effective_workers: int,
    mode_label: str,
    gil_status: str,
) -> None:
    """Emit startup identity lines when pretty banners are off or stderr is not a TTY."""
    from pounce import __version__

    scheme = "https" if config.ssl_certfile else "http"
    url = f"{scheme}://{config.host}:{config.port}"
    if display.name:
        title = display.name
        if display.version:
            title = f"{title} v{display.version}"
        suffix = f" — {display.tagline}" if display.tagline else ""
        logger.info("%s%s", title, suffix)
    for line in display.lines:
        logger.info("%s", line)
    logger.info(
        "pounce v%s | %s | %d %s worker(s) | %s | Python %s",
        __version__,
        url,
        effective_workers,
        mode_label,
        gil_status,
        sys.version.split()[0],
    )


def _startup_hints(config, effective_workers: int) -> list[str]:
    """Generate smart startup hints based on config vs environment."""
    hints: list[str] = []
    cpu_count = os.cpu_count() or 1
    if effective_workers == 1 and cpu_count >= 4:
        hints.append(f"{cpu_count} cores detected — try --workers 0 for auto-scaling")
    if not config.compression:
        hints.append("Compression is disabled; remove --no-compression for smaller responses")
    if config.reload and config.workers > 1:
        hints.append("Reload with multiple workers uses full restart, not rolling")
    return hints


def _render_action(action: Action) -> None:
    """Route an action to its branded template or logger fallback."""
    from pounce._output import _is_pretty, _render, _write

    p = action.payload or {}
    pretty = _is_pretty()

    match action.type:
        # ── Startup ──────────────────────────────────────
        case "BANNER":
            config = p["config"]
            effective_workers = p["effective_workers"]
            mode_label = p["mode_label"]
            gil_status = p["gil_status"]

            from pounce import __version__

            display = config.display if config.display is not None else DisplayConfig()

            signage = display.signage or "full"
            if signage == "off" or not pretty:
                _log_startup_banner_text(
                    config=config,
                    display=display,
                    effective_workers=effective_workers,
                    mode_label=mode_label,
                    gil_status=gil_status,
                )
                return

            scheme = "https" if config.ssl_certfile else "http"
            url = f"{scheme}://{config.host}:{config.port}"

            features: list[dict[str, object]] = []
            if config.ssl_certfile:
                features.append({"name": "TLS", "on": True, "detail": ""})
            if config.http3_enabled:
                features.append({"name": "HTTP/3", "on": True, "detail": "QUIC/UDP"})
            features.append(
                {
                    "name": "Compression",
                    "on": config.compression,
                    "detail": "" if config.compression else "disabled",
                }
            )
            features.append(
                {
                    "name": "Access log",
                    "on": config.access_log,
                    "detail": "" if config.access_log else "disabled",
                }
            )
            if config.server_timing:
                features.append({"name": "Server-Timing", "on": True, "detail": ""})
            if config.reload:
                features.append({"name": "Reload", "on": True, "detail": "watching for changes"})

            hints = _startup_hints(config, effective_workers)

            app_version_str = f"v{display.version}" if display.version else ""
            minimal_server_line = (
                f"pounce v{__version__} · {url} · {effective_workers} "
                f"worker ({mode_label}) · {gil_status}"
            )

            _write(
                _render(
                    "serve_banner.kida",
                    version=__version__,
                    gil=gil_status,
                    url=url,
                    uds=config.uds,
                    workers=effective_workers,
                    mode=mode_label,
                    log_level=config.log_level,
                    features=features,
                    hints=hints,
                    health_check_path=config.health_check_path or "",
                    root_path=config.root_path or "",
                    signage=signage,
                    app_name=display.name or "",
                    app_tagline=display.tagline or "",
                    app_version_str=app_version_str,
                    app_lines=list(display.lines),
                    minimal_server_line=minimal_server_line,
                )
            )

        case "READY":
            host = p.get("host", "")
            port = p.get("port", 0)
            uds = p.get("uds")
            if pretty:
                address = uds if uds else f"{host}:{port}"
                _write(_render("ready.kida", address=address))
            else:
                logger.info("Ready to accept connections")

        # ── Shutdown ─────────────────────────────────────
        case "SHUTDOWN_START":
            if pretty:
                _write(
                    _render(
                        "shutdown.kida",
                        phase="start",
                        connections=p.get("connections", 0),
                        timeout=0,
                    )
                )
            else:
                logger.info("Shutting down — draining connections...")

        case "SHUTDOWN_DRAINED":
            if pretty:
                _write(_render("shutdown.kida", phase="drained", connections=0, timeout=0))
            else:
                logger.info("All connections drained")

        case "SHUTDOWN_TIMEOUT":
            timeout = p["timeout"]
            if pretty:
                _write(_render("shutdown.kida", phase="timeout", connections=0, timeout=timeout))
            else:
                logger.warning(
                    "Shutdown timeout (%.1fs) — forcing remaining connections closed",
                    timeout,
                )

        case "SHUTDOWN_COMPLETE":
            if pretty:
                _write(_render("shutdown.kida", phase="complete", connections=0, timeout=0))
            else:
                logger.info("Pounce server stopped")

        # ── Reload ───────────────────────────────────────
        case "RELOAD_DETECTED":
            files = p.get("files", [])
            if pretty:
                display = list(files[:5])
                if len(files) > 5:
                    display.append(f"+{len(files) - 5} more")
                _write(
                    _render(
                        "reload.kida",
                        phase="detected",
                        files=display,
                        generation=0,
                        error="",
                        workers=0,
                    )
                )
            elif len(files) <= 5:
                logger.info(
                    "Detected %d changed file(s): %s",
                    len(files),
                    ", ".join(files),
                )
            else:
                logger.info(
                    "Detected %d changed file(s): %s...",
                    len(files),
                    ", ".join(files[:5]),
                )

        case "RELOAD_START":
            if pretty:
                _write(
                    _render(
                        "reload.kida",
                        phase="start",
                        files=[],
                        generation=0,
                        error="",
                        workers=0,
                    )
                )
            else:
                logger.info("Reloading...")

        case "RELOAD_COMPLETE":
            workers = p.get("workers", 0)
            generation = p.get("generation")
            if pretty:
                _write(
                    _render(
                        "reload.kida",
                        phase="complete",
                        files=[],
                        generation=generation or 0,
                        error="",
                        workers=workers,
                    )
                )
            elif generation is not None:
                logger.info(
                    "Graceful reload complete. Running %d worker(s) on generation %d",
                    workers,
                    generation,
                )
            else:
                logger.info("All %d worker(s) restarted", workers)

        case "RELOAD_FAILED":
            error_msg = p.get("error", "")
            if pretty:
                _write(
                    _render(
                        "reload.kida",
                        phase="failed",
                        files=[],
                        generation=0,
                        error=error_msg,
                        workers=0,
                    )
                )
            else:
                logger.info("Reload failed — serving previous version")

        # ── Worker events ────────────────────────────────
        case "SUPERVISOR_STARTING":
            count = p["count"]
            mode = p["mode"]
            if pretty:
                _write(
                    _render(
                        "worker_event.kida",
                        event="supervisor_start",
                        id=0,
                        mode=mode,
                        count=count,
                        restarts=0,
                        generation=0,
                    )
                )
            else:
                logger.info("Supervisor starting %d %s worker(s)", count, mode)

        case "WORKER_STARTED":
            worker_id = p["worker_id"]
            mode = p["mode"]
            generation = p.get("generation", 0)
            if pretty:
                _write(
                    _render(
                        "worker_event.kida",
                        event="started",
                        id=worker_id,
                        mode=mode,
                        count=0,
                        restarts=0,
                        generation=generation or 0,
                    )
                )
            else:
                logger.debug("Started worker %d (%s)", worker_id, mode)

        case "WORKER_CRASHED":
            worker_id = p["worker_id"]
            restart_count = p["restart_count"]
            if pretty:
                _write(
                    _render(
                        "worker_event.kida",
                        event="crashed",
                        id=worker_id,
                        mode="",
                        count=0,
                        restarts=restart_count,
                        generation=0,
                    )
                )
            else:
                logger.warning(
                    "Worker %d crashed, restarting (restart #%d)",
                    worker_id,
                    restart_count,
                )

        case "WORKER_MAX_RESTARTS":
            worker_id = p["worker_id"]
            max_restarts = p["max_restarts"]
            if pretty:
                _write(
                    _render(
                        "worker_event.kida",
                        event="max_restarts",
                        id=worker_id,
                        mode="",
                        count=0,
                        restarts=max_restarts,
                        generation=0,
                    )
                )
            else:
                logger.error(
                    "Worker %d exceeded max restarts (%d) — not restarting",
                    worker_id,
                    max_restarts,
                )

        case "SUPERVISOR_SHUTDOWN":
            count = p["count"]
            if pretty:
                _write(
                    _render(
                        "worker_event.kida",
                        event="supervisor_shutdown",
                        id=0,
                        mode="",
                        count=count,
                        restarts=0,
                        generation=0,
                    )
                )
            else:
                logger.info("Shutting down %d worker(s)...", count)

        case "SUPERVISOR_ALL_STOPPED":
            if pretty:
                _write(
                    _render(
                        "worker_event.kida",
                        event="all_stopped",
                        id=0,
                        mode="",
                        count=0,
                        restarts=0,
                        generation=0,
                    )
                )
            else:
                logger.info("All workers stopped")


# ── Store singleton ──────────────────────────────────────

_store: Store | None = None
_store_lock = threading.Lock()


def _get_store() -> Store:
    """Get or create the lifecycle store (thread-safe, lazy)."""
    global _store
    if _store is not None:
        return _store
    with _store_lock:
        if _store is not None:
            return _store
        _store = Store(
            server_reducer,
            initial_state=None,
            middleware=(_render_middleware,),
        )
        return _store


def dispatch(action_type: str, **payload: Any) -> None:
    """Dispatch a lifecycle action to the server store.

    Example::

        dispatch("READY", host="127.0.0.1", port=8000)
        dispatch("SHUTDOWN_COMPLETE")

    """
    _get_store().dispatch(Action(action_type, payload=payload if payload else None))


def get_state() -> ServerModel:
    """Return the current server lifecycle state."""
    return _get_store().state


def _reset_store() -> None:
    """Reset the store to initial state (testing only)."""
    global _store
    with _store_lock:
        _store = None
