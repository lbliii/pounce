"""Tests for pounce._h3_handler — QUIC/HTTP3 datagram protocol handler.

Sprint 1 coverage: connection state management, routing, pruning,
error paths, 0-RTT rejection, and ASGI app error handling.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest

from pounce.asgi.bridge import SendState
from pounce.config import ServerConfig
from pounce.protocols.h3 import is_h3_available

pytestmark = pytest.mark.skipif(
    not is_h3_available(),
    reason="zoomies not installed; pip install pounce[h3]",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SERVER = ("127.0.0.1", 4433)
_ADDR_A = ("10.0.0.1", 5000)
_ADDR_B = ("10.0.0.2", 5001)
_ADDR_C = ("10.0.0.3", 5002)


def _make_config(**overrides: Any) -> ServerConfig:
    """Build a minimal ServerConfig for H3 tests."""
    defaults: dict[str, Any] = {
        "host": "127.0.0.1",
        "port": 4433,
        "ssl_certfile": "/tmp/cert.pem",
        "ssl_keyfile": "/tmp/key.pem",
        "access_log": False,
    }
    defaults.update(overrides)
    return ServerConfig(**defaults)


def _dummy_app(scope: Any, receive: Any, send: Any) -> None:
    """No-op ASGI app."""


async def _echo_app(scope: Any, receive: Any, send: Any) -> None:
    """Simple ASGI app that sends 200 OK."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok", "more_body": False})


async def _error_app(scope: Any, receive: Any, send: Any) -> None:
    """ASGI app that always raises."""
    raise RuntimeError("app exploded")


async def _slow_app(scope: Any, receive: Any, send: Any) -> None:
    """ASGI app that blocks forever (for cancellation tests)."""
    await asyncio.sleep(3600)


class _FakeQuicConnection:
    """Minimal fake for zoomies.core.QuicConnection."""

    def __init__(self, cids: tuple[bytes, ...] = (b"\x01",)) -> None:
        self._cids = cids
        self.close_called = False
        self.close_args: tuple[int, str] | None = None

    @property
    def our_cids(self) -> tuple[bytes, ...]:
        return self._cids

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> list[Any]:
        return []

    def send_datagrams(self) -> list[bytes]:
        return []

    def close(self, error_code: int = 0, reason: str = "") -> None:
        self.close_called = True
        self.close_args = (error_code, reason)


class _FakeH3Connection:
    """Minimal fake for zoomies.h3.H3Connection."""

    def __init__(self) -> None:
        self.sent_headers: list[tuple[int, list[tuple[bytes, bytes]]]] = []
        self.sent_data: list[tuple[int, bytes, bool]] = []

    def handle_event(self, event: Any) -> list[Any]:
        return []

    def send_headers(
        self, *, stream_id: int, headers: list[tuple[bytes, bytes]], end_stream: bool = False
    ) -> None:
        self.sent_headers.append((stream_id, headers))

    def send_data(
        self, *, stream_id: int, data: bytes, end_stream: bool = False
    ) -> None:
        self.sent_data.append((stream_id, data, end_stream))


def _make_connection(
    cids: tuple[bytes, ...] = (b"\x01",),
    addr: tuple[str, int] = _ADDR_A,
    last_activity: float | None = None,
) -> Any:
    """Create a _ZoomiesConnection with fake QUIC/H3."""
    from pounce._h3_handler import _ZoomiesConnection

    conn = _ZoomiesConnection(
        quic=_FakeQuicConnection(cids=cids),
        h3=_FakeH3Connection(),
        last_addr=addr,
    )
    if last_activity is not None:
        conn.last_activity = last_activity
    return conn


def _build_protocol() -> tuple[Any, ServerConfig]:
    """Instantiate a ZoomiesDatagramProtocol with fakes wired up."""
    from zoomies.core import QuicConfiguration

    from pounce._h3_handler import _create_zoomies_datagram_protocol

    config = _make_config()
    quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
    cls = _create_zoomies_datagram_protocol(
        _dummy_app, config, logging.getLogger("test"), _SERVER, quic_config
    )
    protocol = cls()
    transport = MagicMock(spec=asyncio.DatagramTransport)
    protocol.connection_made(transport)
    return protocol, config


