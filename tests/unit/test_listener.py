"""Tests for pounce.net.listener — socket creation and binding."""

import socket

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
