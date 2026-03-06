"""Tests for pounce.net.listener — socket creation and binding."""

import errno
import socket

import pytest

from pounce.config import ServerConfig
from pounce.net.listener import create_listener


class TestCreateListener:
    """create_listener() returns a configured, bound socket."""

    def test_returns_socket(self):
        config = ServerConfig(host="127.0.0.1", port=0)  # Port 0 = ephemeral
        sock = create_listener(config)
        try:
            assert isinstance(sock, socket.socket)
        finally:
            sock.close()

    def test_is_listening(self):
        config = ServerConfig(host="127.0.0.1", port=0)
        sock = create_listener(config)
        try:
            # Should be able to connect to it
            addr = sock.getsockname()
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(addr)
            client.close()
        finally:
            sock.close()

    def test_is_non_blocking(self):
        config = ServerConfig(host="127.0.0.1", port=0)
        sock = create_listener(config)
        try:
            assert sock.getblocking() is False
        finally:
            sock.close()

    def test_reuseaddr_set(self):
        config = ServerConfig(host="127.0.0.1", port=0)
        sock = create_listener(config)
        try:
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0
        finally:
            sock.close()

    def test_ephemeral_port(self):
        config = ServerConfig(host="127.0.0.1", port=0)
        sock = create_listener(config)
        try:
            _, port = sock.getsockname()
            assert port > 0
        finally:
            sock.close()

    def test_duplicate_bind_fails_single_worker(self):
        """Single-worker dev: second instance fails with EADDRINUSE (no SO_REUSEPORT)."""
        config = ServerConfig(host="127.0.0.1", port=0)
        sock1 = create_listener(config)
        try:
            _, port = sock1.getsockname()
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock2:
                sock2.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                with pytest.raises(OSError, match=r".*already in use.*") as exc_info:
                    sock2.bind(("127.0.0.1", port))
                assert (
                    "already in use" in str(exc_info.value).lower()
                    or getattr(exc_info.value, "errno", None) == errno.EADDRINUSE
                )
        finally:
            sock1.close()
