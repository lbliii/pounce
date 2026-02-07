"""Tests for pounce._timing — monotonic clock and Server-Timing header."""

import time

from pounce._timing import ServerTiming, elapsed_ms, monotonic_ns


class TestMonotonicNs:
    """monotonic_ns() returns nanosecond timestamps."""

    def test_returns_int(self):
        assert isinstance(monotonic_ns(), int)

    def test_monotonically_increasing(self):
        a = monotonic_ns()
        b = monotonic_ns()
        assert b >= a

    def test_nonzero(self):
        assert monotonic_ns() > 0


class TestElapsedMs:
    """elapsed_ms() computes duration from a start timestamp."""

    def test_positive_duration(self):
        start = monotonic_ns()
        time.sleep(0.01)  # 10ms
        dur = elapsed_ms(start)
        assert dur >= 5.0  # Allow for scheduling jitter
        assert dur < 500.0  # Sanity upper bound

    def test_near_zero(self):
        start = monotonic_ns()
        dur = elapsed_ms(start)
        assert dur >= 0.0
        assert dur < 50.0  # Should be near-instant

    def test_rounded_to_one_decimal(self):
        start = monotonic_ns()
        dur = elapsed_ms(start)
        # Check that it has at most 1 decimal place
        assert dur == round(dur, 1)


class TestServerTiming:
    """ServerTiming accumulates metrics and renders the header."""

    def test_empty_render(self):
        timing = ServerTiming()
        assert timing.render() == ""

    def test_single_entry(self):
        timing = ServerTiming()
        timing.add("app", 12.3)
        assert timing.render() == "app;dur=12.3"

    def test_multiple_entries(self):
        timing = ServerTiming()
        timing.add("parse", 0.3)
        timing.add("app", 12.1)
        timing.add("encode", 0.8)
        assert timing.render() == "parse;dur=0.3, app;dur=12.1, encode;dur=0.8"

    def test_render_bytes(self):
        timing = ServerTiming()
        timing.add("app", 5.0)
        result = timing.render_bytes()
        assert result == b"app;dur=5.0"
        assert isinstance(result, bytes)

    def test_render_bytes_empty(self):
        timing = ServerTiming()
        assert timing.render_bytes() == b""

    def test_duration_rounded(self):
        timing = ServerTiming()
        timing.add("app", 12.3456)
        assert timing.render() == "app;dur=12.3"

    def test_zero_duration(self):
        timing = ServerTiming()
        timing.add("fast", 0.0)
        assert timing.render() == "fast;dur=0.0"
