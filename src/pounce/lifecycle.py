"""
Connection lifecycle events — structured, immutable records.

Each event captures a moment in a connection's lifecycle as a frozen
dataclass.  Events are designed for aggregation, replay, and
observability — not logging.  They form the foundation of the
full-stack effect observability system.

Events are produced by the worker and consumed by an optional
``LifecycleCollector``.  The default collector (``NoopCollector``)
discards all events with zero overhead.  Replace it with a
``BufferedCollector`` or custom implementation to capture events.

All timestamps use ``time.monotonic_ns()`` for high-resolution,
monotonic ordering that is not affected by system clock adjustments.

"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

# ---------------------------------------------------------------------------
# Event types — frozen, slotted, serializable
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConnectionOpened:
    """A new TCP connection was accepted."""

    connection_id: int
    worker_id: int
    client_addr: str
    client_port: int
    server_addr: str
    server_port: int
    protocol: str  # "h1", "h2", "websocket"
    timestamp_ns: int


@dataclass(frozen=True, slots=True)
class RequestStarted:
    """An HTTP request head was fully parsed."""

    connection_id: int
    worker_id: int
    method: str
    path: str
    http_version: str
    timestamp_ns: int


@dataclass(frozen=True, slots=True)
class ResponseCompleted:
    """An HTTP response was fully sent."""

    connection_id: int
    worker_id: int
    status: int
    bytes_sent: int
    duration_ms: float
    timestamp_ns: int


@dataclass(frozen=True, slots=True)
class ClientDisconnected:
    """The client closed the connection unexpectedly."""

    connection_id: int
    worker_id: int
    during_streaming: bool
    timestamp_ns: int


@dataclass(frozen=True, slots=True)
class ConnectionClosed:
    """The TCP connection was closed (by either side)."""

    connection_id: int
    worker_id: int
    requests_served: int
    total_bytes_sent: int
    duration_ms: float
    reason: str  # "complete" | "timeout" | "client_disconnect" | "error" | "backpressure"
    timestamp_ns: int


# Union of all lifecycle event types
type LifecycleEvent = (
    ConnectionOpened | RequestStarted | ResponseCompleted | ClientDisconnected | ConnectionClosed
)


# ---------------------------------------------------------------------------
# Collector protocol and implementations
# ---------------------------------------------------------------------------


class LifecycleCollector(Protocol):
    """Interface for consuming lifecycle events.

    Implementations must be thread-safe — in free-threading mode,
    multiple workers (threads) will call ``record()`` concurrently.

    """

    def record(self, event: LifecycleEvent) -> None:
        """Record a lifecycle event."""
        ...


class NoopCollector:
    """Default collector — discards all events.

    Zero overhead: the ``record()`` method is an empty function body.
    Used when no observability is configured.

    """

    __slots__ = ()

    def record(self, event: LifecycleEvent) -> None:
        """Discard the event."""


class BufferedCollector:
    """Thread-safe collector that buffers events for batch processing.

    Events accumulate in an internal list.  Call ``flush()`` to retrieve
    and clear the buffer, or register an ``on_flush`` callback for
    automatic batch processing.

    Thread-safe via ``threading.Lock`` — safe for free-threading mode
    where multiple worker threads produce events concurrently.

    Args:
        max_buffer_size: Maximum events to buffer before auto-flushing.
            0 means no limit (manual flush only).
        on_flush: Optional callback invoked with the event batch when
            the buffer is flushed (manually or by reaching max size).

    """

    __slots__ = ("_buffer", "_lock", "_max_size", "_on_flush")

    def __init__(
        self,
        *,
        max_buffer_size: int = 0,
        on_flush: Callable[[list[LifecycleEvent]], None] | None = None,
    ) -> None:
        self._buffer: list[LifecycleEvent] = []
        self._lock = threading.Lock()
        self._max_size = max_buffer_size
        self._on_flush = on_flush

    def record(self, event: LifecycleEvent) -> None:
        """Append an event to the buffer.

        If ``max_buffer_size`` is set and reached, the buffer is
        automatically flushed.

        """
        with self._lock:
            self._buffer.append(event)
            if self._max_size > 0 and len(self._buffer) >= self._max_size:
                self._flush_locked()

    def flush(self) -> list[LifecycleEvent]:
        """Retrieve and clear the buffered events.

        Returns:
            List of events accumulated since the last flush.

        """
        with self._lock:
            events = self._flush_locked()
        return events

    def _flush_locked(self) -> list[LifecycleEvent]:
        """Flush the buffer while holding the lock.  Returns the batch."""
        batch = self._buffer
        self._buffer = []
        if self._on_flush is not None and batch:
            self._on_flush(batch)
        return batch

    def __len__(self) -> int:
        with self._lock:
            return len(self._buffer)


# ---------------------------------------------------------------------------
# Connection ID generator — thread-safe monotonic counter
# ---------------------------------------------------------------------------

_id_counter = 0
_id_lock = threading.Lock()


def next_connection_id() -> int:
    """Return a globally unique, monotonically increasing connection ID.

    Thread-safe — uses a lock for correctness under free-threading.

    """
    global _id_counter
    with _id_lock:
        _id_counter += 1
        return _id_counter


def monotonic_ns() -> int:
    """Return the current monotonic clock value in nanoseconds."""
    return time.monotonic_ns()
