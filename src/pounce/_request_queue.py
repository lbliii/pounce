"""
Request queuing and load shedding for pounce.

Implements application-level request queueing with bounded capacity
to gracefully handle server overload.

"""

import asyncio
import time
from collections.abc import Callable
from typing import Any


class RequestQueue:
    """Bounded request queue for load shedding.

    Queues incoming requests when workers are busy. Returns 503 when
    queue is full to shed load and prevent resource exhaustion.

    Thread-safe for concurrent request handling.

    """

    __slots__ = ("_max_depth", "_queue_depth", "_queue_depth_lock", "_semaphore")

    def __init__(self, max_depth: int) -> None:
        """Initialize request queue.

        Args:
            max_depth: Maximum number of queued requests (0 = unlimited)

        Example:
            # Queue up to 100 requests
            queue = RequestQueue(max_depth=100)

        """
        self._max_depth = max_depth
        # Use semaphore to limit concurrent processing
        # When max_depth is 0 (unlimited), use a very large value
        sem_capacity = max_depth if max_depth > 0 else 100_000
        self._semaphore = asyncio.Semaphore(sem_capacity)
        self._queue_depth = 0
        self._queue_depth_lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Try to acquire a slot in the queue.

        Returns:
            True if acquired, False if queue is full

        """
        if self._max_depth == 0:
            # Unlimited queue
            await self._semaphore.acquire()
            async with self._queue_depth_lock:
                self._queue_depth += 1
            return True

        # Try to acquire without blocking
        if self._semaphore.locked():
            # Queue is full
            return False

        # Acquire slot
        await self._semaphore.acquire()
        async with self._queue_depth_lock:
            self._queue_depth += 1
        return True

    def release(self) -> None:
        """Release a slot in the queue."""
        self._semaphore.release()
        # Note: we can't use async with here, so we'll update queue depth
        # in a sync manner. This is safe because _queue_depth is only
        # decremented here and incremented in acquire() under lock.
        # The worst case is a slightly stale queue depth metric.
        self._queue_depth = max(0, self._queue_depth - 1)

    def get_depth(self) -> int:
        """Get current queue depth.

        Returns:
            Number of requests currently queued

        """
        return self._queue_depth

    def get_max_depth(self) -> int:
        """Get maximum queue depth.

        Returns:
            Maximum queue capacity (0 = unlimited)

        """
        return self._max_depth


class QueueMetrics:
    """Metrics for request queue monitoring.

    Tracks queue depth, wait times, and rejection rate.

    """

    __slots__ = (
        "_lock",
        "_max_wait_time_ms",
        "_total_queued",
        "_total_rejected",
        "_total_wait_time_ms",
    )

    def __init__(self) -> None:
        """Initialize queue metrics."""
        self._total_queued = 0
        self._total_rejected = 0
        self._total_wait_time_ms = 0.0
        self._max_wait_time_ms = 0.0
        self._lock = asyncio.Lock()

    async def record_queued(self, wait_time_ms: float) -> None:
        """Record a queued request.

        Args:
            wait_time_ms: Time spent waiting in queue (milliseconds)

        """
        async with self._lock:
            self._total_queued += 1
            self._total_wait_time_ms += wait_time_ms
            self._max_wait_time_ms = max(self._max_wait_time_ms, wait_time_ms)

    async def record_rejected(self) -> None:
        """Record a rejected request (queue full)."""
        async with self._lock:
            self._total_rejected += 1

    def get_stats(self) -> dict[str, Any]:
        """Get queue statistics.

        Returns:
            Dictionary with queue metrics

        """
        return {
            "total_queued": self._total_queued,
            "total_rejected": self._total_rejected,
            "avg_wait_time_ms": (
                self._total_wait_time_ms / self._total_queued if self._total_queued > 0 else 0.0
            ),
            "max_wait_time_ms": self._max_wait_time_ms,
            "rejection_rate": (
                self._total_rejected / (self._total_queued + self._total_rejected)
                if (self._total_queued + self._total_rejected) > 0
                else 0.0
            ),
        }


def create_queue_wrapper(
    app: Callable,
    queue: RequestQueue,
    metrics: QueueMetrics | None = None,
) -> Callable:
    """Wrap an ASGI app with request queueing.

    Buffers requests when workers are busy. Returns 503 when queue is full.

    Args:
        app: Original ASGI app
        queue: RequestQueue instance
        metrics: Optional QueueMetrics instance for monitoring

    Returns:
        Wrapped ASGI app with request queueing

    Example:
        queue = RequestQueue(max_depth=100)
        metrics = QueueMetrics()
        app = create_queue_wrapper(app, queue, metrics)

    """

    async def wrapper(scope: dict, receive: Callable, send: Callable) -> None:
        """Queue wrapper."""
        if scope["type"] != "http":
            # Only queue HTTP requests
            await app(scope, receive, send)
            return

        # Try to acquire a queue slot
        start_time = time.monotonic()
        acquired = await queue.acquire()

        if not acquired:
            # Queue is full! Shed load with 503
            if metrics:
                await metrics.record_rejected()

            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"retry-after", b"5"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"Service Unavailable - Server Overloaded",
                }
            )
            return

        # Acquired slot, process request
        try:
            wait_time_ms = (time.monotonic() - start_time) * 1000
            if metrics:
                await metrics.record_queued(wait_time_ms)

            # Process request
            await app(scope, receive, send)
        finally:
            # Always release slot
            queue.release()

    return wrapper
