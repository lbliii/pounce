"""
Timing utilities and Server-Timing header builder.

Provides monotonic clock helpers for request-level timing and a builder
for the Server-Timing HTTP header (RFC 6797 extension).

All functions use time.monotonic_ns() for high-resolution, monotonic timing
that is not affected by system clock adjustments.

Example:
    >>> start = monotonic_ns()
    >>> # ... do work ...
    >>> dur = elapsed_ms(start)
    >>> header = ServerTiming()
    >>> header.add("app", dur)
    >>> header.render()
    'app;dur=12.3'

"""

import time
from dataclasses import dataclass, field


def monotonic_ns() -> int:
    """Return the current monotonic clock value in nanoseconds."""
    return time.monotonic_ns()


def elapsed_ms(start_ns: int) -> float:
    """Compute elapsed time in milliseconds since start_ns.

    Args:
        start_ns: Start time from monotonic_ns().

    Returns:
        Elapsed time in milliseconds, rounded to 1 decimal place.
    """
    return round((time.monotonic_ns() - start_ns) / 1_000_000, 1)


@dataclass(slots=True)
class ServerTiming:
    """Accumulates Server-Timing metrics for a single request.

    Collects named duration measurements and renders them as a
    Server-Timing header value.

    Example:
        >>> timing = ServerTiming()
        >>> timing.add("parse", 0.3)
        >>> timing.add("app", 12.1)
        >>> timing.add("encode", 0.8)
        >>> timing.render()
        'parse;dur=0.3, app;dur=12.1, encode;dur=0.8'

    """

    _entries: list[tuple[str, float]] = field(default_factory=list)

    def add(self, name: str, duration_ms: float) -> None:
        """Record a named timing measurement.

        Args:
            name: Metric name (e.g., "parse", "app", "encode").
            duration_ms: Duration in milliseconds.
        """
        self._entries.append((name, round(duration_ms, 1)))

    def render(self) -> str:
        """Render as a Server-Timing header value.

        Returns:
            Header value string, e.g. 'parse;dur=0.3, app;dur=12.1'.
            Empty string if no entries have been added.
        """
        if not self._entries:
            return ""
        return ", ".join(f"{name};dur={dur}" for name, dur in self._entries)

    def render_bytes(self) -> bytes:
        """Render as bytes for direct header insertion.

        Returns:
            UTF-8 encoded header value.
        """
        return self.render().encode("ascii")
