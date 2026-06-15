"""Deterministic unit tests for the supervisor's dup'd-FD reclaim (issue #106).

The integration tests in ``tests/integration/test_subinterpreter_fd_leak.py``
prove the end-to-end "no net FD growth" invariant with real subinterpreters.
These unit tests pin the supervisor-side reclaim logic directly — closing a
recorded FD, clearing it so it cannot be double-closed, and suppressing the
``OSError`` from the race where the worker already self-closed the same FD —
without needing a live, crashing subinterpreter.
"""

import os
import threading
from typing import cast

from pounce.supervisor import Supervisor, _WorkerHandle


def _make_handle() -> _WorkerHandle:
    # ``target`` is unused by the reclaim path; a never-started Thread satisfies
    # the type without spawning anything.
    placeholder = threading.Thread(target=lambda: None)
    return _WorkerHandle(worker_id=0, target=placeholder, worker=None)


def _reclaim(handle: _WorkerHandle) -> None:
    # The helper reads/writes only ``handle.sock_fd`` and module-level os —
    # never ``self`` — so it can be invoked as an unbound method with a stand-in
    # ``self``.
    Supervisor._reclaim_subinterpreter_fd(cast(Supervisor, None), handle)


def _is_open(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


def test_reclaim_closes_recorded_fd_and_clears_slot() -> None:
    r, w = os.pipe()
    try:
        handle = _make_handle()
        handle.sock_fd = w
        assert _is_open(w)

        _reclaim(handle)

        assert not _is_open(w), "recorded FD should be closed"
        assert handle.sock_fd is None, "recorded FD should be cleared"
    finally:
        os.close(r)
        if _is_open(w):
            os.close(w)


def test_reclaim_is_noop_when_no_fd_recorded() -> None:
    handle = _make_handle()
    assert handle.sock_fd is None
    # Must not raise and must leave the slot cleared.
    _reclaim(handle)
    assert handle.sock_fd is None


def test_reclaim_is_idempotent_and_suppresses_double_close() -> None:
    """A second reclaim (or one racing the worker's own self-close) must not
    raise and must not close an unrelated FD the OS may have reassigned."""
    r, w = os.pipe()
    try:
        handle = _make_handle()
        handle.sock_fd = w

        _reclaim(handle)
        assert handle.sock_fd is None

        # Second call: slot already cleared — pure no-op, no double-close.
        _reclaim(handle)
        assert handle.sock_fd is None

        # Simulate the race directly: record an already-closed FD and confirm
        # the suppressed OSError does not propagate.
        handle.sock_fd = w  # already closed above
        _reclaim(handle)
        assert handle.sock_fd is None
    finally:
        os.close(r)
        if _is_open(w):
            os.close(w)
