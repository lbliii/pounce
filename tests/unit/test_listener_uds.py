"""Tests for Unix domain socket support in pounce.net.listener."""

import os
import socket as _socket_mod
from unittest.mock import MagicMock, patch

import pytest

from pounce.config import ServerConfig
from pounce.net.listener import cleanup_unix_socket, create_listener


class TestCreateListenerRouting:
    """create_listener() routes to _bind_unix_socket when uds is set."""

    @patch("pounce.net.listener._bind_unix_socket")
    def test_routes_to_unix_when_uds_set(self, mock_unix):
        """create_listener() calls _bind_unix_socket when config.uds is set."""
        mock_unix.return_value = MagicMock()
        config = ServerConfig(uds="/run/pounce.sock")
        create_listener(config)
        mock_unix.assert_called_once_with(config)

    @patch("pounce.net.listener._bind_socket")
    def test_routes_to_tcp_when_no_uds(self, mock_tcp):
        """create_listener() calls _bind_socket when config.uds is None."""
        mock_tcp.return_value = MagicMock()
        config = ServerConfig()
        create_listener(config)
        mock_tcp.assert_called_once_with(config)


class TestBindUnixSocket:
    """_bind_unix_socket() creates and configures a Unix domain socket."""

    @patch("pounce.net.listener.os.chmod")
    @patch("pounce.net.listener.os.unlink")
    @patch("pounce.net.listener.socket.socket")
    def test_binds_and_listens(self, mock_sock_cls, mock_unlink, mock_chmod):
        """Socket is created, bound, and set to listen."""
        from pounce.net.listener import _bind_unix_socket

        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock

        config = ServerConfig(uds="/run/pounce.sock")
        result = _bind_unix_socket(config)

        mock_sock_cls.assert_called_once_with(_socket_mod.AF_UNIX, _socket_mod.SOCK_STREAM)
        mock_sock.bind.assert_called_once_with("/run/pounce.sock")
        mock_sock.listen.assert_called_once_with(config.backlog)
        mock_sock.setblocking.assert_called_once_with(False)
        mock_chmod.assert_called_once_with("/run/pounce.sock", 0o660)
        assert result is mock_sock

    @patch("pounce.net.listener.os.chmod")
    @patch("pounce.net.listener.os.unlink")
    @patch("pounce.net.listener.socket.socket")
    def test_removes_stale_socket(self, mock_sock_cls, mock_unlink, mock_chmod):
        """Stale socket file is removed before binding."""
        from pounce.net.listener import _bind_unix_socket

        mock_sock_cls.return_value = MagicMock()
        config = ServerConfig(uds="/run/old.sock")
        _bind_unix_socket(config)

        mock_unlink.assert_called_once_with("/run/old.sock")

    @patch("pounce.net.listener.os.chmod")
    @patch("pounce.net.listener.os.unlink", side_effect=FileNotFoundError)
    @patch("pounce.net.listener.socket.socket")
    def test_handles_no_stale_socket(self, mock_sock_cls, mock_unlink, mock_chmod):
        """Missing stale socket file is handled gracefully."""
        from pounce.net.listener import _bind_unix_socket

        mock_sock_cls.return_value = MagicMock()
        config = ServerConfig(uds="/run/fresh.sock")
        _bind_unix_socket(config)  # Should not raise

    @patch("pounce.net.listener.os.unlink")
    @patch("pounce.net.listener.socket.socket")
    def test_closes_socket_on_bind_failure(self, mock_sock_cls, mock_unlink):
        """Socket is closed if bind() fails."""
        from pounce.net.listener import _bind_unix_socket

        mock_sock = MagicMock()
        mock_sock.bind.side_effect = OSError("bind failed")
        mock_sock_cls.return_value = mock_sock

        config = ServerConfig(uds="/run/fail.sock")
        with pytest.raises(OSError, match="bind failed"):
            _bind_unix_socket(config)

        mock_sock.close.assert_called_once()


class TestCleanupUnixSocket:
    """cleanup_unix_socket() removes the socket file on shutdown."""

    def test_removes_socket_file(self, tmp_path):
        sock_path = str(tmp_path / "test.sock")
        # Create a file to remove
        with open(sock_path, "w") as f:
            f.write("socket")

        config = ServerConfig(uds=sock_path)
        assert os.path.exists(sock_path)
        cleanup_unix_socket(config)
        assert not os.path.exists(sock_path)

    def test_noop_when_no_uds(self):
        """cleanup_unix_socket() is safe to call with no UDS configured."""
        config = ServerConfig()
        cleanup_unix_socket(config)  # Should not raise

    def test_noop_when_file_already_gone(self, tmp_path):
        """cleanup_unix_socket() handles missing file gracefully."""
        config = ServerConfig(uds=str(tmp_path / "nonexistent.sock"))
        cleanup_unix_socket(config)  # Should not raise
