"""Tests for HTTP Priority Signals (RFC 9218)."""

import asyncio

import pytest

from pounce._priority import PriorityScheduler, StreamPriority, parse_priority


class TestParsePriority:
    def test_default(self) -> None:
        p = parse_priority("")
        assert p.urgency == 3
        assert p.incremental is False

    def test_urgency_only(self) -> None:
        p = parse_priority("u=0")
        assert p.urgency == 0
        assert p.incremental is False

    def test_urgency_and_incremental(self) -> None:
        p = parse_priority("u=1, i")
        assert p.urgency == 1
        assert p.incremental is True

    def test_incremental_only(self) -> None:
        p = parse_priority("i")
        assert p.urgency == 3  # default
        assert p.incremental is True

    def test_highest_urgency(self) -> None:
        p = parse_priority("u=0")
        assert p.urgency == 0

    def test_lowest_urgency(self) -> None:
        p = parse_priority("u=7")
        assert p.urgency == 7

    def test_out_of_range_urgency_ignored(self) -> None:
        p = parse_priority("u=9")
        assert p.urgency == 3  # default

    def test_negative_urgency_ignored(self) -> None:
        p = parse_priority("u=-1")
        assert p.urgency == 3

    def test_invalid_urgency_ignored(self) -> None:
        p = parse_priority("u=abc")
        assert p.urgency == 3

    def test_bytes_input(self) -> None:
        p = parse_priority(b"u=2, i")
        assert p.urgency == 2
        assert p.incremental is True


class TestStreamPriority:
    def test_defaults(self) -> None:
        p = StreamPriority()
        assert p.urgency == 3
        assert p.incremental is False

    def test_frozen(self) -> None:
        p = StreamPriority()
        with pytest.raises(AttributeError):
            p.urgency = 0  # type: ignore[misc]


