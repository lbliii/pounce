"""
HTTP Priority Signals (RFC 9218).

Parses the ``Priority`` header (``u=N, i``) from HTTP/2 requests and
provides a simple priority-based scheduler for DATA frame writes.

RFC 9218 defines:
- ``u=N`` (urgency): 0-7, default 3. Lower is more urgent.
- ``i`` (incremental): boolean. If present, response can be interleaved.

This module provides:
1. Parsing of Priority header values
2. A per-connection priority scheduler for ordering stream writes

"""

import heapq
import threading
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StreamPriority:
    """Parsed priority for a single HTTP/2 stream.

    Attributes:
        urgency: 0 (highest) to 7 (lowest). Default: 3.
        incremental: If True, response can be interleaved with others.
    """

    urgency: int = 3
    incremental: bool = False

def parse_priority(value: bytes | str) -> StreamPriority:
    """Parse an RFC 9218 Priority header value.

    Args:
        value: The Priority header value (e.g., ``u=1, i``).

    Returns:
        Parsed StreamPriority.

    Examples:
        >>> parse_priority("u=0, i")
        StreamPriority(urgency=0, incremental=True)
        >>> parse_priority("u=7")
        StreamPriority(urgency=7, incremental=False)
        >>> parse_priority("")
        StreamPriority(urgency=3, incremental=False)

    """
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")

    urgency = 3
    incremental = False

    for part in value.split(","):
        part = part.strip()
        if not part:
            continue

        if part.startswith("u="):
            try:
                u = int(part[2:].strip())
                if 0 <= u <= 7:
                    urgency = u
            except ValueError:
                pass

        elif part.strip() == "i":
            incremental = True

    return StreamPriority(urgency=urgency, incremental=incremental)

class PriorityScheduler:
    """Priority-based scheduler for HTTP/2 stream writes.

    Maintains a per-stream priority and provides ordering for which
    stream should get bandwidth next. Higher urgency (lower number)
    streams are served first. Among same-urgency streams, incremental
    streams can interleave while non-incremental streams are sequential.

    All operations are O(log n) via a min-heap. Thread-safe: a lock
    protects all mutable state for correctness under free-threading.

    """

    __slots__ = ("_counter", "_lock", "_pending", "_priorities")

    def __init__(self) -> None:
        self._priorities: dict[int, StreamPriority] = {}
        self._pending: list[tuple[int, int, int]] = []
        self._counter: int = 0
        self._lock = threading.Lock()

    def set_priority(self, stream_id: int, priority: StreamPriority) -> None:
        """Set or update the priority for a stream.

        Args:
            stream_id: The HTTP/2 stream identifier.
            priority: The parsed priority for this stream.

        """
        with self._lock:
            self._priorities[stream_id] = priority

    def get_priority(self, stream_id: int) -> StreamPriority:
        """Get the priority for a stream (default: urgency=3, not incremental)."""
        with self._lock:
            return self._priorities.get(stream_id, StreamPriority())

    def schedule(self, stream_id: int) -> None:
        """Mark a stream as ready to send data.

        Args:
            stream_id: The stream that has data to write.

        """
        with self._lock:
            priority = self._priorities.get(stream_id, StreamPriority())
            self._counter += 1
            heapq.heappush(
                self._pending,
                (priority.urgency, self._counter, stream_id),
            )

    def next_stream(self) -> int | None:
        """Get the next stream that should write data.

        Returns:
            Stream ID with highest priority (lowest urgency), or None
            if no streams are pending.

        """
        with self._lock:
            while self._pending:
                _, _, stream_id = heapq.heappop(self._pending)
                # Stream may have been removed since scheduling
                if stream_id in self._priorities:
                    return stream_id
            return None

    def remove_stream(self, stream_id: int) -> None:
        """Remove a stream from the scheduler.

        Args:
            stream_id: The stream to remove.

        """
        with self._lock:
            self._priorities.pop(stream_id, None)

    @property
    def has_pending(self) -> bool:
        """True if any streams are waiting to send."""
        with self._lock:
            return len(self._pending) > 0

    @property
    def stream_count(self) -> int:
        """Number of tracked streams."""
        with self._lock:
            return len(self._priorities)
