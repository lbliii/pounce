"""
Tests for request queueing and load shedding.

"""

import asyncio

import pytest

from pounce._request_queue import QueueMetrics, RequestQueue, create_queue_wrapper
from pounce.config import ServerConfig


class TestRequestQueue:
    """Tests for RequestQueue."""

    @pytest.mark.asyncio
    async def test_acquire_release(self):
        """Test basic acquire/release."""
        queue = RequestQueue(max_depth=10)

        # Should be able to acquire
        assert await queue.acquire() is True
        assert queue.get_depth() == 1

        # Release
        queue.release()
        assert queue.get_depth() == 0

    @pytest.mark.asyncio
    async def test_max_depth_enforcement(self):
        """Test that max_depth is enforced."""
        queue = RequestQueue(max_depth=3)

        # Acquire 3 slots
        for _ in range(3):
            assert await queue.acquire() is True

        assert queue.get_depth() == 3

        # 4th should fail (queue full)
        assert await queue.acquire() is False
        assert queue.get_depth() == 3

        # Release one
        queue.release()
        assert queue.get_depth() == 2

        # Now should be able to acquire again
        assert await queue.acquire() is True
        assert queue.get_depth() == 3

    @pytest.mark.asyncio
    async def test_unlimited_queue(self):
        """Test that unlimited queue (max_depth=0) never fills."""
        queue = RequestQueue(max_depth=0)

        # Should be able to acquire many slots
        for _ in range(100):
            assert await queue.acquire() is True

        assert queue.get_depth() == 100

        # Release all
        for _ in range(100):
            queue.release()

        assert queue.get_depth() == 0

    @pytest.mark.asyncio
    async def test_concurrent_acquire(self):
        """Test concurrent acquire/release."""
        queue = RequestQueue(max_depth=5)

        async def acquire_and_release():
            acquired = await queue.acquire()
            if acquired:
                await asyncio.sleep(0.01)
                queue.release()
            return acquired

        # Try to acquire 10 slots concurrently (only 5 should succeed)
        results = await asyncio.gather(*[acquire_and_release() for _ in range(10)])

        # At least 5 should have succeeded (some may have succeeded after others released)
        assert sum(results) >= 5

        # Queue should be empty now
        assert queue.get_depth() == 0


class TestQueueMetrics:
    """Tests for QueueMetrics."""

    @pytest.mark.asyncio
    async def test_record_queued(self):
        """Test recording queued requests."""
        metrics = QueueMetrics()

        await metrics.record_queued(10.5)
        await metrics.record_queued(20.0)
        await metrics.record_queued(5.5)

        stats = metrics.get_stats()
        assert stats["total_queued"] == 3
        assert stats["avg_wait_time_ms"] == 12.0  # (10.5 + 20.0 + 5.5) / 3
        assert stats["max_wait_time_ms"] == 20.0

    @pytest.mark.asyncio
    async def test_record_rejected(self):
        """Test recording rejected requests."""
        metrics = QueueMetrics()

        await metrics.record_rejected()
        await metrics.record_rejected()

        stats = metrics.get_stats()
        assert stats["total_rejected"] == 2

    @pytest.mark.asyncio
    async def test_rejection_rate(self):
        """Test rejection rate calculation."""
        metrics = QueueMetrics()

        await metrics.record_queued(10.0)
        await metrics.record_queued(20.0)
        await metrics.record_rejected()

        stats = metrics.get_stats()
        assert stats["rejection_rate"] == pytest.approx(1.0 / 3.0)

    @pytest.mark.asyncio
    async def test_empty_stats(self):
        """Test stats when no requests recorded."""
        metrics = QueueMetrics()

        stats = metrics.get_stats()
        assert stats["total_queued"] == 0
        assert stats["total_rejected"] == 0
        assert stats["avg_wait_time_ms"] == 0.0
        assert stats["max_wait_time_ms"] == 0.0
        assert stats["rejection_rate"] == 0.0


class TestQueueConfiguration:
    """Tests for queue configuration."""

    def test_queue_disabled_by_default(self):
        """Test that queueing is disabled by default."""
        config = ServerConfig()
        assert config.request_queue_enabled is False

    def test_queue_can_be_enabled(self):
        """Test that queueing can be enabled."""
        config = ServerConfig(request_queue_enabled=True)
        assert config.request_queue_enabled is True

    def test_default_queue_depth(self):
        """Test default queue depth."""
        config = ServerConfig()
        assert config.request_queue_max_depth == 1000

    def test_custom_queue_depth(self):
        """Test custom queue depth."""
        config = ServerConfig(request_queue_max_depth=500)
        assert config.request_queue_max_depth == 500

    def test_unlimited_queue_depth(self):
        """Test unlimited queue depth (0)."""
        config = ServerConfig(request_queue_max_depth=0)
        assert config.request_queue_max_depth == 0

    def test_queue_depth_validation(self):
        """Test that queue_max_depth must be non-negative."""
        with pytest.raises(ValueError, match="request_queue_max_depth must be >= 0"):
            ServerConfig(request_queue_max_depth=-1)


