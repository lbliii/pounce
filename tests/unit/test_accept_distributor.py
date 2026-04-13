"""Tests for AcceptDistributor — connection accept and distribution."""

import queue
import socket
import ssl
import threading
from unittest.mock import MagicMock

import pytest

from pounce.accept_distributor import AcceptDistributor, is_shared_socket

# ---------------------------------------------------------------------------
# is_shared_socket
# ---------------------------------------------------------------------------


class TestIsSharedSocket:
    def test_single_socket_not_shared(self):
        s = MagicMock(spec=socket.socket)
        assert is_shared_socket([s]) is False

    def test_same_object_is_shared(self):
        s = MagicMock(spec=socket.socket)
        assert is_shared_socket([s, s, s]) is True

    def test_different_objects_not_shared(self):
        s1 = MagicMock(spec=socket.socket)
        s2 = MagicMock(spec=socket.socket)
        assert is_shared_socket([s1, s2]) is False

    def test_empty_list(self):
        assert is_shared_socket([]) is False


# ---------------------------------------------------------------------------
# AcceptDistributor — happy path
# ---------------------------------------------------------------------------


class TestAcceptDistributorHappyPath:
    """Accept a connection, apply TCP_NODELAY, enqueue it."""

    def test_accepted_connection_enqueued(self):
        """A single accepted connection lands in the queue."""
        shutdown = threading.Event()
        conn_queue: queue.Queue[tuple[socket.socket, object]] = queue.Queue()

        mock_conn = MagicMock(spec=socket.socket)
        mock_conn.family = socket.AF_INET

        mock_sock = MagicMock(spec=socket.socket)
        # First accept() returns a connection, second triggers shutdown
        call_count = 0

        def accept_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_conn, ("127.0.0.1", 5000))
            shutdown.set()
            raise TimeoutError

        mock_sock.accept = accept_side_effect

        dist = AcceptDistributor(mock_sock, conn_queue, shutdown_event=shutdown)
        dist.run()

        assert not conn_queue.empty()
        conn, addr = conn_queue.get_nowait()
        assert conn is mock_conn
        assert addr == ("127.0.0.1", 5000)

    def test_tcp_nodelay_set_for_inet(self):
        """TCP_NODELAY is set for AF_INET connections."""
        shutdown = threading.Event()
        conn_queue: queue.Queue = queue.Queue()

        mock_conn = MagicMock(spec=socket.socket)
        mock_conn.family = socket.AF_INET

        mock_sock = MagicMock(spec=socket.socket)
        call_count = 0

        def accept_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_conn, ("127.0.0.1", 5000))
            shutdown.set()
            raise TimeoutError

        mock_sock.accept = accept_side_effect

        dist = AcceptDistributor(mock_sock, conn_queue, shutdown_event=shutdown)
        dist.run()

        mock_conn.setsockopt.assert_called_once_with(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def test_tcp_nodelay_skipped_for_unix(self):
        """TCP_NODELAY is not set for AF_UNIX connections."""
        shutdown = threading.Event()
        conn_queue: queue.Queue = queue.Queue()

        mock_conn = MagicMock(spec=socket.socket)
        mock_conn.family = socket.AF_UNIX

        mock_sock = MagicMock(spec=socket.socket)
        call_count = 0

        def accept_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_conn, "")
            shutdown.set()
            raise TimeoutError

        mock_sock.accept = accept_side_effect

        dist = AcceptDistributor(mock_sock, conn_queue, shutdown_event=shutdown)
        dist.run()

        mock_conn.setsockopt.assert_not_called()


# ---------------------------------------------------------------------------
# AcceptDistributor — SSL handshake failure
# ---------------------------------------------------------------------------


class TestAcceptDistributorSSLFailure:
    """SSL handshake failure closes the connection and continues."""

    def test_ssl_error_closes_conn_and_continues(self):
        """SSLError during wrap_socket closes conn, loop continues."""
        shutdown = threading.Event()
        conn_queue: queue.Queue = queue.Queue()

        mock_conn_bad = MagicMock(spec=socket.socket)
        mock_conn_bad.family = socket.AF_INET

        mock_conn_good = MagicMock(spec=socket.socket)
        mock_conn_good.family = socket.AF_INET

        mock_ssl_ctx = MagicMock(spec=ssl.SSLContext)
        wrapped_conn = MagicMock(spec=ssl.SSLSocket)
        wrapped_conn.family = socket.AF_INET

        # First wrap fails, second succeeds
        mock_ssl_ctx.wrap_socket.side_effect = [
            ssl.SSLError("handshake failed"),
            wrapped_conn,
        ]

        mock_sock = MagicMock(spec=socket.socket)
        call_count = 0

        def accept_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (mock_conn_bad, ("1.2.3.4", 5000))
            if call_count == 2:
                return (mock_conn_good, ("5.6.7.8", 6000))
            shutdown.set()
            raise TimeoutError

        mock_sock.accept = accept_side_effect

        dist = AcceptDistributor(
            mock_sock, conn_queue, shutdown_event=shutdown, ssl_context=mock_ssl_ctx
        )
        dist.run()

        # Bad connection was closed
        mock_conn_bad.close.assert_called_once()

        # Good connection was enqueued (the wrapped version)
        assert conn_queue.qsize() == 1
        conn, addr = conn_queue.get_nowait()
        assert conn is wrapped_conn
        assert addr == ("5.6.7.8", 6000)


# ---------------------------------------------------------------------------
# AcceptDistributor — graceful shutdown
# ---------------------------------------------------------------------------


class TestAcceptDistributorShutdown:
    """Shutdown event stops the accept loop cleanly."""

    def test_shutdown_event_stops_loop(self):
        """Setting shutdown event causes run() to exit."""
        shutdown = threading.Event()
        conn_queue: queue.Queue = queue.Queue()

        mock_sock = MagicMock(spec=socket.socket)
        # Always timeout — shutdown event will stop the loop
        mock_sock.accept.side_effect = TimeoutError

        dist = AcceptDistributor(mock_sock, conn_queue, shutdown_event=shutdown)

        # Run in a thread, set shutdown after a brief delay
        def delayed_shutdown():
            shutdown.set()

        t = threading.Thread(target=dist.run)
        t.start()
        delayed_shutdown()
        t.join(timeout=2.0)

        assert not t.is_alive(), "AcceptDistributor did not stop after shutdown"
        assert conn_queue.empty()

    def test_oserror_during_shutdown_exits_cleanly(self):
        """OSError after shutdown event is set exits without raising."""
        shutdown = threading.Event()
        conn_queue: queue.Queue = queue.Queue()

        mock_sock = MagicMock(spec=socket.socket)

        def accept_raises_oserror():
            shutdown.set()
            raise OSError("socket closed")

        mock_sock.accept = accept_raises_oserror

        dist = AcceptDistributor(mock_sock, conn_queue, shutdown_event=shutdown)
        # Should not raise
        dist.run()
        assert conn_queue.empty()

    def test_oserror_without_shutdown_reraises(self):
        """OSError when shutdown is not set propagates the exception."""
        conn_queue: queue.Queue = queue.Queue()

        mock_sock = MagicMock(spec=socket.socket)
        mock_sock.accept.side_effect = OSError("unexpected error")

        dist = AcceptDistributor(mock_sock, conn_queue)

        with pytest.raises(OSError, match="unexpected error"):
            dist.run()
