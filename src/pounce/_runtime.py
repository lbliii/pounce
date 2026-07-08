"""
Runtime detection utilities.

Detects GIL state and determines the appropriate worker mode for the
current Python interpreter. Used by the supervisor to decide between
thread-based (nogil) and process-based (GIL) worker spawning.

"""

import os
import sys
import sysconfig
from enum import StrEnum

from pounce._errors import SupervisorError


class WorkerMode(StrEnum):
    """Worker spawning strategy."""

    THREAD = "thread"
    PROCESS = "process"
    SUBINTERPRETER = "subinterpreter"


class WorkerExecutionMode(StrEnum):
    """Worker execution model."""

    SYNC = "sync"
    ASYNC = "async"


def is_gil_enabled() -> bool:
    """Check whether the GIL is active in the current interpreter.

    Returns the runtime state, which can be ``True`` on either a standard
    build or a free-threaded build started with its GIL enabled. Falls back to
    ``True`` on Python < 3.13 where ``sys._is_gil_enabled`` does not exist.

    """
    return bool(getattr(sys, "_is_gil_enabled", lambda: True)())


def is_free_threaded_build() -> bool:
    """Return whether this interpreter was compiled with free-threading support.

    This is distinct from :func:`is_gil_enabled`: a free-threaded build can
    still start with its GIL enabled, while a standard build cannot disable it.
    """
    return bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


def has_subinterpreters() -> bool:
    """Check whether ``concurrent.interpreters`` is available (Python 3.14+)."""
    try:
        import concurrent.interpreters  # noqa: F401

        return True
    except ImportError:
        return False


def detect_worker_mode() -> WorkerMode:
    """Choose the worker spawning strategy based on GIL state.

    Returns:
        ``"thread"`` on free-threaded builds (nogil) — workers share one
        interpreter.  ``"process"`` on GIL builds — workers are forked.

    """
    return WorkerMode.PROCESS if is_gil_enabled() else WorkerMode.THREAD


def default_worker_count() -> int:
    """Return a sensible default worker count based on available CPUs.

    Returns ``os.cpu_count()`` or ``1`` when the CPU count cannot be
    determined.

    """
    return os.cpu_count() or 1


def resolve_worker_execution_mode(worker_mode: str) -> WorkerExecutionMode:
    """Resolve the effective worker execution mode.

    Args:
        worker_mode: Config value ("auto", "sync", "async", "subinterpreter").

    Returns:
        "sync" on free-threaded builds when worker_mode is "auto" or "sync".
        "async" for subinterpreter mode (sync workers can't share AsyncPool
        across interpreter boundaries).
        "async" otherwise (GIL builds or explicit "async").

    """
    match worker_mode.lower():
        case "subinterpreter":
            return WorkerExecutionMode.ASYNC
        case "sync":
            return WorkerExecutionMode.SYNC
        case "async":
            return WorkerExecutionMode.ASYNC
        case "auto":
            return WorkerExecutionMode.SYNC if not is_gil_enabled() else WorkerExecutionMode.ASYNC
        case _:
            return WorkerExecutionMode.ASYNC  # Unknown value — fall back to safe default


def resolve_worker_model(worker_mode: str, worker_count: int) -> str:
    """Return the actual spawning and execution model for this runtime.

    A single worker uses the direct async server path. Multi-worker ``auto``
    uses sync thread workers on a free-threaded build and async process workers
    on a GIL build. Explicit subinterpreter mode always uses isolated async
    workers, including when the configured worker count is one.
    """
    normalized = worker_mode.lower()
    if normalized == "subinterpreter":
        return "subinterpreter (async)"
    if worker_count == 1:
        return "single (async)"

    strategy = detect_worker_mode()
    execution = resolve_worker_execution_mode(normalized)
    if strategy is WorkerMode.PROCESS and execution is WorkerExecutionMode.SYNC:
        execution = WorkerExecutionMode.ASYNC
    return f"{strategy.value} ({execution.value})"


def validate_subinterpreter_app_path(worker_mode: str, app_path: str | None) -> None:
    """Fail at the embedding boundary when isolated workers cannot import the app."""
    if worker_mode.lower() == "subinterpreter" and not app_path:
        raise SupervisorError(
            "Subinterpreter workers require an app import path "
            "(e.g., 'myapp:app'). Pass app_path to Server or use the CLI.",
            code="POUNCE_SUPERVISOR_SUBINTERPRETER_NO_APP_PATH",
            hint="Pass --app myapp:app at the CLI or app_path= to Server().",
        )