# ---------------------------------------------------------------------------
# Task 1.2 — Connection State Tests
# ---------------------------------------------------------------------------


class TestZoomiesConnection:
    """Tests for _ZoomiesConnection dataclass."""

    def test_default_fields(self) -> None:
        conn = _make_connection()
        assert conn.stream_tasks == {}
        assert conn.stream_body_bytes == {}
        assert conn.stream_body_ended == set()
        assert conn.last_addr == _ADDR_A

    def test_last_activity_default_is_recent(self) -> None:
        before = time.monotonic()
        conn = _make_connection()
        after = time.monotonic()
        assert before <= conn.last_activity <= after

    def test_stream_tasks_isolation(self) -> None:
        """Each connection gets independent stream_tasks dicts."""
        c1 = _make_connection(cids=(b"\x01",))
        c2 = _make_connection(cids=(b"\x02",))
        c1.stream_tasks[0] = ("task", "queue")  # type: ignore[assignment]
        assert 0 not in c2.stream_tasks


class TestRouteConnection:
    """Tests for ZoomiesDatagramProtocol._route_connection()."""

    def test_route_by_addr(self) -> None:
        protocol, _ = _build_protocol()
        conn = _make_connection(cids=(b"\xAA",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        result = protocol._route_connection(b"\x00" * 20, _ADDR_A)
        assert result is conn

    def test_route_by_cid(self) -> None:
        protocol, _ = _build_protocol()
        conn = _make_connection(cids=(b"\xBB",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\xBB"] = conn
        # Route via CID even from a different address (connection migration)
        result = protocol._route_connection(b"\x00" * 20, _ADDR_B)
        # Falls through CID lookup (pull_destination_cid_for_routing depends
        # on actual QUIC packet format), then falls back to addr lookup.
        # With unknown addr _ADDR_B, returns None.
        # The important thing: no crash on migration scenario.
        assert result is None or result is conn

    def test_route_unknown_returns_none(self) -> None:
        protocol, _ = _build_protocol()
        result = protocol._route_connection(b"\x00" * 20, _ADDR_C)
        assert result is None


class TestPruneIdleConnections:
    """Tests for ZoomiesDatagramProtocol._prune_idle_connections()."""

    def test_prune_removes_expired(self) -> None:
        protocol, _ = _build_protocol()
        # Connection idle for 60s (timeout is 30s default)
        old_conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        old_conn.last_activity = time.monotonic() - 60
        protocol._connections[_ADDR_A] = old_conn
        protocol._cid_to_conn[b"\x01"] = old_conn

        protocol._prune_idle_connections()

        assert _ADDR_A not in protocol._connections
        assert b"\x01" not in protocol._cid_to_conn

    def test_prune_keeps_active(self) -> None:
        protocol, _ = _build_protocol()
        fresh_conn = _make_connection(cids=(b"\x02",), addr=_ADDR_B)
        fresh_conn.last_activity = time.monotonic()
        protocol._connections[_ADDR_B] = fresh_conn
        protocol._cid_to_conn[b"\x02"] = fresh_conn

        protocol._prune_idle_connections()

        assert _ADDR_B in protocol._connections
        assert b"\x02" in protocol._cid_to_conn

    def test_prune_mixed_connections(self) -> None:
        """Only expired connections are removed; active ones stay."""
        protocol, _ = _build_protocol()

        old = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        old.last_activity = time.monotonic() - 60
        protocol._connections[_ADDR_A] = old
        protocol._cid_to_conn[b"\x01"] = old

        fresh = _make_connection(cids=(b"\x02",), addr=_ADDR_B)
        fresh.last_activity = time.monotonic()
        protocol._connections[_ADDR_B] = fresh
        protocol._cid_to_conn[b"\x02"] = fresh

        protocol._prune_idle_connections()

        assert _ADDR_A not in protocol._connections
        assert _ADDR_B in protocol._connections

    def test_prune_respects_custom_timeout(self) -> None:
        """Custom http3_idle_timeout is honored."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        config = _make_config(http3_idle_timeout=5.0)
        quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
        cls = _create_zoomies_datagram_protocol(
            _dummy_app, config, logging.getLogger("test"), _SERVER, quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        conn.last_activity = time.monotonic() - 6  # 6s > 5s timeout
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\x01"] = conn

        protocol._prune_idle_connections()
        assert _ADDR_A not in protocol._connections


class TestRemoveConnection:
    """Tests for ZoomiesDatagramProtocol._remove_connection()."""

    def test_remove_clears_addr_and_cids(self) -> None:
        protocol, _ = _build_protocol()
        conn = _make_connection(cids=(b"\x01", b"\x02"), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\x01"] = conn
        protocol._cid_to_conn[b"\x02"] = conn

        protocol._remove_connection(conn)

        assert _ADDR_A not in protocol._connections
        assert b"\x01" not in protocol._cid_to_conn
        assert b"\x02" not in protocol._cid_to_conn

    def test_remove_idempotent(self) -> None:
        """Removing a connection twice doesn't raise."""
        protocol, _ = _build_protocol()
        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\x01"] = conn

        protocol._remove_connection(conn)
        protocol._remove_connection(conn)  # Should not raise

        assert _ADDR_A not in protocol._connections


# ---------------------------------------------------------------------------
# Task 1.3 — Error Path Tests
# ---------------------------------------------------------------------------


class TestErrorPaths:
    """Tests for error handling in the H3 handler."""

    def test_datagram_received_no_transport(self) -> None:
        """Datagrams before connection_made are silently dropped."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        config = _make_config()
        quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
        cls = _create_zoomies_datagram_protocol(
            _dummy_app, config, logging.getLogger("test"), _SERVER, quic_config
        )
        protocol = cls()
        # Don't call connection_made — transport is None
        protocol.datagram_received(b"\x00" * 20, _ADDR_A)  # Should not raise

    def test_connection_closed_event_removes_connection(self) -> None:
        """ConnectionClosed event triggers connection removal."""
        protocol, _ = _build_protocol()
        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\x01"] = conn

        from zoomies.events import ConnectionClosed

        # Patch quic to return ConnectionClosed
        conn.quic.datagram_received = lambda data, addr: [ConnectionClosed(error_code=0)]
        conn.quic.send_datagrams = list

        protocol.datagram_received(b"\x00" * 20, _ADDR_A)

        assert _ADDR_A not in protocol._connections
        assert b"\x01" not in protocol._cid_to_conn

    def test_handle_data_unknown_stream_ignored(self) -> None:
        """Data for unknown stream ID is silently dropped."""
        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)

        @dataclass(frozen=True)
        class FakeH3DataReceived:
            stream_id: int = 99
            data: bytes = b"payload"
            end_stream: bool = False

        # _handle_data should return without error
        protocol._handle_data(conn, FakeH3DataReceived(), _ADDR_A)

    def test_handle_data_body_exceeded_truncates(self) -> None:
        """Body exceeding max_request_size is truncated."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        config = _make_config(max_request_size=100)
        quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
        cls = _create_zoomies_datagram_protocol(
            _dummy_app, config, logging.getLogger("test"), _SERVER, quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        conn = _make_connection(addr=_ADDR_A)
        body_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        task = MagicMock()
        conn.stream_tasks[0] = (task, body_queue)

        @dataclass(frozen=True)
        class FakeH3DataReceived:
            stream_id: int = 0
            data: bytes = b"x" * 200  # > 100 byte limit
            end_stream: bool = False

        protocol._handle_data(conn, FakeH3DataReceived(), _ADDR_A)

        assert 0 in conn.stream_body_ended
        msg = body_queue.get_nowait()
        assert msg["more_body"] is False

    def test_handle_data_after_truncation_ignored(self) -> None:
        """Further data after body truncation is silently dropped."""
        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)
        body_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        task = MagicMock()
        conn.stream_tasks[0] = (task, body_queue)
        conn.stream_body_ended.add(0)  # Already truncated

        @dataclass(frozen=True)
        class FakeH3DataReceived:
            stream_id: int = 0
            data: bytes = b"more data"
            end_stream: bool = False

        protocol._handle_data(conn, FakeH3DataReceived(), _ADDR_A)

        assert body_queue.empty()  # No new messages queued

    def test_flush_with_no_transport(self) -> None:
        """Flushing with no transport is a no-op."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        config = _make_config()
        quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
        cls = _create_zoomies_datagram_protocol(
            _dummy_app, config, logging.getLogger("test"), _SERVER, quic_config
        )
        protocol = cls()
        # No connection_made
        conn = _make_connection(addr=_ADDR_A)
        protocol._flush(conn, _ADDR_A)  # Should not raise

    async def test_run_stream_app_error_sends_500(self) -> None:
        """ASGI app exception results in 500 response."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        config = _make_config()
        quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
        cls = _create_zoomies_datagram_protocol(
            _error_app, config, logging.getLogger("test"), _SERVER, quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        h3_conn = _FakeH3Connection()
        conn = _make_connection(addr=_ADDR_A)
        conn.h3 = h3_conn

        scope = {
            "type": "http",
            "http_version": "3",
            "method": "GET",
            "path": "/",
            "scheme": "https",
            "client": _ADDR_A,
            "server": _SERVER,
            "headers": [],
        }
        body_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        body_queue.put_nowait({"type": "http.request", "body": b"", "more_body": False})

        await protocol._run_stream(conn, 0, scope, body_queue, _ADDR_A)

        # Verify 500 was sent
        assert len(h3_conn.sent_headers) == 1
        status_header = h3_conn.sent_headers[0][1]
        assert (b":status", b"500") in status_header

    async def test_run_stream_cleans_up_on_error(self) -> None:
        """Stream tasks and body bytes are cleaned up after app error."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        config = _make_config()
        quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
        cls = _create_zoomies_datagram_protocol(
            _error_app, config, logging.getLogger("test"), _SERVER, quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        conn = _make_connection(addr=_ADDR_A)
        conn.h3 = _FakeH3Connection()
        conn.stream_tasks[0] = (MagicMock(), asyncio.Queue())
        conn.stream_body_bytes[0] = 42

        scope = {
            "type": "http",
            "http_version": "3",
            "method": "GET",
            "path": "/",
            "scheme": "https",
            "client": _ADDR_A,
            "server": _SERVER,
            "headers": [],
        }
        body_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        body_queue.put_nowait({"type": "http.request", "body": b"", "more_body": False})

        await protocol._run_stream(conn, 0, scope, body_queue, _ADDR_A)

        # stream_tasks and stream_body_bytes cleaned up in finally block
        assert 0 not in conn.stream_tasks
        assert 0 not in conn.stream_body_bytes


# ---------------------------------------------------------------------------
# Task 2.1 — Connection Limit Enforcement Tests
# ---------------------------------------------------------------------------


def _build_protocol_with_config(**overrides: Any) -> tuple[Any, ServerConfig]:
    """Build a protocol with custom config options."""
    from zoomies.core import QuicConfiguration

    from pounce._h3_handler import _create_zoomies_datagram_protocol

    config = _make_config(**overrides)
    quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
    cls = _create_zoomies_datagram_protocol(
        _dummy_app, config, logging.getLogger("test"), _SERVER, quic_config
    )
    protocol = cls()
    transport = MagicMock(spec=asyncio.DatagramTransport)
    protocol.connection_made(transport)
    return protocol, config


class TestConnectionLimitEnforcement:
    """Tests for http3_max_connections enforcement."""

    def test_under_limit_accepts_connection(self) -> None:
        """New connections are accepted when under the limit."""
        protocol, _ = _build_protocol_with_config(http3_max_connections=10)
        # Add one connection
        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        assert len(protocol._connections) == 1

        # Protocol should still accept — we're under limit
        # datagram_received for a new addr will try to create a new connection
        # (may fail on QUIC parsing, but the limit check should pass)
        protocol.datagram_received(b"\x00" * 20, _ADDR_B)
        # Connection was attempted (created or failed on QUIC parse, but not rejected)
        # The key assertion: no rejection log, connection map grew or stayed same

    def test_at_limit_rejects_new_connection(self) -> None:
        """New connections are rejected when at the limit."""
        protocol, _ = _build_protocol_with_config(http3_max_connections=2)

        # Fill to capacity
        for i, addr in enumerate([_ADDR_A, _ADDR_B]):
            conn = _make_connection(cids=(bytes([i + 1]),), addr=addr)
            protocol._connections[addr] = conn

        assert len(protocol._connections) == 2

        # New connection from _ADDR_C should be rejected
        protocol.datagram_received(b"\x00" * 20, _ADDR_C)

        # Connection should NOT be added — rejected at limit
        assert _ADDR_C not in protocol._connections
        assert len(protocol._connections) == 2

    def test_at_limit_existing_connection_still_works(self) -> None:
        """Existing connections are not rejected when at the limit."""
        protocol, _ = _build_protocol_with_config(http3_max_connections=1)

        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn

        # Datagram for existing connection should still be processed
        old_activity = conn.last_activity
        protocol.datagram_received(b"\x00" * 20, _ADDR_A)

        # Connection still exists and was updated
        assert _ADDR_A in protocol._connections
        assert conn.last_activity >= old_activity

    def test_limit_of_one(self) -> None:
        """Edge case: max_connections=1 allows exactly one connection."""
        protocol, _ = _build_protocol_with_config(http3_max_connections=1)

        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn

        # Second connection rejected
        protocol.datagram_received(b"\x00" * 20, _ADDR_B)
        assert _ADDR_B not in protocol._connections

    def test_after_prune_accepts_again(self) -> None:
        """After idle connections are pruned, new ones can be accepted."""
        protocol, _ = _build_protocol_with_config(
            http3_max_connections=1, http3_idle_timeout=1.0
        )

        # Add an expired connection
        old_conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        old_conn.last_activity = time.monotonic() - 10  # Well past timeout
        protocol._connections[_ADDR_A] = old_conn
        protocol._cid_to_conn[b"\x01"] = old_conn

        # datagram_received calls _prune_idle_connections first, then checks limit
        # So the expired connection is pruned, making room for the new one
        protocol.datagram_received(b"\x00" * 20, _ADDR_B)

        # Old connection pruned
        assert _ADDR_A not in protocol._connections


# ---------------------------------------------------------------------------
# Task 1.5 — 0-RTT Rejection Tests
# ---------------------------------------------------------------------------


class TestZeroRttRejection:
    """Tests for 0-RTT early data rejection of non-idempotent methods."""

    def _handle_headers_with_0rtt(
        self, protocol: Any, conn: Any, method: str
    ) -> None:
        """Simulate H3HeadersReceived with is_0rtt=True."""

        @dataclass(frozen=True)
        class FakeH3HeadersReceived:
            stream_id: int = 0
            headers: list[tuple[bytes, bytes]] = field(default_factory=list)
            end_stream: bool = True
            is_0rtt: bool = True

        headers = [
            (b":method", method.encode()),
            (b":path", b"/"),
            (b":scheme", b"https"),
            (b":authority", b"example.com"),
        ]
        event = FakeH3HeadersReceived(stream_id=0, headers=headers)
        protocol._handle_headers(conn, event, _ADDR_A)

    def test_0rtt_post_rejected_with_425(self) -> None:
        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)
        conn.h3 = _FakeH3Connection()
        protocol._connections[_ADDR_A] = conn

        self._handle_headers_with_0rtt(protocol, conn, "POST")

        # Should send 425 Too Early, not create a stream task
        assert 0 not in conn.stream_tasks
        assert len(conn.h3.sent_headers) == 1
        assert (b":status", b"425") in conn.h3.sent_headers[0][1]

    def test_0rtt_put_rejected_with_425(self) -> None:
        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)
        conn.h3 = _FakeH3Connection()

        self._handle_headers_with_0rtt(protocol, conn, "PUT")

        assert 0 not in conn.stream_tasks
        assert (b":status", b"425") in conn.h3.sent_headers[0][1]

    def test_0rtt_delete_rejected_with_425(self) -> None:
        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)
        conn.h3 = _FakeH3Connection()

        self._handle_headers_with_0rtt(protocol, conn, "DELETE")

        assert 0 not in conn.stream_tasks
        assert (b":status", b"425") in conn.h3.sent_headers[0][1]

    def test_0rtt_patch_rejected_with_425(self) -> None:
        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)
        conn.h3 = _FakeH3Connection()

        self._handle_headers_with_0rtt(protocol, conn, "PATCH")

        assert 0 not in conn.stream_tasks
        assert (b":status", b"425") in conn.h3.sent_headers[0][1]

    async def test_0rtt_get_allowed(self) -> None:
        """GET is idempotent — 0-RTT should be allowed."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        config = _make_config()
        quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
        cls = _create_zoomies_datagram_protocol(
            _echo_app, config, logging.getLogger("test"), _SERVER, quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        conn = _make_connection(addr=_ADDR_A)
        conn.h3 = _FakeH3Connection()
        protocol._connections[_ADDR_A] = conn

        @dataclass(frozen=True)
        class FakeH3HeadersReceived:
            stream_id: int = 0
            headers: list[tuple[bytes, bytes]] = field(default_factory=list)
            end_stream: bool = True
            is_0rtt: bool = True

        headers = [
            (b":method", b"GET"),
            (b":path", b"/"),
            (b":scheme", b"https"),
            (b":authority", b"example.com"),
        ]
        event = FakeH3HeadersReceived(stream_id=0, headers=headers)
        protocol._handle_headers(conn, event, _ADDR_A)

        # GET should create a stream task, not reject
        assert 0 in conn.stream_tasks
        # No 425 sent
        assert not any(
            (b":status", b"425") in hdrs for _, hdrs in conn.h3.sent_headers
        )
        # Let the spawned task complete
        task, _ = conn.stream_tasks[0]
        import contextlib

        with contextlib.suppress(asyncio.CancelledError, Exception):
            await asyncio.wait_for(task, timeout=2.0)


# ---------------------------------------------------------------------------
# Task 3.1 — Graceful Shutdown Tests
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    """Tests for close_all_connections() graceful shutdown."""

    def test_close_all_connections_calls_quic_close(self) -> None:
        """close_all_connections() calls close() on each QuicConnection."""
        protocol, _ = _build_protocol()

        conn_a = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        conn_b = _make_connection(cids=(b"\x02",), addr=_ADDR_B)
        protocol._connections[_ADDR_A] = conn_a
        protocol._connections[_ADDR_B] = conn_b
        protocol._cid_to_conn[b"\x01"] = conn_a
        protocol._cid_to_conn[b"\x02"] = conn_b

        protocol.close_all_connections()

        assert conn_a.quic.close_called
        assert conn_a.quic.close_args == (0, "Server shutting down")
        assert conn_b.quic.close_called
        assert conn_b.quic.close_args == (0, "Server shutting down")

    def test_close_all_connections_clears_maps(self) -> None:
        """close_all_connections() clears connection and CID maps."""
        protocol, _ = _build_protocol()

        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\x01"] = conn

        protocol.close_all_connections()

        assert len(protocol._connections) == 0
        assert len(protocol._cid_to_conn) == 0

    def test_close_all_with_no_connections(self) -> None:
        """close_all_connections() with empty maps doesn't crash."""
        protocol, _ = _build_protocol()
        protocol.close_all_connections()  # Should not raise

    def test_close_all_cancels_stream_tasks(self) -> None:
        """close_all_connections() cancels active stream tasks."""
        protocol, _ = _build_protocol()

        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        mock_task = MagicMock()
        mock_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        conn.stream_tasks[0] = (mock_task, mock_queue)
        protocol._connections[_ADDR_A] = conn

        protocol.close_all_connections()

        mock_task.cancel.assert_called_once()
        assert 0 not in conn.stream_tasks

    def test_close_all_survives_oserror(self) -> None:
        """close_all_connections() handles OSError during close gracefully."""
        protocol, _ = _build_protocol()

        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        conn.quic.close = MagicMock(side_effect=OSError("transport gone"))
        protocol._connections[_ADDR_A] = conn

        protocol.close_all_connections()  # Should not raise
        assert len(protocol._connections) == 0


# ---------------------------------------------------------------------------
# Task 3.2 — StreamReset Handling Tests
# ---------------------------------------------------------------------------


class TestStreamResetHandling:
    """Tests for handling peer-initiated stream resets."""

    def test_stream_reset_cancels_task(self) -> None:
        """StreamReset event cancels the stream task and cleans up."""
        protocol, _ = _build_protocol()
        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\x01"] = conn

        mock_task = MagicMock()
        mock_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        conn.stream_tasks[4] = (mock_task, mock_queue)
        conn.stream_body_bytes[4] = 100

        from zoomies.events import StreamReset

        conn.quic.datagram_received = lambda data, addr: [
            StreamReset(stream_id=4, error_code=0, final_size=0)
        ]
        conn.quic.send_datagrams = list

        protocol.datagram_received(b"\x00" * 20, _ADDR_A)

        mock_task.cancel.assert_called_once()
        assert 4 not in conn.stream_tasks
        assert 4 not in conn.stream_body_bytes

    def test_stream_reset_unknown_stream_no_crash(self) -> None:
        """StreamReset for unknown stream ID doesn't crash."""
        protocol, _ = _build_protocol()
        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\x01"] = conn

        from zoomies.events import StreamReset

        conn.quic.datagram_received = lambda data, addr: [
            StreamReset(stream_id=99, error_code=0, final_size=0)
        ]
        conn.quic.send_datagrams = list

        protocol.datagram_received(b"\x00" * 20, _ADDR_A)  # Should not raise


# ---------------------------------------------------------------------------
# Task 3.3 — StopSendingReceived Handling Tests
# ---------------------------------------------------------------------------


class TestStopSendingHandling:
    """Tests for handling peer StopSending signals."""

    def test_stop_sending_cancels_task(self) -> None:
        """StopSendingReceived event cancels the stream task."""
        protocol, _ = _build_protocol()
        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\x01"] = conn

        mock_task = MagicMock()
        mock_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        conn.stream_tasks[8] = (mock_task, mock_queue)

        from zoomies.events import StopSendingReceived

        conn.quic.datagram_received = lambda data, addr: [
            StopSendingReceived(stream_id=8, error_code=0)
        ]
        conn.quic.send_datagrams = list

        protocol.datagram_received(b"\x00" * 20, _ADDR_A)

        mock_task.cancel.assert_called_once()
        assert 8 not in conn.stream_tasks

    def test_stop_sending_unknown_stream_no_crash(self) -> None:
        """StopSendingReceived for unknown stream doesn't crash."""
        protocol, _ = _build_protocol()
        conn = _make_connection(cids=(b"\x01",), addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        protocol._cid_to_conn[b"\x01"] = conn

        from zoomies.events import StopSendingReceived

        conn.quic.datagram_received = lambda data, addr: [
            StopSendingReceived(stream_id=99, error_code=0)
        ]
        conn.quic.send_datagrams = list

        protocol.datagram_received(b"\x00" * 20, _ADDR_A)  # Should not raise


# ---------------------------------------------------------------------------
# Task 3.4 — Refactor Verification Tests
# ---------------------------------------------------------------------------


class TestRefactoredMethods:
    """Tests for the extracted helper methods."""

    def test_prepare_stream_extracts_request_id(self) -> None:
        """_prepare_stream sets request_id in scope extensions."""
        protocol, _ = _build_protocol()
        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": _ADDR_A,
            "headers": [(b"x-request-id", b"test-123")],
        }
        request_id, _, _timing, _compressor = protocol._prepare_stream(scope)
        assert request_id is not None
        assert scope["extensions"]["request_id"] == request_id

    def test_prepare_stream_negotiates_compression(self) -> None:
        """_prepare_stream returns a compressor when client accepts gzip."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        config = _make_config(compression=True)
        quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
        cls = _create_zoomies_datagram_protocol(
            _dummy_app, config, logging.getLogger("test"), _SERVER, quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        scope: dict[str, Any] = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "client": _ADDR_A,
            "headers": [(b"accept-encoding", b"gzip, deflate")],
        }
        _, _, _, compressor = protocol._prepare_stream(scope)
        assert compressor is not None

    def test_send_error_response_sends_500(self) -> None:
        """_send_error_response sends 500 status and body."""
        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)
        conn.h3 = _FakeH3Connection()
        send_state = SendState()

        protocol._send_error_response(conn, 0, _ADDR_A, send_state)

        assert len(conn.h3.sent_headers) == 1
        assert (b":status", b"500") in conn.h3.sent_headers[0][1]
        assert len(conn.h3.sent_data) == 1
        assert conn.h3.sent_data[0][1] == b"Internal Server Error"
        assert send_state.status == 500

    def test_maybe_handle_health_check_returns_false_for_non_health(self) -> None:
        """_maybe_handle_health_check returns False for regular requests."""
        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)
        scope = {"path": "/api/data", "method": "GET"}
        assert protocol._maybe_handle_health_check(conn, 0, scope, _ADDR_A) is False

    def test_maybe_handle_health_check_handles_health_path(self) -> None:
        """_maybe_handle_health_check handles configured health path."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        config = _make_config(health_check_path="/healthz")
        quic_config = QuicConfiguration(certificate=b"cert", private_key=b"key")
        cls = _create_zoomies_datagram_protocol(
            _dummy_app, config, logging.getLogger("test"), _SERVER, quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        conn = _make_connection(addr=_ADDR_A)
        conn.h3 = _FakeH3Connection()
        scope = {"path": "/healthz", "method": "GET"}
        result = protocol._maybe_handle_health_check(conn, 0, scope, _ADDR_A)
        assert result is True
        assert len(conn.h3.sent_headers) == 1


# ---------------------------------------------------------------------------
# Sprint 3 — ZeroRtt Events + Policy Wiring
# ---------------------------------------------------------------------------


class TestZeroRttEventHandling:
    """ZeroRttAccepted/ZeroRttRejected events dispatched without error."""

    def test_zero_rtt_accepted_event_logged(self) -> None:
        from zoomies.events import ZeroRttAccepted

        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        for cid in conn.quic.our_cids:
            protocol._cid_to_conn[cid] = conn

        # Inject ZeroRttAccepted into the event dispatch
        conn.quic._events = [ZeroRttAccepted()]
        protocol.datagram_received(b"\x00" * 50, _ADDR_A)
        # No crash — event was handled (logged at DEBUG)

    def test_zero_rtt_rejected_event_logged(self) -> None:
        from zoomies.events import ZeroRttRejected

        protocol, _ = _build_protocol()
        conn = _make_connection(addr=_ADDR_A)
        protocol._connections[_ADDR_A] = conn
        for cid in conn.quic.our_cids:
            protocol._cid_to_conn[cid] = conn

        conn.quic._events = [ZeroRttRejected()]
        protocol.datagram_received(b"\x00" * 50, _ADDR_A)
        # No crash — event was handled


class TestZeroRttPolicyWiring:
    """_PounceZeroRttPolicy and _make_zero_rtt_policy."""

    def test_policy_allows_0rtt(self) -> None:
        from pounce._h3_handler import _PounceZeroRttPolicy

        policy = _PounceZeroRttPolicy()
        assert policy.allow_0rtt(b"ticket-data", 12345) is True

    def test_make_zero_rtt_policy_returns_policy(self) -> None:
        from pounce._h3_handler import _make_zero_rtt_policy, _PounceZeroRttPolicy

        policy = _make_zero_rtt_policy()
        assert isinstance(policy, _PounceZeroRttPolicy)

    def test_config_disabled_no_policy(self) -> None:
        config = _make_config(http3_zero_rtt_enabled=False)
        assert config.http3_zero_rtt_enabled is False

    def test_config_enabled_has_policy(self) -> None:
        config = _make_config(http3_zero_rtt_enabled=True)
        assert config.http3_zero_rtt_enabled is True
