"""Tests for the shared FIRST_COMPLETED race helpers (``pounce._concurrency``).

Covers:
- ``race_first_completed``: the loser task is cancelled *and* awaited (no
  leak) when the companion task wins mid-stream, the correct ``(done,
  pending)`` split when the app wins, and exception propagation via
  ``done`` results.
- ``cancel_and_drain`` / ``wait_first_completed``: the composable primitives
  used by the body-reader site so the app task can finish after the reader
  wins instead of being cancelled.
"""

import asyncio

import pytest

from pounce._concurrency import (
    cancel_and_drain,
    race_first_completed,
    wait_first_completed,
)


class TestRaceFirstCompleted:
    """race_first_completed drains losers and returns the (done, pending) split."""

    @pytest.mark.asyncio
    async def test_loser_is_cancelled_and_awaited_when_companion_wins(self):
        """When the companion wins mid-stream the losing task is cancelled AND awaited.

        The loser must be both ``cancelled()`` and ``done()`` (awaited to
        completion) by the time the helper returns, so no task is leaked.
        """
        cancelled_observed = asyncio.Event()

        async def app() -> str:
            try:
                await asyncio.sleep(10)  # never completes on its own
            except asyncio.CancelledError:
                cancelled_observed.set()
                raise
            return "app"  # pragma: no cover - cancelled before reaching here

        async def companion() -> str:
            return "companion"

        app_task = asyncio.create_task(app())
        companion_task = asyncio.create_task(companion())

        done, pending = await race_first_completed(app_task, companion_task)

        assert companion_task in done
        assert app_task in pending
        # Loser was cancelled AND awaited (no leak): cancelled + done.
        assert app_task.cancelled()
        assert app_task.done()
        # The app coroutine actually observed the cancellation.
        assert cancelled_observed.is_set()

    @pytest.mark.asyncio
    async def test_app_wins_returns_correct_split(self):
        """When the app wins it lands in done and the companion is drained."""
        app_done = asyncio.Event()

        async def app() -> str:
            app_done.set()
            return "app"

        async def companion() -> str:
            await asyncio.sleep(10)
            return "companion"  # pragma: no cover

        app_task = asyncio.create_task(app())
        companion_task = asyncio.create_task(companion())

        done, pending = await race_first_completed(app_task, companion_task)

        assert app_task in done
        assert companion_task in pending
        assert app_task.result() == "app"
        # Companion (loser) was cancelled and drained.
        assert companion_task.cancelled()
        assert companion_task.done()

    @pytest.mark.asyncio
    async def test_winner_exception_propagates_via_result(self):
        """An exception raised by the winning task is preserved on its result."""

        class BoomError(RuntimeError):
            pass

        async def app() -> None:
            raise BoomError("app exploded")

        async def companion() -> None:
            await asyncio.sleep(10)  # pragma: no cover

        app_task = asyncio.create_task(app())
        companion_task = asyncio.create_task(companion())

        done, _pending = await race_first_completed(app_task, companion_task)

        assert app_task in done
        with pytest.raises(BoomError, match="app exploded"):
            app_task.result()

    @pytest.mark.asyncio
    async def test_no_pending_when_winner_already_done(self):
        """A single immediately-completing task leaves an empty pending set."""

        async def quick() -> int:
            return 42

        task = asyncio.create_task(quick())
        done, pending = await race_first_completed(task)

        assert task in done
        assert pending == set()
        assert task.result() == 42


class TestCancelAndDrain:
    """cancel_and_drain cancels and awaits each task, swallowing cancellation."""

    @pytest.mark.asyncio
    async def test_cancels_and_awaits_each(self):
        observed = []

        async def sleeper(name: str) -> None:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                observed.append(name)
                raise

        tasks = [asyncio.create_task(sleeper("a")), asyncio.create_task(sleeper("b"))]
        # Let the tasks start.
        await asyncio.sleep(0)

        await cancel_and_drain(tasks)

        assert sorted(observed) == ["a", "b"]
        for task in tasks:
            assert task.cancelled()
            assert task.done()

    @pytest.mark.asyncio
    async def test_empty_iterable_is_a_noop(self):
        await cancel_and_drain([])  # should not raise


class TestWaitFirstCompleted:
    """wait_first_completed returns the split WITHOUT touching pending tasks."""

    @pytest.mark.asyncio
    async def test_does_not_cancel_loser(self):
        """The losing task is left running so the caller can let it finish.

        This is the body-reader pattern: the reader (companion) wins, then
        the app task must be allowed to run to completion rather than being
        cancelled.
        """
        reader_done = asyncio.Event()

        async def reader() -> str:
            reader_done.set()
            return "reader"

        async def app() -> str:
            await reader_done.wait()
            # Yield once so the reader wins the FIRST_COMPLETED race.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return "app-finished"

        app_task = asyncio.create_task(app())
        reader_task = asyncio.create_task(reader())

        done, pending = await wait_first_completed(app_task, reader_task)

        assert reader_task in done
        assert app_task in pending
        # The loser is NOT cancelled: caller can still await it to completion.
        assert not app_task.cancelled()
        assert await app_task == "app-finished"
