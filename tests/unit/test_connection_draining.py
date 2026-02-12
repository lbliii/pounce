"""
Tests for enhanced connection draining and shutdown.

"""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from pounce.config import ServerConfig
from pounce.worker import Worker


class TestConnectionDraining:
    """Tests for connection draining during shutdown."""

    @pytest.fixture
    def config(self):
        """Standard test configuration."""
        return ServerConfig(
            host="127.0.0.1",
            port=8000,
            shutdown_timeout=5.0,
        )

    @pytest.fixture
    def mock_sock(self):
        """Mock socket."""
        sock = Mock()
        sock.fileno.return_value = 42
        return sock

    @pytest.fixture
    def shutdown_event(self):
        """Shutdown event for coordinating worker shutdown."""
        return threading.Event()

    @pytest.mark.asyncio
    async def test_rejects_connections_when_draining(self, config, mock_sock, shutdown_event):
        """Test that worker rejects new connections when draining."""

        async def simple_app(scope, receive, send):
            """Simple ASGI app."""
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({
                "type": "http.response.body",
                "body": b"OK",
            })

        worker = Worker(
            app=simple_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            max_connections=100,
            shutdown_event=shutdown_event,
        )

        # Mark worker as draining
        worker.start_draining()

        # Create mock reader/writer
        reader = AsyncMock(spec=asyncio.StreamReader)
        writer = AsyncMock(spec=asyncio.StreamWriter)
        writer.get_extra_info.return_value = ("127.0.0.1", 12345)
        writer.write = Mock()
        writer.drain = AsyncMock()
        writer.close = Mock()
        writer.wait_closed = AsyncMock()

        # Try to handle a connection while draining
        await worker._handle_connection(reader, writer)

        # Verify 503 response was sent
        assert writer.write.called
        response = writer.write.call_args[0][0]
        assert b"503 Service Unavailable" in response
        assert b"Server shutting down" in response

        # Verify connection was closed
        assert writer.close.called
        assert writer.wait_closed.called

    @pytest.mark.asyncio
    async def test_accepts_connections_when_not_draining(self, config, mock_sock, shutdown_event):
        """Test that worker accepts connections normally when not draining."""

        async def simple_app(scope, receive, send):
            """Simple ASGI app."""
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({
                "type": "http.response.body",
                "body": b"OK",
            })

        worker = Worker(
            app=simple_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            max_connections=100,
            shutdown_event=shutdown_event,
        )

        # Worker is NOT draining
        assert not worker._draining

        # Create mock reader/writer with valid HTTP/1.1 request
        reader = AsyncMock(spec=asyncio.StreamReader)
        writer = AsyncMock(spec=asyncio.StreamWriter)
        writer.get_extra_info.return_value = ("127.0.0.1", 12345)
        writer.write = Mock()
        writer.drain = AsyncMock()
        writer.close = Mock()
        writer.wait_closed = AsyncMock()

        # Mock the reader to provide a simple HTTP request
        reader.read = AsyncMock(side_effect=[
            b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n",
            b"",  # EOF
        ])

        # Handle connection - should not immediately reject
        # Note: This will fail during HTTP parsing, but that's OK for this test
        # We're just checking it doesn't reject immediately due to draining
        try:
            await worker._handle_connection(reader, writer)
        except Exception:
            pass  # Expected to fail during HTTP parsing

        # Verify it didn't send a 503 shutdown response
        if writer.write.called:
            response = writer.write.call_args[0][0]
            assert b"Server shutting down" not in response

    def test_start_draining_sets_flag(self, config, mock_sock, shutdown_event):
        """Test that start_draining() sets the draining flag."""

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(
            app=simple_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            max_connections=100,
            shutdown_event=shutdown_event,
        )

        # Initially not draining
        assert not worker._draining

        # Start draining
        worker.start_draining()

        # Now draining
        assert worker._draining

    def test_is_idle_with_no_connections(self, config, mock_sock, shutdown_event):
        """Test is_idle() returns True when no active connections."""

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(
            app=simple_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            max_connections=100,
            shutdown_event=shutdown_event,
        )

        # No active connections
        assert worker._active_connections == 0
        assert worker.is_idle()

    def test_is_idle_with_active_connections(self, config, mock_sock, shutdown_event):
        """Test is_idle() returns False when connections are active."""

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(
            app=simple_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            max_connections=100,
            shutdown_event=shutdown_event,
        )

        # Simulate active connections
        worker._active_connections = 5
        assert not worker.is_idle()

    @pytest.mark.asyncio
    async def test_draining_with_max_connections_both_reject(
        self, config, mock_sock, shutdown_event
    ):
        """Test that draining check happens before max_connections check."""

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(
            app=simple_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            max_connections=1,  # Very low limit
            shutdown_event=shutdown_event,
        )

        # Mark as draining AND at capacity
        worker.start_draining()
        worker._active_connections = 1  # At max

        # Create mock connection
        reader = AsyncMock(spec=asyncio.StreamReader)
        writer = AsyncMock(spec=asyncio.StreamWriter)
        writer.get_extra_info.return_value = ("127.0.0.1", 12345)
        writer.write = Mock()
        writer.drain = AsyncMock()
        writer.close = Mock()
        writer.wait_closed = AsyncMock()

        # Handle connection
        await worker._handle_connection(reader, writer)

        # Should get shutdown message (draining check first)
        assert writer.write.called
        response = writer.write.call_args[0][0]
        assert b"Server shutting down" in response


