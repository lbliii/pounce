"""
Runtime detection utilities.

Detects GIL state and determines the appropriate worker mode for the
current Python interpreter. Used by the supervisor to decide between
thread-based (nogil) and process-based (GIL) worker spawning.

"""

import os
import sys
from typing import Literal

WorkerMode = Literal["thread", "process"]
WorkerExecutionMode = Literal["sync", "async"]


def is_gil_enabled() -> bool:
    """Check whether the GIL is active in the current interpreter.

    Returns ``False`` on Python 3.14t (free-threading) and ``True`` on
    standard GIL-enabled builds. Falls back to ``True`` on Python < 3.13
    where ``sys._is_gil_enabled`` does not exist.

    """
    return getattr(sys, "_is_gil_enabled", lambda: True)()


def detect_worker_mode() -> WorkerMode:
    """Choose the worker spawning strategy based on GIL state.

    Returns:
        ``"thread"`` on free-threaded builds (nogil) — workers share one
        interpreter.  ``"process"`` on GIL builds — workers are forked.

    """
    return "process" if is_gil_enabled() else "thread"


def default_worker_count() -> int:
    """Return a sensible default worker count based on available CPUs.

    Returns ``os.cpu_count()`` or ``1`` when the CPU count cannot be
    determined.

    """
    return os.cpu_count() or 1


def resolve_worker_execution_mode(worker_mode: str) -> WorkerExecutionMode:
    """Resolve the effective worker execution mode.

    Args:
        worker_mode: Config value ("auto", "sync", "async").

    Returns:
        "sync" on free-threaded builds when worker_mode is "auto" or "sync".
        "async" otherwise (GIL builds or explicit "async").

    """
    worker_mode = worker_mode.lower()
    if worker_mode == "sync":
        return "sync"
    if worker_mode == "async":
        return "async"
    if worker_mode == "auto":
        return "sync" if not is_gil_enabled() else "async"
    return "async"  # Unknown value — fall back to safe default
