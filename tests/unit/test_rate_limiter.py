"""
Tests for rate limiting and backpressure.

"""

import time

import pytest

from pounce._rate_limiter import RateLimiter, TokenBucket, create_rate_limit_wrapper
from pounce.config import ServerConfig


class TestTokenBucket:
    """Tests for TokenBucket rate limiter."""

    def test_initial_tokens_at_capacity(self):
        """Test that bucket starts with full capacity."""
        bucket = TokenBucket(rate=10.0, burst=20)
        # Should be able to consume up to burst size
        for _ in range(20):
            assert bucket.consume() is True
        # 21st request should be rate limited
        assert bucket.consume() is False

    def test_token_refill_over_time(self):
        """Test that tokens refill at configured rate."""
        bucket = TokenBucket(rate=10.0, burst=10)

        # Consume all tokens
        for _ in range(10):
            assert bucket.consume() is True

        # Should be empty now
        assert bucket.consume() is False

        # Wait 0.5 seconds (should refill ~5 tokens at 10/s)
        time.sleep(0.5)

        # Should be able to consume ~5 tokens
        consumed = 0
        for _ in range(10):
            if bucket.consume():
                consumed += 1
            else:
                break

        # Should have consumed around 5 tokens (allow ±2 for timing variance)
        assert 3 <= consumed <= 7

    def test_burst_capacity_limit(self):
        """Test that bucket doesn't exceed burst capacity."""
        bucket = TokenBucket(rate=100.0, burst=5)

        # Wait to ensure full refill
        time.sleep(0.2)

        # Should only be able to consume burst size
        for _ in range(5):
            assert bucket.consume() is True

        # 6th should fail (capacity is 5)
        assert bucket.consume() is False

    def test_thread_safety(self):
        """Test that token bucket is thread-safe."""
        import threading

        # Use low rate so refill during test is negligible (avoids flakiness)
        bucket = TokenBucket(rate=0.01, burst=50)
        results = []

        def consume_token():
            result = bucket.consume()
            results.append(result)

        # Create 100 threads trying to consume tokens
        threads = [threading.Thread(target=consume_token) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly 50 should succeed (burst capacity)
        assert sum(results) == 50


class TestRateLimiter:
    """Tests for RateLimiter."""

    def test_different_ips_have_separate_limits(self):
        """Test that each IP has its own token bucket."""
        limiter = RateLimiter(rate=10.0, burst=5)

        # Consume all tokens for IP 1
        for _ in range(5):
            assert limiter.check_rate_limit("192.168.1.1") is True
        assert limiter.check_rate_limit("192.168.1.1") is False

        # IP 2 should still have full capacity
        for _ in range(5):
            assert limiter.check_rate_limit("192.168.1.2") is True
        assert limiter.check_rate_limit("192.168.1.2") is False

    def test_rate_limit_allows_requests_under_limit(self):
        """Test that requests under limit are allowed."""
        limiter = RateLimiter(rate=100.0, burst=10)

        # First 10 requests should be allowed
        for _ in range(10):
            assert limiter.check_rate_limit("127.0.0.1") is True

    def test_rate_limit_blocks_requests_over_limit(self):
        """Test that requests over limit are blocked."""
        limiter = RateLimiter(rate=10.0, burst=5)

        # Consume all tokens
        for _ in range(5):
            assert limiter.check_rate_limit("127.0.0.1") is True

        # Next request should be rate limited
        assert limiter.check_rate_limit("127.0.0.1") is False

    def test_cleanup_removes_stale_buckets(self):
        """Test that cleanup removes inactive buckets."""
        limiter = RateLimiter(rate=10.0, burst=10)

        # Create buckets for 3 IPs
        limiter.check_rate_limit("192.168.1.1")
        limiter.check_rate_limit("192.168.1.2")
        limiter.check_rate_limit("192.168.1.3")

        assert len(limiter._buckets) == 3

        # Wait for tokens to refill to full capacity
        time.sleep(1.5)

        # Force cleanup by setting last_cleanup to trigger interval
        limiter._last_cleanup = time.monotonic() - limiter._cleanup_interval - 1
        limiter._maybe_cleanup()

        # All buckets should be cleaned up (full = stale)
        assert len(limiter._buckets) == 0

    def test_cleanup_concurrent_with_consume(self):
        """Cleanup must not race with consume() on bucket fields.

        Under free-threaded 3.14t, _maybe_cleanup used to read bucket fields
        without holding the bucket's lock. This exercise confirms the fix:
        one thread hammers consume() while another forces cleanup repeatedly,
        and the rate limiter stays consistent (no crash, bucket accounting
        stays within the burst bound).

        """
        import threading

        limiter = RateLimiter(rate=1000.0, burst=100)
        stop = threading.Event()
        errors: list[BaseException] = []

        def consumer(ip: str) -> None:
            try:
                while not stop.is_set():
                    limiter.check_rate_limit(ip)
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        def cleaner() -> None:
            try:
                while not stop.is_set():
                    limiter._last_cleanup = time.monotonic() - limiter._cleanup_interval - 1
                    limiter._maybe_cleanup()
            except BaseException as exc:  # pragma: no cover
                errors.append(exc)

        ips = [f"10.0.0.{i}" for i in range(20)]
        threads = [threading.Thread(target=consumer, args=(ip,)) for ip in ips]
        threads.append(threading.Thread(target=cleaner))
        for t in threads:
            t.start()
        time.sleep(0.3)
        stop.set()
        for t in threads:
            t.join(timeout=2.0)

        assert not errors, f"unexpected errors: {errors}"
        # Every surviving bucket's token count must still obey the burst cap.
        for bucket in limiter._buckets.values():
            assert 0.0 <= bucket._tokens <= bucket._capacity

    def test_cleanup_keeps_active_buckets(self):
        """Test that cleanup keeps recently used buckets."""
        limiter = RateLimiter(rate=10.0, burst=10)

        # Create bucket and consume tokens
        for _ in range(5):
            limiter.check_rate_limit("192.168.1.1")

        assert len(limiter._buckets) == 1

        # Force cleanup
        limiter._last_cleanup = time.monotonic() - limiter._cleanup_interval - 1
        limiter._maybe_cleanup()

        # Bucket should still exist (not full = active)
        assert len(limiter._buckets) == 1


class TestRateLimiterBounds:
    """Regression tests for issue #110 — bounded per-IP bucket map."""

    def test_unique_ip_flood_stays_bounded(self):
        """A flood of unique client IPs cannot grow the map past the cap."""
        cap = 50
        limiter = RateLimiter(rate=10.0, burst=5, max_tracked_ips=cap)

        # Far more distinct IPs than the cap (simulating an IPv6 source flood).
        for i in range(cap * 20):
            limiter.check_rate_limit(f"2001:db8::{i:x}")

        assert len(limiter._buckets) <= cap

    def test_sustained_flood_keeping_buckets_nonfull_stays_bounded(self):
        """Sustained traffic that keeps buckets non-full is still bounded.

        Each IP consumes enough tokens to keep its bucket below capacity, so
        the old full-only cleanup would never reap them. The hard cap must
        still hold.
        """
        cap = 25
        limiter = RateLimiter(rate=1.0, burst=3, max_tracked_ips=cap)

        for round_no in range(5):
            for i in range(cap * 10):
                ip = f"203.0.113.{round_no}.{i}"
                # Drain tokens so the bucket is non-full when cleanup runs.
                limiter.check_rate_limit(ip)
                limiter.check_rate_limit(ip)
                limiter.check_rate_limit(ip)
            assert len(limiter._buckets) <= cap

    def test_cap_evicts_least_recently_seen(self):
        """When the cap is hit, the oldest (LRU) bucket is evicted first."""
        limiter = RateLimiter(rate=10.0, burst=5, max_tracked_ips=2)

        limiter.check_rate_limit("10.0.0.1")
        limiter.check_rate_limit("10.0.0.2")
        # Touch .1 so .2 becomes the least-recently-seen.
        limiter.check_rate_limit("10.0.0.1")
        # Inserting a third IP must evict .2 (the LRU), not .1.
        limiter.check_rate_limit("10.0.0.3")

        assert len(limiter._buckets) == 2
        assert "10.0.0.1" in limiter._buckets
        assert "10.0.0.3" in limiter._buckets
        assert "10.0.0.2" not in limiter._buckets

    def test_active_client_still_rate_limited_under_flood(self):
        """A real client keeps a working bucket even while unique IPs flood in."""
        limiter = RateLimiter(rate=1.0, burst=3, max_tracked_ips=1000)
        victim = "198.51.100.42"

        # Active client exhausts its burst.
        assert limiter.check_rate_limit(victim) is True
        assert limiter.check_rate_limit(victim) is True
        assert limiter.check_rate_limit(victim) is True
        # Now over its limit.
        assert limiter.check_rate_limit(victim) is False

        # Its bucket survives because it was recently used (LRU keeps it warm),
        # and it stays rate limited.
        for i in range(500):
            limiter.check_rate_limit(f"2001:db8:1::{i:x}")
        assert limiter.check_rate_limit(victim) is False

    def test_idle_buckets_evicted_on_cleanup(self):
        """Buckets idle past the idle window are reaped even when non-full."""
        from pounce import _rate_limiter as rl

        limiter = RateLimiter(rate=1.0, burst=5)
        # Drain so the bucket is NOT full (full buckets were already reaped).
        for _ in range(5):
            limiter.check_rate_limit("192.0.2.1")
        assert len(limiter._buckets) == 1
        assert limiter.check_rate_limit("192.0.2.1") is False  # non-full

        bucket = limiter._buckets["192.0.2.1"]
        # Make the bucket look idle: push its last-refill far into the past.
        bucket._last_refill -= rl._IDLE_EVICTION_SECONDS + 10.0

        limiter._last_cleanup = time.monotonic() - limiter._cleanup_interval - 1
        limiter._maybe_cleanup()

        assert len(limiter._buckets) == 0

    def test_max_tracked_ips_validation(self):
        """max_tracked_ips must be >= 1."""
        with pytest.raises(ValueError, match="max_tracked_ips must be >= 1"):
            RateLimiter(rate=10.0, burst=5, max_tracked_ips=0)


class TestRateLimitConfiguration:
    """Tests for rate limit configuration."""

    def test_rate_limiting_disabled_by_default(self):
        """Test that rate limiting is disabled by default."""
        config = ServerConfig()
        assert config.rate_limit_enabled is False

    def test_rate_limiting_can_be_enabled(self):
        """Test that rate limiting can be enabled."""
        config = ServerConfig(rate_limit_enabled=True)
        assert config.rate_limit_enabled is True

    def test_default_rate_limit_values(self):
        """Test default rate limit configuration."""
        config = ServerConfig()
        assert config.rate_limit_requests_per_second == 100.0
        assert config.rate_limit_burst == 200

    def test_custom_rate_limit_values(self):
        """Test custom rate limit configuration."""
        config = ServerConfig(
            rate_limit_requests_per_second=50.0,
            rate_limit_burst=100,
        )
        assert config.rate_limit_requests_per_second == 50.0
        assert config.rate_limit_burst == 100

    def test_default_max_tracked_ips(self):
        """Default tracked-IP cap is set."""
        config = ServerConfig()
        assert config.rate_limit_max_tracked_ips == 100_000

    def test_custom_max_tracked_ips(self):
        """Tracked-IP cap is configurable."""
        config = ServerConfig(rate_limit_max_tracked_ips=500)
        assert config.rate_limit_max_tracked_ips == 500

    def test_max_tracked_ips_config_validation(self):
        """rate_limit_max_tracked_ips must be >= 1."""
        with pytest.raises(ValueError, match="rate_limit_max_tracked_ips must be >= 1"):
            ServerConfig(rate_limit_max_tracked_ips=0)

    def test_rate_limit_requests_per_second_validation(self):
        """Test that rate_limit_requests_per_second must be positive."""
        with pytest.raises(ValueError, match="rate_limit_requests_per_second must be > 0"):
            ServerConfig(rate_limit_requests_per_second=0.0)

        with pytest.raises(ValueError, match="rate_limit_requests_per_second must be > 0"):
            ServerConfig(rate_limit_requests_per_second=-10.0)

    def test_rate_limit_burst_validation(self):
        """Test that rate_limit_burst must be positive."""
        with pytest.raises(ValueError, match="rate_limit_burst must be > 0"):
            ServerConfig(rate_limit_burst=0)

        with pytest.raises(ValueError, match="rate_limit_burst must be > 0"):
            ServerConfig(rate_limit_burst=-5)


class TestRateLimitWrapper:
    """Tests for ASGI rate limit wrapper."""

    @pytest.mark.asyncio
    async def test_wrapper_allows_requests_under_limit(self):
        """Test that wrapper allows requests under rate limit."""
        # Mock app
        app_called = False

        async def mock_app(scope, receive, send):
            nonlocal app_called
            app_called = True
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"OK",
                }
            )

        # Create wrapper with high limits
        limiter = RateLimiter(rate=1000.0, burst=100)
        wrapped = create_rate_limit_wrapper(mock_app, limiter)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": ("127.0.0.1", 12345),
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await wrapped(scope, receive, send)

        # App should have been called
        assert app_called is True
        assert messages[0]["status"] == 200
        assert messages[1]["body"] == b"OK"

    @pytest.mark.asyncio
    async def test_wrapper_blocks_requests_over_limit(self):
        """Test that wrapper blocks requests over rate limit."""

        async def mock_app(scope, receive, send):
            # Should not be called
            raise AssertionError("App should not be called when rate limited")

        # Create wrapper with very low limits
        limiter = RateLimiter(rate=1.0, burst=1)
        wrapped = create_rate_limit_wrapper(mock_app, limiter)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": ("127.0.0.1", 12345),
            "headers": [],
        }

        # First request should succeed
        messages1 = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send1(message):
            messages1.append(message)

        # Consume the one token
        limiter.check_rate_limit("127.0.0.1")

        # Second request should be rate limited
        messages2 = []

        async def send2(message):
            messages2.append(message)

        await wrapped(scope, receive, send2)

        # Should return 429
        assert messages2[0]["type"] == "http.response.start"
        assert messages2[0]["status"] == 429
        assert messages2[1]["body"] == b"Too Many Requests"

        # Should have Retry-After header
        headers = dict(messages2[0]["headers"])
        assert headers[b"retry-after"] == b"1"

    @pytest.mark.asyncio
    async def test_wrapper_handles_missing_client_info(self):
        """Test that wrapper allows requests with no client info."""
        app_called = False

        async def mock_app(scope, receive, send):
            nonlocal app_called
            app_called = True
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"OK",
                }
            )

        limiter = RateLimiter(rate=10.0, burst=5)
        wrapped = create_rate_limit_wrapper(mock_app, limiter)

        # Scope without client info
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
        }

        messages = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            messages.append(message)

        await wrapped(scope, receive, send)

        # Should allow request (no client = no rate limit)
        assert app_called is True

    @pytest.mark.asyncio
    async def test_wrapper_only_rate_limits_http(self):
        """Test that wrapper only rate limits HTTP requests."""
        app_called = False

        async def mock_app(scope, receive, send):
            nonlocal app_called
            app_called = True

        limiter = RateLimiter(rate=1.0, burst=1)
        wrapped = create_rate_limit_wrapper(mock_app, limiter)

        # WebSocket scope
        scope = {
            "type": "websocket",
            "path": "/ws",
            "client": ("127.0.0.1", 12345),
        }

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            pass

        # Consume all tokens for this IP via HTTP
        limiter.check_rate_limit("127.0.0.1")

        # WebSocket should still be allowed
        await wrapped(scope, receive, send)
        assert app_called is True

    @pytest.mark.asyncio
    async def test_wrapper_rate_limits_per_ip(self):
        """Test that wrapper rate limits per client IP."""
        call_count = 0

        async def mock_app(scope, receive, send):
            nonlocal call_count
            call_count += 1
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"OK",
                }
            )

        limiter = RateLimiter(rate=10.0, burst=2)
        wrapped = create_rate_limit_wrapper(mock_app, limiter)

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        # Make 2 requests from IP1 (should succeed)
        for _ in range(2):
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/",
                "client": ("192.168.1.1", 12345),
                "headers": [],
            }
            await wrapped(scope, receive, send)

        assert call_count == 2

        # Make 2 requests from IP2 (should also succeed)
        for _ in range(2):
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/",
                "client": ("192.168.1.2", 12345),
                "headers": [],
            }
            await wrapped(scope, receive, send)

        assert call_count == 4

        # 3rd request from IP1 should be rate limited
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": ("192.168.1.1", 12345),
            "headers": [],
        }

        messages = []

        async def send_capture(message):
            messages.append(message)

        await wrapped(scope, receive, send_capture)

        # Should be rate limited (app not called)
        assert call_count == 4
        assert messages[0]["status"] == 429
