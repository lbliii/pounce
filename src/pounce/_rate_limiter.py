"""
Rate limiting and backpressure for pounce.

Implements token bucket rate limiting per client IP with request queuing
and load shedding for production overload protection.

Per-worker semantics (issue #109): each worker holds its own token buckets.
In thread mode (3.14t) one limiter is genuinely shared, but in process mode
(GIL build, fork) and subinterpreter mode every worker inherits an independent
copy of the bucket state with no IPC between them. The real per-IP ceiling is
therefore ``rate x workers`` (burst ``burst x workers``). The configured number
is the aggregate guarantee only when ``workers=1``. See
``docs/deployment/backpressure.md``.

"""

import time
from collections import OrderedDict
from collections.abc import Callable
from threading import Lock

# Hard cap on the number of distinct client IPs tracked at once. Bounds memory
# even under a flood of unique source IPs (e.g. a wide IPv6 /64). When the cap
# is reached, the least-recently-seen bucket is evicted (LRU) to make room.
DEFAULT_MAX_TRACKED_IPS = 100_000

# Idle buckets (no request newer than this many seconds) are reaped during
# cleanup regardless of their token level, so a sustained flood that keeps
# buckets non-full cannot pin entries forever.
_IDLE_EVICTION_SECONDS = 300.0


class TokenBucket:
    """Token bucket rate limiter for a single client.

    Classic token bucket algorithm:
    - Tokens refill at a constant rate (requests per second)
    - Bucket has a maximum capacity (burst size)
    - Each request consumes one token
    - Requests are denied when bucket is empty

    Thread-safe for free-threading mode.

    Per-worker (issue #109): a bucket lives in a single worker's address space.
    With multiple workers the aggregate per-IP allowance scales with the worker
    count -- see the module docstring and ``docs/deployment/backpressure.md``.

    """

    __slots__ = ("_capacity", "_last_refill", "_lock", "_rate", "_tokens")

    def __init__(self, rate: float, burst: int) -> None:
        """Initialize token bucket.

        Args:
            rate: Tokens per second to refill
            burst: Maximum tokens (burst capacity)

        """
        self._capacity = burst
        self._rate = rate
        self._tokens = float(burst)
        self._last_refill = time.monotonic()
        self._lock = Lock()

    def consume(self) -> bool:
        """Try to consume one token.

        Returns:
            True if token was available, False if rate limited

        """
        with self._lock:
            now = time.monotonic()

            # Refill tokens based on time elapsed
            elapsed = now - self._last_refill
            refill = elapsed * self._rate
            self._tokens = min(self._capacity, self._tokens + refill)
            self._last_refill = now

            # Try to consume a token
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return True

            return False

    def is_full_at(self, now: float) -> bool:
        """Return True if the bucket would be at capacity after refill at ``now``.

        Used by :class:`RateLimiter` cleanup to identify inactive buckets
        without reaching into private fields from outside the lock.

        """
        with self._lock:
            elapsed = now - self._last_refill
            tokens_after_refill = min(self._capacity, self._tokens + elapsed * self._rate)
            return tokens_after_refill >= self._capacity

    def idle_since(self, now: float) -> float:
        """Return seconds since this bucket last refilled (i.e. last consume).

        ``_last_refill`` advances on every :meth:`consume`, so it doubles as a
        last-touched timestamp. Used by :class:`RateLimiter` cleanup to evict
        buckets that have seen no traffic, even when they never refilled to
        full capacity.

        """
        with self._lock:
            return now - self._last_refill


