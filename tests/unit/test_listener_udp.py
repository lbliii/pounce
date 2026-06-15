"""Coverage for the UDP/HTTP3 listener paths and TCP error mapping.

The TCP listener already has a test suite; these tests exercise the UDP
(``create_udp_listener`` / ``create_udp_listeners`` / ``_bind_udp_socket``)
counterparts plus the EADDRINUSE error-message mapping that turns a raw
``OSError`` into an actionable hint. All sockets bind to an ephemeral port
(``port=0``) so the tests are hermetic.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from pounce.config import ServerConfig
from pounce.net.listener import (
    create_udp_listener,
    create_udp_listeners,
    has_so_reuseport,
)


def _ephemeral_config(**kw: object) -> ServerConfig:
    return ServerConfig(host="127.0.0.1", port=0, **kw)


class TestCreateUdpListener:
    def test_returns_bound_dgram_socket(self) -> None:
        sock = create_udp_listener(_ephemeral_config())
        try:
            assert sock.type & socket.SOCK_DGRAM
            # A bound ephemeral socket has a non-zero assigned port.
            assert sock.getsockname()[1] != 0
        finally:
            sock.close()

    def test_socket_is_non_blocking(self) -> None:
        sock = create_udp_listener(_ephemeral_config())
        try:
            assert sock.getblocking() is False
        finally:
            sock.close()

    def test_reuseaddr_is_set(self) -> None:
        sock = create_udp_listener(_ephemeral_config())
        try:
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0
        finally:
            sock.close()


class TestCreateUdpListeners:
    def test_single_worker_returns_one_socket(self) -> None:
        socks = create_udp_listeners(_ephemeral_config(), 1)
        try:
            assert len(socks) == 1
            assert socks[0].type & socket.SOCK_DGRAM
        finally:
            for s in set(socks):
                s.close()

    def test_zero_count_raises(self) -> None:
        with pytest.raises(ValueError, match="count must be >= 1"):
            create_udp_listeners(_ephemeral_config(), 0)

    def test_negative_count_raises(self) -> None:
        with pytest.raises(ValueError, match="count must be >= 1"):
            create_udp_listeners(_ephemeral_config(), -3)

    def test_multiple_workers_with_reuseport(self) -> None:
        if not has_so_reuseport():
            pytest.skip("SO_REUSEPORT not available on this platform")
        # port=0 + SO_REUSEPORT: each socket binds independently to its own
        # ephemeral port; we only assert count and family here.
        socks = create_udp_listeners(_ephemeral_config(), 3)
        try:
            assert len(socks) == 3
            assert all(s.type & socket.SOCK_DGRAM for s in socks)
        finally:
            for s in set(socks):
                s.close()

    def test_multiple_workers_shared_fallback_without_reuseport(self) -> None:
        # Force the no-SO_REUSEPORT branch: a single shared socket is returned
        # for every worker (same fd repeated).
        with patch("pounce.net.listener.has_so_reuseport", return_value=False):
            socks = create_udp_listeners(_ephemeral_config(), 4)
        try:
            assert len(socks) == 4
            assert len({id(s) for s in socks}) == 1  # all the same fd
        finally:
            socks[0].close()


class TestUdpReuseportCleanupOnFailure:
    def test_partial_failure_closes_already_bound_sockets(self) -> None:
        if not has_so_reuseport():
            pytest.skip("SO_REUSEPORT not available on this platform")

        created: list[socket.socket] = []

        import pounce.net.listener as mod

        real_bind = mod._bind_udp_socket
        calls = {"n": 0}

        def flaky_bind(config: ServerConfig, **kw: object) -> socket.socket:
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("boom on second bind")
            sock = real_bind(config, **kw)  # type: ignore[arg-type]
            created.append(sock)
            return sock

        with (
            patch.object(mod, "_bind_udp_socket", side_effect=flaky_bind),
            pytest.raises(OSError, match="boom on second bind"),
        ):
            create_udp_listeners(_ephemeral_config(), 3)

        # The first successfully-bound socket must have been closed during cleanup.
        assert created, "expected at least one socket to have been created"
        assert created[0].fileno() == -1  # closed -> fd released


class TestHasSoReuseport:
    def test_returns_bool(self) -> None:
        assert isinstance(has_so_reuseport(), bool)


class TestTcpErrorMapping:
    """``_bind_socket`` rewrites raw OSErrors into actionable hints."""

    def test_address_in_use_gets_actionable_hint(self) -> None:
        import errno

        from pounce.net.listener import _bind_socket

        class FakeSock:
            def setsockopt(self, *a: object) -> None:
                return None

            def setblocking(self, *a: object) -> None:
                return None

            def bind(self, *a: object) -> None:
                raise OSError(errno.EADDRINUSE, "Address already in use")

            def listen(self, *a: object) -> None:
                return None

            def close(self) -> None:
                return None

            def getsockname(self) -> tuple[str, int]:
                return ("127.0.0.1", 0)

        with (
            patch("socket.socket", return_value=FakeSock()),
            pytest.raises(OSError, match="already in use"),
        ):
            _bind_socket(ServerConfig(host="127.0.0.1", port=12345))

    def test_permission_denied_gets_actionable_hint(self) -> None:
        import errno

        from pounce.net.listener import _bind_socket

        class FakeSock:
            def setsockopt(self, *a: object) -> None:
                return None

            def setblocking(self, *a: object) -> None:
                return None

            def bind(self, *a: object) -> None:
                raise OSError(errno.EACCES, "Permission denied")

            def listen(self, *a: object) -> None:
                return None

            def close(self) -> None:
                return None

        with (
            patch("socket.socket", return_value=FakeSock()),
            pytest.raises(OSError, match="Permission denied binding"),
        ):
            _bind_socket(ServerConfig(host="127.0.0.1", port=80))

    def test_unmapped_oserror_propagates(self) -> None:
        import errno

        from pounce.net.listener import _bind_socket

        class FakeSock:
            def setsockopt(self, *a: object) -> None:
                return None

            def setblocking(self, *a: object) -> None:
                return None

            def bind(self, *a: object) -> None:
                raise OSError(errno.ENOMEM, "Out of memory")

            def listen(self, *a: object) -> None:
                return None

            def close(self) -> None:
                return None

        with (
            patch("socket.socket", return_value=FakeSock()),
            pytest.raises(OSError, match="Out of memory"),
        ):
            _bind_socket(ServerConfig(host="127.0.0.1", port=12346))
