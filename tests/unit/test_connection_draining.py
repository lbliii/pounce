"""
Tests for enhanced connection draining and shutdown.

"""

import asyncio
import contextlib
import threading
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from pounce.config import ServerConfig
from pounce.worker import Worker, _make_asyncio_server_wakeup_idempotent


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

    @pytest.mark.issue(301)
    @pytest.mark.asyncio
    async def test_asyncio_server_wakeup_is_idempotent(self):
        """A late transport detach cannot wake a closed server twice."""
        loop = asyncio.get_running_loop()
        server = asyncio.Server(loop, [], lambda: None, None, 1, None)
        _make_asyncio_server_wakeup_idempotent(server)

        server_internal = cast("Any", server)
        wakeup = server_internal._wakeup
        wakeup()
        assert server_internal._waiters is None
        wakeup()

    @pytest.mark.asyncio
    async def test_rejects_connections_when_draining(self, config, mock_sock, shutdown_event):
        """Test that worker rejects new connections when draining."""

        async def simple_app(scope, receive, send):
            """Simple ASGI app."""
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
        reader.read = AsyncMock(
            side_effect=[
                b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n",
                b"",  # EOF
            ]
        )

        # Handle connection - should not immediately reject
        # Note: This will fail during HTTP parsing, but that's OK for this test
        # We're just checking it doesn't reject immediately due to draining
        with contextlib.suppress(Exception):
            await worker._handle_connection(reader, writer)

        # Verify it didn't send a 503 shutdown response
        if writer.write.called:
            response = writer.write.call_args[0][0]
            assert b"Server shutting down" not in response

    @pytest.mark.issue(301)
    @pytest.mark.asyncio
    async def test_connection_remains_active_until_writer_detaches(
        self, config, mock_sock, shutdown_event
    ):
        """Idle accounting must include asynchronous transport closure."""

        async def simple_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"OK"})

        worker = Worker(
            app=simple_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            max_connections=100,
            shutdown_event=shutdown_event,
        )
        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.read = AsyncMock(
            side_effect=[b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"]
        )
        writer = AsyncMock(spec=asyncio.StreamWriter)
        writer.get_extra_info.side_effect = lambda name, default=None: {
            "peername": ("127.0.0.1", 12345),
            "sockname": ("127.0.0.1", 8000),
        }.get(name, default)
        writer.write = Mock()
        writer.drain = AsyncMock()
        writer.close = Mock()
        close_started = asyncio.Event()
        release_close = asyncio.Event()

        async def wait_closed() -> None:
            close_started.set()
            await release_close.wait()

        writer.wait_closed = AsyncMock(side_effect=wait_closed)

        connection_task = asyncio.create_task(worker._handle_connection(reader, writer))
        await asyncio.wait_for(close_started.wait(), timeout=1.0)

        assert worker._active_connections == 1
        assert not worker.is_idle()

        release_close.set()
        await connection_task
        assert worker.is_idle()

    @pytest.mark.issue(301)
    @pytest.mark.asyncio
    async def test_capacity_rejection_closes_after_write_failure(
        self, config, mock_sock, shutdown_event
    ):
        """A reset while writing the capacity 503 must not leak its transport."""

        async def simple_app(scope, receive, send):
            pass

        worker = Worker(
            app=simple_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            max_connections=1,
            shutdown_event=shutdown_event,
        )
        worker._active_connections = 1
        reader = AsyncMock(spec=asyncio.StreamReader)
        writer = AsyncMock(spec=asyncio.StreamWriter)
        writer.write = Mock()
        writer.close = Mock()
        writer.wait_closed = AsyncMock()

        with patch(
            "pounce.worker.drain_with_timeout",
            new=AsyncMock(side_effect=OSError("client reset")),
        ):
            await worker._handle_connection(reader, writer)

        writer.close.assert_called_once_with()
        writer.wait_closed.assert_awaited_once_with()

    @pytest.mark.issue(301)
    @pytest.mark.asyncio
    async def test_full_shutdown_waits_for_server_without_aborting_clients(
        self, config, mock_sock, shutdown_event
    ):
        """The server drain does not cut off an in-flight accepted request."""

        async def lifecycle_app(scope, receive, send):
            pass

        calls: list[str] = []

        class RecordingServer:
            def close(self) -> None:
                calls.append("close")

            async def wait_closed(self) -> None:
                calls.append("wait_closed")

            def abort_clients(self) -> None:
                calls.append("abort_clients")

        worker = Worker(
            app=lifecycle_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            shutdown_event=shutdown_event,
        )
        worker._draining = True
        shutdown_event.set()

        with patch(
            "pounce.worker.asyncio.start_server",
            new=AsyncMock(return_value=RecordingServer()),
        ):
            await worker._serve()

        assert calls[:2] == ["close", "wait_closed"]
        assert "abort_clients" not in calls

    @pytest.mark.issue(301)
    @pytest.mark.asyncio
    async def test_accepted_task_is_retained_until_writer_detaches(
        self, config, mock_sock, shutdown_event
    ):
        """An accepted handler retains its writer through transport detach."""

        async def simple_app(scope, receive, send):
            pass

        async def handled_connection(self, reader, writer) -> None:
            pass

        worker = Worker(
            app=simple_app,
            config=config,
            sock=mock_sock,
            worker_id=1,
            shutdown_event=shutdown_event,
        )
        reader = AsyncMock(spec=asyncio.StreamReader)
        writer = AsyncMock(spec=asyncio.StreamWriter)
        writer.close = Mock()
        detach_started = asyncio.Event()
        release_detach = asyncio.Event()

        async def wait_closed() -> None:
            detach_started.set()
            await release_detach.wait()

        writer.wait_closed = AsyncMock(side_effect=wait_closed)

        with patch.object(Worker, "_handle_connection", new=handled_connection):
            worker._start_connection(reader, writer)
            await asyncio.wait_for(detach_started.wait(), timeout=1.0)

            assert len(worker._connection_tasks) == 1
            release_detach.set()
            await asyncio.gather(*worker._connection_tasks)
            await asyncio.sleep(0)

        assert worker._connection_tasks == set()
        writer.close.assert_called_once_with()
        writer.wait_closed.assert_awaited_once_with()

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

        # Drain should dispatch lifecycle actions in order
        with patch("pounce.supervisor.dispatch") as mock_dispatch:
            supervisor._drain()

            action_types = [call.args[0] for call in mock_dispatch.call_args_list]
            assert "SUPERVISOR_SHUTDOWN" in action_types
            assert "SUPERVISOR_ALL_STOPPED" in action_types
            assert action_types.index("SUPERVISOR_SHUTDOWN") < action_types.index(
                "SUPERVISOR_ALL_STOPPED"
            )

    def test_drain_force_stops_unresponsive_workers(self):
        """Test that _drain() force-stops workers that don't exit in time."""
        import multiprocessing

        from pounce.supervisor import Supervisor, _WorkerHandle

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
            warning_calls = list(mock_logger.warning.call_args_list)
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
