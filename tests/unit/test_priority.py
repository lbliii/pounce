"""Tests for HTTP Priority Signals (RFC 9218)."""

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
        import pytest

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
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=5))
        s.set_priority(3, StreamPriority(urgency=1))
        s.set_priority(5, StreamPriority(urgency=3))

        s.schedule(1)
        s.schedule(3)
        s.schedule(5)

        assert s.next_stream() == 3  # urgency 1
        assert s.next_stream() == 5  # urgency 3
        assert s.next_stream() == 1  # urgency 5

    def test_fifo_within_same_urgency(self) -> None:
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=3))
        s.set_priority(3, StreamPriority(urgency=3))

        s.schedule(1)
        s.schedule(3)

        assert s.next_stream() == 1  # scheduled first
        assert s.next_stream() == 3

    def test_remove_stream(self) -> None:
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=1))
        s.set_priority(3, StreamPriority(urgency=5))

        s.schedule(1)
        s.schedule(3)

        s.remove_stream(1)
        # Stream 1 was removed, should skip to stream 3
        assert s.next_stream() == 3

    def test_update_priority_via_set(self) -> None:
        """set_priority replaces an existing entry (update_priority was merged)."""
        s = PriorityScheduler()
        s.set_priority(1, StreamPriority(urgency=5))
        s.set_priority(1, StreamPriority(urgency=0))
        assert s.get_priority(1).urgency == 0

    def test_has_pending(self) -> None:
        s = PriorityScheduler()
        assert not s.has_pending
        s.set_priority(1, StreamPriority())
        s.schedule(1)
        assert s.has_pending
        s.next_stream()
        assert not s.has_pending
