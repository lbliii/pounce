"""
Shared structured-concurrency helpers.

Single source of truth for the ``FIRST_COMPLETED`` race that several
request/connection handlers run: the ASGI app task is raced against a
companion task (disconnect monitor, body reader, frame reader, ...) and
whichever finishes first wins. The losing task(s) are always cancelled and
awaited so no orphaned task is leaked back into the event loop.

Callers keep their own *divergent* winner handling — these helpers only own
the part that is byte-identical everywhere (the ``asyncio.wait`` plus the
cancel-and-drain of losing tasks) and hand back the ``(done, pending)`` split
so each site can inspect which task won.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterable


async def cancel_and_drain(tasks: Iterable[asyncio.Task[object]]) -> None:
    """Cancel every task in *tasks* and await it, swallowing cancellation.

    Guarantees each task is cancelled *and* awaited under
    ``contextlib.suppress(asyncio.CancelledError)`` so no losing task is left
    dangling on the event loop. Safe to call with an empty iterable.
    """
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def race_first_completed(
    *tasks: asyncio.Task[object],
) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
    """Race *tasks* until the first one completes, then drain the losers.

    Awaits :func:`asyncio.wait` with ``return_when=FIRST_COMPLETED`` and then
    guarantees that every still-pending (losing) task is cancelled *and*
    awaited via :func:`cancel_and_drain`, so no task is left dangling on the
    loop.

    The returned ``pending`` set therefore contains tasks that are already
    finished (cancelled and drained); callers must not re-await them or call
    ``.result()`` on them. Winner handling should operate on the ``done`` set.

    This is the right helper when *every* loser should be abandoned (the
    WebSocket and HTTP/2 WebSocket bridges). When a losing task must instead
    be allowed to run to completion (e.g. the ASGI app must finish its
    response after the request body has been fully read), use
    :func:`wait_first_completed` together with :func:`cancel_and_drain` so the
    caller controls exactly which tasks are cancelled.

    Args:
        *tasks: The tasks to race. At least one must be supplied.

    Returns:
        The ``(done, pending)`` split exactly as returned by
        :func:`asyncio.wait`. Tasks in ``pending`` have been cancelled and
        awaited before this coroutine returns.

    """
    done, pending = await wait_first_completed(*tasks)
    await cancel_and_drain(pending)
    return done, pending


async def wait_first_completed(
    *tasks: asyncio.Task[object],
) -> tuple[set[asyncio.Task[object]], set[asyncio.Task[object]]]:
    """Await :func:`asyncio.wait` with ``FIRST_COMPLETED`` and return the split.

    A thin wrapper that does *not* touch the pending tasks, for callers whose
    winner handling needs to inspect ``done``/``pending`` and selectively let
    a losing task finish before draining the rest (via
    :func:`cancel_and_drain`).

    Args:
        *tasks: The tasks to race. At least one must be supplied.

    Returns:
        The ``(done, pending)`` split as returned by :func:`asyncio.wait`.

    """
    return await asyncio.wait(set(tasks), return_when=asyncio.FIRST_COMPLETED)
