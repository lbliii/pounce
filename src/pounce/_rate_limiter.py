"""
Rate limiting and backpressure for pounce.

Implements token bucket rate limiting per client IP with request queuing
and load shedding for production overload protection.

"""

import time
from collections.abc import Callable
from threading import Lock


class TokenBucket:
    """Token bucket rate limiter for a single client.

    Classic token bucket algorithm:
    - Tokens refill at a constant rate (requests per second)
    - Bucket has a maximum capacity (burst size)
    - Each request consumes one token
    - Requests are denied when bucket is empty

    Thread-safe for free-threading mode.

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


class RateLimiter:
    """Per-IP rate limiter with token buckets.

    Tracks rate limits per client IP address using token bucket algorithm.
    Automatically cleans up stale buckets to prevent memory leaks.

    Thread-safe for concurrent worker threads.

    """

    __slots__ = ("_buckets", "_burst", "_cleanup_interval", "_last_cleanup", "_lock", "_rate")

    def __init__(self, rate: float, burst: int) -> None:
        """Initialize rate limiter.

        Args:
            rate: Requests per second allowed per IP
            burst: Maximum burst size per IP

        Example:
            # Allow 100 req/s with burst of 200
            limiter = RateLimiter(rate=100.0, burst=200)

        """
        self._rate = rate
        self._burst = burst
        self._buckets: dict[str, TokenBucket] = {}
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
        # Periodic cleanup of stale buckets
        self._maybe_cleanup()

        with self._lock:
            # Get or create bucket for this IP
            if client_ip not in self._buckets:
                self._buckets[client_ip] = TokenBucket(self._rate, self._burst)

            bucket = self._buckets[client_ip]

        # Try to consume a token (outside the lock for better concurrency)
        return bucket.consume()

    def _maybe_cleanup(self) -> None:
        """Clean up stale buckets to prevent memory leaks.

        Removes buckets that are full (no recent activity).

        """
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return

        with self._lock:
            # Remove buckets that would be at full capacity if refilled now
            stale = []
            for ip, bucket in self._buckets.items():
                # Calculate what tokens would be after refill
                elapsed = now - bucket._last_refill
                refill = elapsed * bucket._rate
                tokens_after_refill = min(bucket._capacity, bucket._tokens + refill)

                # If bucket would be full, it's stale
                if tokens_after_refill >= bucket._capacity:
                    stale.append(ip)

            for ip in stale:
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