class TestQueueWrapper:
    """Tests for ASGI queue wrapper."""

    @pytest.mark.asyncio
    async def test_wrapper_allows_requests_under_capacity(self):
        """Test that wrapper allows requests when queue has capacity."""
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

        queue = RequestQueue(max_depth=10)
        wrapped = create_queue_wrapper(mock_app, queue)

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

        # App should have been called
        assert app_called is True
        assert messages[0]["status"] == 200
        assert messages[1]["body"] == b"OK"

        # Queue should be empty after request completes
        assert queue.get_depth() == 0

    @pytest.mark.asyncio
    async def test_wrapper_rejects_when_queue_full(self):
        """Test that wrapper returns 503 when queue is full."""

        async def mock_app(scope, receive, send):
            # Should not be called
            raise AssertionError("App should not be called when queue is full")

        queue = RequestQueue(max_depth=1)
        metrics = QueueMetrics()
        wrapped = create_queue_wrapper(mock_app, queue, metrics)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        # Fill the queue
        await queue.acquire()

        # Try to make a request (should be rejected)
        messages = []

        async def send(message):
            messages.append(message)

        await wrapped(scope, receive, send)

        # Should return 503
        assert messages[0]["type"] == "http.response.start"
        assert messages[0]["status"] == 503
        assert messages[1]["body"] == b"Service Unavailable - Server Overloaded"

        # Should have Retry-After header
        headers = dict(messages[0]["headers"])
        assert headers[b"retry-after"] == b"5"

        # Metrics should record rejection
        stats = metrics.get_stats()
        assert stats["total_rejected"] == 1

    @pytest.mark.asyncio
    async def test_wrapper_releases_slot_on_success(self):
        """Test that wrapper releases queue slot after request."""

        async def mock_app(scope, receive, send):
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

        queue = RequestQueue(max_depth=10)
        wrapped = create_queue_wrapper(mock_app, queue)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        # Make request
        await wrapped(scope, receive, send)

        # Queue should be empty
        assert queue.get_depth() == 0

    @pytest.mark.asyncio
    async def test_wrapper_releases_slot_on_error(self):
        """Test that wrapper releases queue slot even on error."""

        async def mock_app(scope, receive, send):
            raise RuntimeError("App error")

        queue = RequestQueue(max_depth=10)
        wrapped = create_queue_wrapper(mock_app, queue)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        # Make request (should raise error)
        with pytest.raises(RuntimeError, match="App error"):
            await wrapped(scope, receive, send)

        # Queue should be empty (slot released despite error)
        assert queue.get_depth() == 0

    @pytest.mark.asyncio
    async def test_wrapper_only_queues_http(self):
        """Test that wrapper only queues HTTP requests."""
        app_called = False

        async def mock_app(scope, receive, send):
            nonlocal app_called
            app_called = True

        queue = RequestQueue(max_depth=1)
        wrapped = create_queue_wrapper(mock_app, queue)

        # WebSocket scope
        scope = {
            "type": "websocket",
            "path": "/ws",
        }

        async def receive():
            return {"type": "websocket.connect"}

        async def send(message):
            pass

        # Fill the queue (HTTP)
        await queue.acquire()

        # WebSocket should not be queued
        await wrapped(scope, receive, send)
        assert app_called is True

    @pytest.mark.asyncio
    async def test_wrapper_records_wait_time(self):
        """Test that wrapper records queue wait time."""

        async def mock_app(scope, receive, send):
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

        queue = RequestQueue(max_depth=10)
        metrics = QueueMetrics()
        wrapped = create_queue_wrapper(mock_app, queue, metrics)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        # Make request
        await wrapped(scope, receive, send)

        # Metrics should record the request
        stats = metrics.get_stats()
        assert stats["total_queued"] == 1
        assert stats["avg_wait_time_ms"] >= 0

    @pytest.mark.asyncio
    async def test_concurrent_requests(self):
        """Test concurrent request handling."""
        request_count = 0

        async def mock_app(scope, receive, send):
            nonlocal request_count
            request_count += 1
            await asyncio.sleep(0.05)  # Simulate work
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

        queue = RequestQueue(max_depth=5)
        metrics = QueueMetrics()
        wrapped = create_queue_wrapper(mock_app, queue, metrics)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        # Make 10 concurrent requests (queue depth 5, so 5 should be rejected)
        await asyncio.gather(
            *[wrapped(scope, receive, send) for _ in range(10)],
            return_exceptions=True,
        )

        # Some requests should have succeeded
        assert request_count >= 5

        # Queue should be empty now
        assert queue.get_depth() == 0

        # Check metrics
        stats = metrics.get_stats()
        assert stats["total_queued"] + stats["total_rejected"] == 10