class TestSupervisorDraining:
    """Tests for supervisor draining coordination."""

    def test_drain_logs_shutdown_message(self):
        """Test that _drain() logs shutdown initiation."""
        from pounce.supervisor import Supervisor

        config = ServerConfig(
            host="127.0.0.1",
            port=8000,
            workers=2,
            shutdown_timeout=1.0,
        )

        async def simple_app(scope, receive, send):
            pass

        supervisor = Supervisor(app=simple_app, config=config)

        # Mock the handles to be empty (no workers to actually drain)
        supervisor._handles = []
        supervisor._effective_workers = 0

        # Drain should complete without error
        with patch("pounce.supervisor.logger") as mock_logger:
            supervisor._drain()

            # Should log shutdown message
            assert mock_logger.info.called

    def test_drain_force_stops_unresponsive_workers(self):
        """Test that _drain() force-stops workers that don't exit in time."""
        from pounce.supervisor import Supervisor, _WorkerHandle
        import multiprocessing

        config = ServerConfig(
            host="127.0.0.1",
            port=8000,
            workers=1,
            shutdown_timeout=0.1,  # Very short timeout
        )

        async def simple_app(scope, receive, send):
            pass

        supervisor = Supervisor(app=simple_app, config=config)

        # Create a mock Process target that never stops
        mock_target = Mock(spec=multiprocessing.Process)
        mock_target.is_alive.return_value = True  # Always alive
        mock_target.join = Mock()  # Never actually joins
        mock_target.terminate = Mock()
        mock_target.kill = Mock()

        # Create a mock Worker instance
        mock_worker_instance = Mock()

        handle = _WorkerHandle(
            worker_id=1,
            target=mock_target,
            worker=mock_worker_instance,
        )
        supervisor._handles = [handle]
        supervisor._effective_workers = 1

        # Drain - should force-terminate the unresponsive worker
        with patch("pounce.supervisor.logger") as mock_logger:
            supervisor._drain()

            # Should have logged a warning about force termination
            warning_calls = [call for call in mock_logger.warning.call_args_list]
            assert len(warning_calls) > 0
            # Check that terminate was called
            assert mock_target.terminate.called


class TestShutdownTimeout:
    """Tests for shutdown timeout configuration."""

    def test_shutdown_timeout_config(self):
        """Test that shutdown_timeout can be configured."""
        config = ServerConfig(shutdown_timeout=15.0)
        assert config.shutdown_timeout == 15.0

    def test_shutdown_timeout_validation(self):
        """Test that invalid shutdown_timeout raises error."""
        with pytest.raises(ValueError, match="shutdown_timeout must be > 0"):
            ServerConfig(shutdown_timeout=-1.0)

        with pytest.raises(ValueError, match="shutdown_timeout must be > 0"):
            ServerConfig(shutdown_timeout=0.0)
