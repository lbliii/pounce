"""
HTTP Priority Signals (RFC 9218).

Parses the ``Priority`` header (``u=N, i``) from HTTP/2 requests and
provides a priority-based scheduler for DATA frame writes.

RFC 9218 defines:
- ``u=N`` (urgency): 0-7, default 3. Lower is more urgent.
- ``i`` (incremental): boolean. If present, response can be interleaved.

Scheduling policy (§8):
- Streams at lower urgency numbers are served before higher ones.
- At the same urgency, non-incremental streams are served one at a
  time (sticky): the first-ready stream holds the slot until it is
  unscheduled or removed.
- At the same urgency, incremental streams round-robin via
  :meth:`PriorityScheduler.mark_wrote` after each chunk.
- Non-incremental streams at a given urgency preempt incremental
  streams at that urgency, since non-incremental responses expect
  serial delivery.

"""

import asyncio
import threading
from collections import deque
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

    for raw in value.split(","):
        part = raw.strip()
        if not part:
            continue

        if part.startswith("u="):
            try:
                u = int(part[2:].strip())
            except ValueError:
                continue
            if 0 <= u <= 7:
                urgency = u
        elif part == "i":
            incremental = True

    return StreamPriority(urgency=urgency, incremental=incremental)


class PriorityScheduler:
    """RFC 9218-compliant priority scheduler for HTTP/2 streams.

    Maintains per-stream priority and a ready-set of streams that have
    data to send. :meth:`next_stream` returns the stream that should
    write next, per the policy described in the module docstring.

    Usage::

        scheduler.set_priority(sid, parse_priority(header))
        scheduler.schedule(sid)
        while (ready := scheduler.next_stream()) is not None:
            write_one_chunk(ready)
            if has_more_data:
                scheduler.mark_wrote(ready)  # rotate incremental
            else:
                scheduler.unschedule(ready)
        scheduler.remove_stream(sid)  # on stream close

    Thread-safe: a lock protects all mutable state for correctness
    under free-threading.

    """

    __slots__ = ("_lock", "_priorities", "_ready_inc", "_ready_noninc", "_turn")

    def __init__(self) -> None:
        self._priorities: dict[int, StreamPriority] = {}
        self._ready_noninc: dict[int, deque[int]] = {}
        self._ready_inc: dict[int, deque[int]] = {}
        self._lock = threading.Lock()
        # Per-stream asyncio.Event set when it is the stream's turn.
        # Populated lazily from within the event loop in await_turn().
        self._turn: dict[int, asyncio.Event] = {}

    def set_priority(self, stream_id: int, priority: StreamPriority) -> None:
        """Set or update the priority for a stream.

        If the stream is already scheduled, it is re-bucketed to match
        the new urgency/incremental classification.
        """
        with self._lock:
            was_scheduled = self._remove_from_ready(stream_id)
            self._priorities[stream_id] = priority
            if was_scheduled:
                self._add_to_ready(stream_id, priority)
            self._wake_current()

    def get_priority(self, stream_id: int) -> StreamPriority:
        """Return the priority for a stream (default: urgency=3, non-incremental)."""
        with self._lock:
            return self._priorities.get(stream_id, StreamPriority())

    def schedule(self, stream_id: int) -> None:
        """Mark a stream as ready to send data.

        Idempotent: repeated calls for the same stream do not duplicate
        entries in the ready set.
        """
        with self._lock:
            priority = self._priorities.get(stream_id, StreamPriority())
            self._add_to_ready(stream_id, priority)
            self._wake_current()

    def unschedule(self, stream_id: int) -> None:
        """Remove a stream from the ready set without forgetting its priority."""
        with self._lock:
            self._remove_from_ready(stream_id)
            self._wake_current()

    def mark_wrote(self, stream_id: int) -> None:
        """Signal that a stream just wrote a chunk.

        For incremental streams, rotates the round-robin cursor so the
        next same-urgency incremental stream gets the next slot. No-op
        for non-incremental streams (they stay at the head until
        :meth:`unschedule` or :meth:`remove_stream`).
        """
        with self._lock:
            priority = self._priorities.get(stream_id)
            if priority is None or not priority.incremental:
                return
            bucket = self._ready_inc.get(priority.urgency)
            if bucket and bucket[0] == stream_id:
                bucket.popleft()
                bucket.append(stream_id)
                self._wake_current()

    def next_stream(self) -> int | None:
        """Return the next stream that should write data, per RFC 9218.

        Does not mutate state — call :meth:`mark_wrote` to rotate
        incremental streams after a write.

        Returns:
            Stream ID with highest effective priority, or None if no
            streams are ready.
        """
        with self._lock:
            urgencies = set(self._ready_noninc) | set(self._ready_inc)
            for urgency in sorted(urgencies):
                ni = self._ready_noninc.get(urgency)
                if ni:
                    return ni[0]
                inc = self._ready_inc.get(urgency)
                if inc:
                    return inc[0]
            return None

    def remove_stream(self, stream_id: int) -> None:
        """Remove a stream entirely — priority and ready state."""
        with self._lock:
            self._priorities.pop(stream_id, None)
            self._remove_from_ready(stream_id)
            event = self._turn.pop(stream_id, None)
            self._wake_current()
        if event is not None:
            event.set()  # unblock any waiter on the removed stream

    async def await_turn(self, stream_id: int) -> None:
        """Block until this stream is the one :meth:`next_stream` picks.

        Called by the H2/H3 send path before writing a DATA chunk so that
        priority ordering actually constrains when streams emit bytes.
        The stream must already be scheduled — typically the caller does::

            scheduler.schedule(stream_id)
            await scheduler.await_turn(stream_id)
            write_chunk()
            scheduler.mark_wrote(stream_id)  # for incremental rotation
        """
        while True:
            with self._lock:
                if self._priorities.get(stream_id) is None:
                    return  # stream was removed — caller will notice
                picked = self._peek_locked()
                if picked == stream_id:
                    return
                event = self._turn.setdefault(stream_id, asyncio.Event())
                event.clear()
            await event.wait()

    def _peek_locked(self) -> int | None:
        urgencies = set(self._ready_noninc) | set(self._ready_inc)
        for urgency in sorted(urgencies):
            ni = self._ready_noninc.get(urgency)
            if ni:
                return ni[0]
            inc = self._ready_inc.get(urgency)
            if inc:
                return inc[0]
        return None

    def _wake_current(self) -> None:
        """Wake the waiter for whichever stream is now at the head."""
        picked = self._peek_locked()
        if picked is None:
            return
        event = self._turn.get(picked)
        if event is not None:
            event.set()

    @property
    def has_pending(self) -> bool:
        """True if any stream is currently ready to send."""
        with self._lock:
            return any(self._ready_noninc.values()) or any(self._ready_inc.values())

    @property
    def stream_count(self) -> int:
        """Number of streams with registered priorities."""
        with self._lock:
            return len(self._priorities)

    def _add_to_ready(self, stream_id: int, priority: StreamPriority) -> None:
        bucket = self._ready_inc if priority.incremental else self._ready_noninc
        queue = bucket.setdefault(priority.urgency, deque())
        if stream_id not in queue:
            queue.append(stream_id)

    def _remove_from_ready(self, stream_id: int) -> bool:
        for bucket in (self._ready_noninc, self._ready_inc):
            for queue in bucket.values():
                try:
                    queue.remove(stream_id)
                except ValueError:
                    continue
                return True
        return False