class RateLimiter:
    """Per-IP rate limiter with token buckets.

    Tracks rate limits per client IP address using token bucket algorithm.
    Automatically cleans up stale buckets to prevent memory leaks.

    Thread-safe for concurrent worker threads.

    """

    __slots__ = (
        "_buckets",
        "_burst",
        "_cleanup_interval",
        "_last_cleanup",
        "_lock",
        "_max_tracked_ips",
        "_rate",
    )

    def __init__(
        self,
        rate: float,
        burst: int,
        max_tracked_ips: int = DEFAULT_MAX_TRACKED_IPS,
    ) -> None:
        """Initialize rate limiter.

        Args:
            rate: Requests per second allowed per IP
            burst: Maximum burst size per IP
            max_tracked_ips: Hard cap on the number of distinct client IPs
                tracked at once. When exceeded, the least-recently-seen bucket
                is evicted (LRU). Bounds memory under a unique-IP flood.

        Example:
            # Allow 100 req/s with burst of 200
            limiter = RateLimiter(rate=100.0, burst=200)

        """
        if max_tracked_ips < 1:
            msg = f"max_tracked_ips must be >= 1 (got {max_tracked_ips})"
            raise ValueError(msg)
        self._rate = rate
        self._burst = burst
        self._max_tracked_ips = max_tracked_ips
        # OrderedDict gives O(1) LRU semantics: move_to_end on touch, popitem
        # of the oldest entry when we hit the cap.
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()
        self._lock = Lock()
        self._cleanup_interval = 300.0  # Clean up every 5 minutes
        self._last_cleanup = time.monotonic()

    def check_rate_limit(self, client_ip: str) -> bool:
        """Check if request is rate limited.

        Args:
            client_ip: Client IP address

        Returns:
            True if request is allowed, False if rate limited

        """
        # Periodic cleanup of stale buckets (time- or count-triggered).
        self._maybe_cleanup()

        with self._lock:
            # Get or create bucket for this IP, maintaining LRU ordering.
            bucket = self._buckets.get(client_ip)
            if bucket is None:
                # Enforce the hard cap BEFORE inserting so the map can never
                # exceed max_tracked_ips, even under a flood of unique IPs.
                while len(self._buckets) >= self._max_tracked_ips:
                    self._buckets.popitem(last=False)  # evict least-recently-seen
                bucket = TokenBucket(self._rate, self._burst)
                self._buckets[client_ip] = bucket
            else:
                # Mark as most-recently-seen for LRU eviction.
                self._buckets.move_to_end(client_ip)

        # Try to consume a token (outside the lock for better concurrency)
        return bucket.consume()

    def _maybe_cleanup(self) -> None:
        """Clean up stale buckets to prevent unbounded growth.

        Triggers when the cleanup interval has elapsed OR the map has grown
        past the tracked-IP cap. Evicts buckets that are either at full
        capacity (no pending traffic) or idle (no request within the idle
        window) so sustained floods cannot pin entries forever.

        """
        now = time.monotonic()
        with self._lock:
            over_cap = len(self._buckets) >= self._max_tracked_ips
            if not over_cap and now - self._last_cleanup < self._cleanup_interval:
                return
            # Snapshot bucket references under the outer lock so concurrent
            # insertions in check_rate_limit don't race with iteration, then
            # probe each bucket under its own lock.
            snapshot = list(self._buckets.items())

        stale = [
            ip
            for ip, bucket in snapshot
            if bucket.is_full_at(now) or bucket.idle_since(now) >= _IDLE_EVICTION_SECONDS
        ]

        with self._lock:
            for ip in stale:
                # A bucket may have been touched between probe and delete;
                # only evict if it is still stale at deletion time.
                bucket = self._buckets.get(ip)
                if bucket is not None and (
                    bucket.is_full_at(now) or bucket.idle_since(now) >= _IDLE_EVICTION_SECONDS
                ):
                    del self._buckets[ip]

            self._last_cleanup = now


def create_rate_limit_wrapper(
    app: Callable,
    rate_limiter: RateLimiter,
) -> Callable:
    """Wrap an ASGI app with rate limiting.

    Intercepts requests and applies rate limiting before passing to app.
    Returns 429 Too Many Requests when rate limit is exceeded.

    Args:
        app: Original ASGI app
        rate_limiter: RateLimiter instance

    Returns:
        Wrapped ASGI app with rate limiting

    Example:
        limiter = RateLimiter(rate=100.0, burst=200)
        app = create_rate_limit_wrapper(app, limiter)

    """

    async def wrapper(scope: dict, receive: Callable, send: Callable) -> None:
        """Rate limit wrapper."""
        if scope["type"] != "http":
            # Only rate limit HTTP requests
            await app(scope, receive, send)
            return

        # Extract client IP from scope
        client = scope.get("client")
        if client is None:
            # No client info, allow request
            await app(scope, receive, send)
            return

        client_ip = client[0] if isinstance(client, tuple) else str(client)

        # Check rate limit
        if not rate_limiter.check_rate_limit(client_ip):
            # Rate limited! Return 429
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"retry-after", b"1"),
                    ],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"Too Many Requests",
                }
            )
            return

        # Allow request
        await app(scope, receive, send)

    return wrapper
