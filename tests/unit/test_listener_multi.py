"""Tests for pounce.net.listener — multi-socket creation for Phase 2."""

from __future__ import annotations

import socket

import pytest

from pounce.config import ServerConfig
from pounce.net.listener import create_listeners, has_so_reuseport


class TestCreateListeners:
    """create_listeners() returns the correct number of sockets."""

    def test_single_worker(self):
        config = ServerConfig(host="127.0.0.1", port=0)
        sockets = create_listeners(config, 1)
        try:
            assert len(sockets) == 1
            assert isinstance(sockets[0], socket.socket)
        finally:
            for s in set(sockets):
                s.close()

    def test_multiple_workers(self):
        config = ServerConfig(host="127.0.0.1", port=0)
        sockets = create_listeners(config, 4)
        try:
            assert len(sockets) == 4
            for s in sockets:
                assert isinstance(s, socket.socket)
                assert s.getblocking() is False
        finally:
            for s in set(sockets):
                s.close()

    def test_all_sockets_are_listening(self):
        config = ServerConfig(host="127.0.0.1", port=0)
        sockets = create_listeners(config, 2)
        try:
            for s in set(sockets):
                addr = s.getsockname()
                client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                client.connect(addr)
                client.close()
        finally:
            for s in set(sockets):
                s.close()

    def test_invalid_count_raises(self):
        config = ServerConfig(host="127.0.0.1", port=0)
        with pytest.raises(ValueError, match="count must be >= 1"):
            create_listeners(config, 0)

    def test_negative_count_raises(self):
        config = ServerConfig(host="127.0.0.1", port=0)
        with pytest.raises(ValueError, match="count must be >= 1"):
            create_listeners(config, -1)


class TestSocketStrategy:
    """create_listeners() picks the right strategy per platform."""

    def test_shared_socket_when_no_reuseport(self):
        """Without SO_REUSEPORT, all entries should be the same socket."""
        from unittest.mock import patch

        config = ServerConfig(host="127.0.0.1", port=0)
        with patch("pounce.net.listener.has_so_reuseport", return_value=False):
            sockets = create_listeners(config, 3)
        try:
            # All three should be the exact same object
            assert sockets[0] is sockets[1]
            assert sockets[1] is sockets[2]
        finally:
            sockets[0].close()

    def test_independent_sockets_when_reuseport(self):
        """With SO_REUSEPORT, each entry should be a distinct socket."""
        if not has_so_reuseport():
            pytest.skip("SO_REUSEPORT not available on this platform")

        config = ServerConfig(host="127.0.0.1", port=0)
        sockets = create_listeners(config, 2)
        try:
            assert sockets[0] is not sockets[1]
            assert sockets[0].fileno() != sockets[1].fileno()
        finally:
            for s in sockets:
                s.close()


class TestHasSoReuseport:
    """has_so_reuseport() detects platform capability."""

    def test_returns_bool(self):
        assert isinstance(has_so_reuseport(), bool)