class TestPriorityScheduler:
    def test_empty_scheduler(self) -> None:
        s = PriorityScheduler()
        assert s.next_stream() is None
        assert not s.has_pending
        assert s.stream_count == 0

    def test_single_stream(self) -> None:
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=3))
        s.schedule(1)
        assert s.next_stream() == 1

    def test_higher_urgency_first(self) -> None:
        """Lower urgency number = higher priority, served first."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=5))
        s.set_priority(3, StreamPriority(urgency=1))
        s.set_priority(5, StreamPriority(urgency=3))

        s.schedule(1)
        s.schedule(3)
        s.schedule(5)

        assert s.next_stream() == 3  # urgency 1 — highest priority
        s.unschedule(3)
        assert s.next_stream() == 5  # urgency 3
        s.unschedule(5)
        assert s.next_stream() == 1  # urgency 5
        s.unschedule(1)
        assert s.next_stream() is None

    def test_non_incremental_is_sticky(self) -> None:
        """Non-incremental streams at same urgency serve one at a time."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=3, incremental=False))
        s.set_priority(3, StreamPriority(urgency=3, incremental=False))

        s.schedule(1)
        s.schedule(3)

        # Sticky: stream 1 keeps returning until unscheduled/removed
        assert s.next_stream() == 1
        assert s.next_stream() == 1
        s.mark_wrote(1)  # no-op for non-incremental
        assert s.next_stream() == 1

        s.unschedule(1)
        assert s.next_stream() == 3

    def test_incremental_round_robin(self) -> None:
        """Incremental streams at same urgency round-robin via mark_wrote."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=3, incremental=True))
        s.set_priority(3, StreamPriority(urgency=3, incremental=True))
        s.set_priority(5, StreamPriority(urgency=3, incremental=True))

        s.schedule(1)
        s.schedule(3)
        s.schedule(5)

        # Head stays until mark_wrote rotates it
        assert s.next_stream() == 1
        s.mark_wrote(1)
        assert s.next_stream() == 3
        s.mark_wrote(3)
        assert s.next_stream() == 5
        s.mark_wrote(5)
        assert s.next_stream() == 1  # back around

    def test_non_incremental_preempts_incremental_at_same_urgency(self) -> None:
        """At same urgency, non-incremental is served before incremental."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=3, incremental=True))
        s.set_priority(3, StreamPriority(urgency=3, incremental=False))

        s.schedule(1)
        s.schedule(3)

        assert s.next_stream() == 3  # non-incremental wins at same urgency
        s.unschedule(3)
        assert s.next_stream() == 1

    def test_remove_stream(self) -> None:
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=1))
        s.set_priority(3, StreamPriority(urgency=5))

        s.schedule(1)
        s.schedule(3)

        s.remove_stream(1)
        assert s.next_stream() == 3
        assert s.stream_count == 1

    def test_update_priority_via_set(self) -> None:
        """set_priority replaces an existing entry."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=5))
        s.set_priority(1, StreamPriority(urgency=0))
        assert s.get_priority(1).urgency == 0

    def test_set_priority_rebuckets_scheduled_stream(self) -> None:
        """Changing priority of a scheduled stream moves it to the new bucket."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=5, incremental=False))
        s.set_priority(3, StreamPriority(urgency=3, incremental=False))

        s.schedule(1)
        s.schedule(3)
        assert s.next_stream() == 3  # urgency 3

        # Upgrade stream 1 to highest urgency
        s.set_priority(1, StreamPriority(urgency=0, incremental=False))
        assert s.next_stream() == 1

    def test_schedule_is_idempotent(self) -> None:
        """Scheduling the same stream twice does not create duplicates."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=3, incremental=True))
        s.set_priority(3, StreamPriority(urgency=3, incremental=True))
        s.schedule(1)
        s.schedule(1)
        s.schedule(3)

        # Round-robin should only see 1 and 3, not 1, 1, 3
        assert s.next_stream() == 1
        s.mark_wrote(1)
        assert s.next_stream() == 3
        s.mark_wrote(3)
        assert s.next_stream() == 1

    def test_has_pending(self) -> None:
        s = PriorityScheduler()
        assert not s.has_pending
        s.set_priority(1, StreamPriority())
        s.schedule(1)
        assert s.has_pending
        s.unschedule(1)
        assert not s.has_pending

    def test_unschedule_preserves_priority(self) -> None:
        """unschedule removes from ready set but keeps priority registered."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=2))
        s.schedule(1)
        s.unschedule(1)
        assert s.next_stream() is None
        assert s.stream_count == 1
        # Can re-schedule with same priority
        s.schedule(1)
        assert s.next_stream() == 1

    def test_cross_urgency_non_incremental_stickiness(self) -> None:
        """Higher-urgency arrivals preempt lower-urgency non-incremental."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=5, incremental=False))
        s.schedule(1)
        assert s.next_stream() == 1

        s.set_priority(3, StreamPriority(urgency=1, incremental=False))
        s.schedule(3)
        assert s.next_stream() == 3  # urgency 1 preempts urgency 5
        s.unschedule(3)
        assert s.next_stream() == 1  # fall back to stream 1


class TestAwaitTurn:
    """Tests for the async turn primitive that gates stream writes."""

    @pytest.mark.asyncio
    async def test_await_turn_returns_immediately_when_at_head(self) -> None:
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=3))
        s.schedule(1)
        # No one else — immediate return
        await asyncio.wait_for(s.await_turn(1), timeout=1.0)

    @pytest.mark.asyncio
    async def test_higher_urgency_write_before_lower(self) -> None:
        """An urgency-1 stream completes its write before an urgency-5 one."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=5))
        s.set_priority(3, StreamPriority(urgency=1))
        s.schedule(1)
        s.schedule(3)

        write_order: list[int] = []

        async def stream_writer(sid: int) -> None:
            await s.await_turn(sid)
            write_order.append(sid)
            s.unschedule(sid)

        # Start the lower-priority one first to ensure scheduling, not timing, decides
        task1 = asyncio.create_task(stream_writer(1))
        await asyncio.sleep(0)
        task3 = asyncio.create_task(stream_writer(3))

        await asyncio.gather(task1, task3)

        assert write_order == [3, 1]  # urgency 1 went first

    @pytest.mark.asyncio
    async def test_incremental_streams_interleave(self) -> None:
        """Two incremental streams at same urgency alternate chunks."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=3, incremental=True))
        s.set_priority(3, StreamPriority(urgency=3, incremental=True))
        s.schedule(1)
        s.schedule(3)

        write_order: list[int] = []

        async def stream_writer(sid: int, chunks: int) -> None:
            for _ in range(chunks):
                await s.await_turn(sid)
                write_order.append(sid)
                s.mark_wrote(sid)
                await asyncio.sleep(0)  # yield to other tasks
            s.unschedule(sid)

        await asyncio.gather(stream_writer(1, 3), stream_writer(3, 3))

        # Must alternate: each stream appears the same number of times
        # and no stream writes 3 times in a row.
        assert write_order.count(1) == 3
        assert write_order.count(3) == 3
        # Check no 3-in-a-row
        for i in range(len(write_order) - 2):
            assert not (write_order[i] == write_order[i + 1] == write_order[i + 2])

    @pytest.mark.asyncio
    async def test_non_incremental_serializes(self) -> None:
        """Two non-incremental streams at same urgency complete sequentially."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=3, incremental=False))
        s.set_priority(3, StreamPriority(urgency=3, incremental=False))
        s.schedule(1)
        s.schedule(3)

        write_order: list[int] = []

        async def stream_writer(sid: int, chunks: int) -> None:
            for _ in range(chunks):
                await s.await_turn(sid)
                write_order.append(sid)
                await asyncio.sleep(0)
            s.unschedule(sid)

        await asyncio.gather(stream_writer(1, 3), stream_writer(3, 3))

        # All writes for first-scheduled stream come before any for the second
        assert write_order == [1, 1, 1, 3, 3, 3]

    @pytest.mark.asyncio
    async def test_remove_stream_wakes_waiters(self) -> None:
        """Removing a stream unblocks any await_turn waiting on it."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=1))
        s.set_priority(3, StreamPriority(urgency=5))
        s.schedule(1)
        s.schedule(3)

        async def waiter() -> None:
            await s.await_turn(3)

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        assert not task.done()  # blocked behind stream 1

        s.remove_stream(1)
        await asyncio.wait_for(task, timeout=1.0)  # should complete now
