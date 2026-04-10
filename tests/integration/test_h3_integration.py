"""Integration tests for HTTP/3 — full protocol stack, real certs, real sockets.

Tests fall into two categories:

1. **Real QUIC handshake tests** (Sprint 1, zoomies 0.3.1 client mode):
   Sans-I/O loopback between a QUIC client and Pounce's ZoomiesDatagramProtocol,
   plus real-socket H3Worker tests with actual QUIC clients.

2. **Resilience tests** (Sprint 4, retained):
   Garbage-datagram and connection-limit tests validating error paths.
"""

import asyncio
import datetime
import logging
import socket
import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from pounce.config import ServerConfig
from pounce.h3_worker import H3Worker
from pounce.protocols.h3 import is_h3_available

pytestmark = pytest.mark.skipif(
    not is_h3_available(),
    reason="zoomies not installed; pip install pounce[h3]",
)


# ---------------------------------------------------------------------------
# TLS cert generation helper
# ---------------------------------------------------------------------------


def _generate_test_certs() -> tuple[bytes, bytes]:
    """Generate ephemeral self-signed TLS cert + key for tests."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    return cert_pem, key_pem


def _make_udp_socket() -> socket.socket:
    """Create a bound UDP socket on localhost ephemeral port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    return sock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tls_certs() -> tuple[bytes, bytes]:
    """Module-scoped TLS cert + key pair."""
    return _generate_test_certs()


# ---------------------------------------------------------------------------
# Sans-I/O loopback helpers
# ---------------------------------------------------------------------------


def _build_protocol(
    tls_certs: tuple[bytes, bytes],
    app: Any,
    *,
    access_log: bool = False,
    http3_max_connections: int = 10_000,
    http3_qpack_max_table_capacity: int = 0,
) -> tuple[Any, MagicMock]:
    """Build a Pounce ZoomiesDatagramProtocol with a mock transport.

    Returns (protocol, transport_mock).
    """
    from zoomies.core import QuicConfiguration

    from pounce._h3_handler import _create_zoomies_datagram_protocol

    cert_pem, key_pem = tls_certs
    config = ServerConfig(
        host="127.0.0.1",
        port=4433,
        ssl_certfile="/tmp/cert.pem",
        ssl_keyfile="/tmp/key.pem",
        access_log=access_log,
        http3_max_connections=http3_max_connections,
        http3_qpack_max_table_capacity=http3_qpack_max_table_capacity,
    )
    quic_config = QuicConfiguration(
        certificate=cert_pem, private_key=key_pem, idle_timeout=30.0,
    )
    cls = _create_zoomies_datagram_protocol(
        app, config, logging.getLogger("test"), ("127.0.0.1", 4433), quic_config,
    )
    protocol = cls()
    transport = MagicMock(spec=asyncio.DatagramTransport)
    sent: list[tuple[bytes, tuple[str, int]]] = []

    def _capture_sendto(data: bytes, addr: tuple[str, int]) -> None:
        sent.append((data, addr))

    transport.sendto = _capture_sendto
    transport._sent = sent  # stash for test access
    protocol.connection_made(transport)
    return protocol, transport


def _make_client() -> tuple[Any, Any]:
    """Create a QUIC client QuicConnection + H3Connection pair."""
    from zoomies.core import QuicConfiguration, QuicConnection
    from zoomies.h3 import H3Connection

    client_quic = QuicConnection(
        QuicConfiguration(is_client=True, verify_mode=False, server_name="localhost"),
    )
    client_h3 = H3Connection(sender=client_quic)
    return client_quic, client_h3


def _shuttle_to_server(
    client: Any,
    protocol: Any,
    client_addr: tuple[str, int],
    *,
    now: float,
) -> None:
    """Send all pending client datagrams to the server protocol."""
    for dg in client.send_datagrams(now=now):
        protocol.datagram_received(dg, client_addr)


def _shuttle_to_client(
    transport: MagicMock,
    client: Any,
    server_addr: tuple[str, int],
    *,
    now: float,
) -> list[Any]:
    """Collect datagrams from mock transport and feed to client. Returns events."""
    events: list[Any] = []
    for data, _addr in transport._sent:
        events.extend(client.datagram_received(data, server_addr, now=now))
    transport._sent.clear()
    return events


async def _do_handshake(
    client: Any,
    protocol: Any,
    transport: MagicMock,
    client_addr: tuple[str, int] = ("127.0.0.1", 5000),
    server_addr: tuple[str, int] = ("127.0.0.1", 4433),
) -> bool:
    """Perform QUIC handshake via loopback. Returns True if HandshakeComplete seen."""
    from zoomies.events import HandshakeComplete

    now = time.monotonic()
    client.connect()

    all_client_events: list[Any] = []
    # Up to 4 rounds is plenty for TLS 1.3
    for _ in range(4):
        _shuttle_to_server(client, protocol, client_addr, now=now)
        await asyncio.sleep(0.01)  # let server tasks run
        evts = _shuttle_to_client(transport, client, server_addr, now=now)
        all_client_events.extend(evts)
        if any(isinstance(e, HandshakeComplete) for e in evts):
            # One more round for client Finished
            _shuttle_to_server(client, protocol, client_addr, now=now)
            await asyncio.sleep(0.01)
            break

    return any(isinstance(e, HandshakeComplete) for e in all_client_events)


# ---------------------------------------------------------------------------
# Sprint 1 — Real QUIC Handshake Integration Tests
# ---------------------------------------------------------------------------


class TestQuicHandshakeIntegration:
    """Real QUIC handshake tests using zoomies 0.3.1 client mode."""

    @pytest.mark.asyncio
    async def test_loopback_handshake_completes(
        self, tls_certs: tuple[bytes, bytes],
    ) -> None:
        """Full TLS 1.3 handshake via sans-I/O datagram shuttle."""

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        protocol, transport = _build_protocol(tls_certs, app)
        client_quic, _client_h3 = _make_client()

        ok = await _do_handshake(client_quic, protocol, transport)
        assert ok, "Client did not receive HandshakeComplete"
        assert len(protocol._connections) == 1

    @pytest.mark.asyncio
    async def test_http3_request_response_through_asgi_app(
        self, tls_certs: tuple[bytes, bytes],
    ) -> None:
        """GET /hello through Pounce ASGI app returns 200 with body."""

        async def app(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                })
                await send({
                    "type": "http.response.body",
                    "body": b"hello-from-pounce",
                })

        from zoomies.events import H3DataReceived, H3HeadersReceived, StreamDataReceived

        protocol, transport = _build_protocol(tls_certs, app)
        client_quic, client_h3 = _make_client()
        client_addr = ("127.0.0.1", 5000)
        server_addr = ("127.0.0.1", 4433)

        ok = await _do_handshake(client_quic, protocol, transport, client_addr, server_addr)
        assert ok

        now = time.monotonic()

        # Send HTTP/3 GET request
        stream_id = 0
        client_h3.send_headers(stream_id, [
            (b":method", b"GET"),
            (b":path", b"/hello"),
            (b":scheme", b"https"),
            (b":authority", b"localhost"),
        ], end_stream=True)

        _shuttle_to_server(client_quic, protocol, client_addr, now=now)
        await asyncio.sleep(0.2)  # let ASGI app process

        # Collect response
        events = _shuttle_to_client(transport, client_quic, server_addr, now=now)
        response_headers = None
        response_body = b""
        for ev in events:
            if isinstance(ev, StreamDataReceived):
                for h3_ev in client_h3.handle_event(ev):
                    if isinstance(h3_ev, H3HeadersReceived):
                        response_headers = dict(h3_ev.headers)
                    elif isinstance(h3_ev, H3DataReceived):
                        response_body += h3_ev.data

        assert response_headers is not None, "No H3 response headers received"
        assert response_headers[b":status"] == b"200"
        assert response_headers[b"content-type"] == b"text/plain"
        assert b"x-request-id" in response_headers
        assert response_body == b"hello-from-pounce"

    @pytest.mark.asyncio
    async def test_connection_limit_with_real_handshakes(
        self, tls_certs: tuple[bytes, bytes],
    ) -> None:
        """First N clients complete handshake; client N+1 is rejected."""

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        protocol, transport = _build_protocol(tls_certs, app, http3_max_connections=2)

        # First two clients handshake successfully
        for i in range(2):
            client_quic, _ = _make_client()
            client_addr = ("127.0.0.1", 5000 + i)
            ok = await _do_handshake(client_quic, protocol, transport, client_addr)
            assert ok, f"Client {i} handshake failed"

        assert len(protocol._connections) == 2

        # Third client should be rejected at connection limit
        client_quic3, _ = _make_client()
        client_addr3 = ("127.0.0.1", 5002)
        now = time.monotonic()

        client_quic3.connect()
        _shuttle_to_server(client_quic3, protocol, client_addr3, now=now)
        await asyncio.sleep(0.05)

        # Server should not have added a third connection
        assert len(protocol._connections) == 2
        assert client_addr3 not in protocol._connections

    @pytest.mark.asyncio
    async def test_graceful_shutdown_after_real_handshake(
        self, tls_certs: tuple[bytes, bytes],
    ) -> None:
        """close_all_connections works after real handshake established."""

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        protocol, transport = _build_protocol(tls_certs, app)
        client_quic, _ = _make_client()

        ok = await _do_handshake(client_quic, protocol, transport)
        assert ok
        assert len(protocol._connections) == 1

        protocol.close_all_connections()
        assert len(protocol._connections) == 0


class TestH3WorkerRealHandshake:
    """H3Worker with real QUIC client over real UDP sockets."""

    def test_worker_completes_real_handshake(
        self, tls_certs: tuple[bytes, bytes], tmp_path: Any,
    ) -> None:
        """QUIC client completes handshake with H3Worker over real UDP."""
        from zoomies.core import QuicConfiguration, QuicConnection
        from zoomies.events import HandshakeComplete

        cert_pem, key_pem = tls_certs
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        cert_file.write_bytes(cert_pem)
        key_file.write_bytes(key_pem)

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
            http3_idle_timeout=5.0,
            access_log=False,
        )
        sock = _make_udp_socket()
        server_addr = sock.getsockname()
        ext_shutdown = threading.Event()

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        worker = H3Worker(
            config, app, sock, worker_id=0,
            shutdown_event=ext_shutdown,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
        )
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        time.sleep(0.3)

        try:
            # Create real QUIC client
            client = QuicConnection(
                QuicConfiguration(
                    is_client=True, verify_mode=False, server_name="localhost",
                ),
            )
            client.connect()
            now = time.monotonic()

            # Use a real UDP socket for the client
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client_sock.settimeout(2.0)
            try:
                # Send Initial
                for dg in client.send_datagrams(now=now):
                    client_sock.sendto(dg, server_addr)

                # Read server response and feed back
                all_events: list[Any] = []
                for _ in range(10):  # up to 10 response packets
                    try:
                        data, _addr = client_sock.recvfrom(65535)
                    except TimeoutError:
                        break
                    events = client.datagram_received(data, server_addr, now=now)
                    all_events.extend(events)

                    # Send any client responses (Finished, ACKs)
                    for dg in client.send_datagrams(now=now):
                        client_sock.sendto(dg, server_addr)

                    if any(isinstance(e, HandshakeComplete) for e in events):
                        break

                assert any(
                    isinstance(e, HandshakeComplete) for e in all_events
                ), f"Handshake did not complete. Events: {[type(e).__name__ for e in all_events]}"
            finally:
                client_sock.close()

            assert thread.is_alive(), "Worker crashed during handshake"
        finally:
            ext_shutdown.set()
            thread.join(timeout=5.0)
            assert not thread.is_alive()


# ---------------------------------------------------------------------------
# Sprint 2 — QPACK Dynamic Table Integration Tests
# ---------------------------------------------------------------------------


class TestQpackDynamicTable:
    """QPACK dynamic table compression via H3Connection."""

    @pytest.mark.asyncio
    async def test_dynamic_table_enabled_with_capacity(
        self, tls_certs: tuple[bytes, bytes],
    ) -> None:
        """H3Connection has encoder/decoder when capacity > 0."""

        async def app(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"x-custom-repeated", b"same-value-every-time"),
                    ],
                })
                await send({"type": "http.response.body", "body": b"ok"})

        protocol, transport = _build_protocol(
            tls_certs, app, http3_qpack_max_table_capacity=4096,
        )
        client_quic, _client_h3 = _make_client()

        ok = await _do_handshake(client_quic, protocol, transport)
        assert ok

        # Verify the server connection has QPACK encoder/decoder
        conn = next(iter(protocol._connections.values()))
        assert conn.h3.encoder is not None
        assert conn.h3.decoder is not None
        assert conn.h3.encoder.table.capacity == 4096

    def test_encoder_compresses_repeated_headers_standalone(self) -> None:
        """QpackEncoder produces smaller output for repeated custom headers."""
        from zoomies.h3 import Header, QpackEncoder

        enc = QpackEncoder(max_table_capacity=4096)
        enc.set_capacity(4096)

        headers = [Header(":status", "200"), Header("x-custom", "repeated-value")]
        first = enc.encode(headers)
        _instructions = enc.encoder_stream_data()  # consume pending instructions

        second = enc.encode(headers)
        assert len(second) < len(first), (
            f"Expected compression: first={len(first)}, second={len(second)}"
        )

    @pytest.mark.asyncio
    async def test_static_only_with_zero_capacity(
        self, tls_certs: tuple[bytes, bytes],
    ) -> None:
        """With capacity=0, QPACK uses static table only (default behavior)."""

        async def app(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                await send({
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                })
                await send({"type": "http.response.body", "body": b"ok"})

        protocol, transport = _build_protocol(
            tls_certs, app, http3_qpack_max_table_capacity=0,
        )
        client_quic, _client_h3 = _make_client()
        client_addr = ("127.0.0.1", 5000)
        server_addr = ("127.0.0.1", 4433)

        ok = await _do_handshake(client_quic, protocol, transport, client_addr, server_addr)
        assert ok

        # Verify the server-side H3Connection has no encoder (static-only)
        conn = next(iter(protocol._connections.values()))
        assert conn.h3.encoder is None


# ---------------------------------------------------------------------------
# Sprint 4 (retained) — Resilience Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Task 4.1 — End-to-End Protocol Integration Tests
# ---------------------------------------------------------------------------


class TestProtocolFactoryIntegration:
    """Full integration: real certs → QuicConfiguration → protocol factory → protocol."""

    def test_factory_with_real_certs(self, tls_certs: tuple[bytes, bytes]) -> None:
        """Protocol factory creates a working protocol with real TLS certs."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import create_zoomies_datagram_protocol_factory

        cert_pem, key_pem = tls_certs
        config = ServerConfig(
            host="127.0.0.1",
            port=4433,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
        )
        quic_config = QuicConfiguration(
            certificate=cert_pem,
            private_key=key_pem,
            idle_timeout=config.http3_idle_timeout,
        )

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        factory = create_zoomies_datagram_protocol_factory(
            app, config, logging.getLogger("test"), ("127.0.0.1", 4433), quic_config
        )
        protocol = factory()
        assert protocol.__class__.__name__ == "ZoomiesDatagramProtocol"

    def test_protocol_accepts_datagrams_after_connection_made(
        self, tls_certs: tuple[bytes, bytes]
    ) -> None:
        """Protocol processes datagrams after transport is connected."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        cert_pem, key_pem = tls_certs
        config = ServerConfig(
            host="127.0.0.1",
            port=4433,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
            access_log=False,
        )
        quic_config = QuicConfiguration(
            certificate=cert_pem, private_key=key_pem,
        )

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        cls = _create_zoomies_datagram_protocol(
            app, config, logging.getLogger("test"), ("127.0.0.1", 4433), quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        # Feed a garbage datagram — should not crash (invalid QUIC handled gracefully)
        protocol.datagram_received(b"\xff" * 50, ("10.0.0.1", 5000))

        # Protocol should still be functional
        assert protocol._transport is transport

    def test_protocol_handles_many_invalid_datagrams(
        self, tls_certs: tuple[bytes, bytes]
    ) -> None:
        """Protocol survives a burst of invalid datagrams without crashing."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import _create_zoomies_datagram_protocol

        cert_pem, key_pem = tls_certs
        config = ServerConfig(
            host="127.0.0.1",
            port=4433,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
            access_log=False,
        )
        quic_config = QuicConfiguration(certificate=cert_pem, private_key=key_pem)

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        cls = _create_zoomies_datagram_protocol(
            app, config, logging.getLogger("test"), ("127.0.0.1", 4433), quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        # Fire various malformed payloads — protocol must not crash
        payloads = [
            b"",                      # empty
            b"\x00",                  # tiny
            b"\xff" * 1500,           # max-MTU garbage
            b"\xc0\x00\x00\x01" + b"\x00" * 100,  # long-header-ish
            b"GET / HTTP/1.1\r\n",    # TCP data on UDP port
        ]
        for payload in payloads:
            protocol.datagram_received(payload, ("10.0.0.1", 5000))

        # Protocol still functional
        assert protocol._transport is transport

    def test_graceful_shutdown_with_real_certs(
        self, tls_certs: tuple[bytes, bytes]
    ) -> None:
        """close_all_connections works with real QuicConnection objects."""
        from zoomies.core import QuicConfiguration

        from pounce._h3_handler import (
            _create_zoomies_datagram_protocol,
            _ZoomiesConnection,
        )

        cert_pem, key_pem = tls_certs
        config = ServerConfig(
            host="127.0.0.1",
            port=4433,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
            access_log=False,
        )
        quic_config = QuicConfiguration(certificate=cert_pem, private_key=key_pem)

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        cls = _create_zoomies_datagram_protocol(
            app, config, logging.getLogger("test"), ("127.0.0.1", 4433), quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        # Manually create a real QuicConnection to avoid QUIC parsing issues
        from zoomies.core import QuicConnection
        from zoomies.h3 import H3Connection

        quic = QuicConnection(quic_config)
        conn = _ZoomiesConnection(
            quic=quic,
            h3=H3Connection(sender=quic),
            last_addr=("10.0.0.1", 5000),
        )
        protocol._connections[("10.0.0.1", 5000)] = conn
        assert len(protocol._connections) == 1

        # Graceful shutdown — should call close() and clear maps
        protocol.close_all_connections()
        assert len(protocol._connections) == 0


# ---------------------------------------------------------------------------
# Task 4.2 — Connection Limit Integration Tests
# ---------------------------------------------------------------------------


class TestConnectionLimitIntegration:
    """Connection limit enforcement with real QUIC connections."""

    def test_limit_enforced_with_real_connections(
        self, tls_certs: tuple[bytes, bytes]
    ) -> None:
        """http3_max_connections is enforced with real QuicConnection objects."""
        from zoomies.core import QuicConfiguration, QuicConnection
        from zoomies.h3 import H3Connection

        from pounce._h3_handler import (
            _create_zoomies_datagram_protocol,
            _ZoomiesConnection,
        )

        cert_pem, key_pem = tls_certs
        config = ServerConfig(
            host="127.0.0.1",
            port=4433,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
            access_log=False,
            http3_max_connections=3,
        )
        quic_config = QuicConfiguration(certificate=cert_pem, private_key=key_pem)

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        cls = _create_zoomies_datagram_protocol(
            app, config, logging.getLogger("test"), ("127.0.0.1", 4433), quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        # Fill to capacity with real QuicConnection objects
        for i in range(3):
            addr = ("10.0.0.1", 5000 + i)
            quic = QuicConnection(quic_config)
            conn = _ZoomiesConnection(
                quic=quic, h3=H3Connection(sender=quic), last_addr=addr,
            )
            protocol._connections[addr] = conn

        assert len(protocol._connections) == 3

        # 4th connection should be rejected
        excess_addr = ("10.0.0.1", 6000)
        protocol.datagram_received(b"\x00" * 50, excess_addr)
        assert excess_addr not in protocol._connections
        assert len(protocol._connections) == 3

    def test_server_stable_under_excess_connections(
        self, tls_certs: tuple[bytes, bytes]
    ) -> None:
        """Server stays stable when 2x max_connections attempted."""
        from zoomies.core import QuicConfiguration, QuicConnection
        from zoomies.h3 import H3Connection

        from pounce._h3_handler import (
            _create_zoomies_datagram_protocol,
            _ZoomiesConnection,
        )

        cert_pem, key_pem = tls_certs
        config = ServerConfig(
            host="127.0.0.1",
            port=4433,
            ssl_certfile="/tmp/cert.pem",
            ssl_keyfile="/tmp/key.pem",
            access_log=False,
            http3_max_connections=5,
        )
        quic_config = QuicConfiguration(certificate=cert_pem, private_key=key_pem)

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        cls = _create_zoomies_datagram_protocol(
            app, config, logging.getLogger("test"), ("127.0.0.1", 4433), quic_config
        )
        protocol = cls()
        transport = MagicMock(spec=asyncio.DatagramTransport)
        protocol.connection_made(transport)

        # Pre-fill to capacity
        for i in range(5):
            addr = ("10.0.0.1", 5000 + i)
            quic = QuicConnection(quic_config)
            conn = _ZoomiesConnection(
                quic=quic, h3=H3Connection(sender=quic), last_addr=addr,
            )
            protocol._connections[addr] = conn

        # Attempt 5 more — all should be rejected
        for i in range(5):
            addr = ("10.0.0.2", 6000 + i)
            protocol.datagram_received(b"\x00" * 50, addr)

        # Still exactly 5 — excess connections were rejected, existing ones preserved
        assert len(protocol._connections) == 5
        for i in range(5):
            assert ("10.0.0.1", 5000 + i) in protocol._connections
        for i in range(5):
            assert ("10.0.0.2", 6000 + i) not in protocol._connections


# ---------------------------------------------------------------------------
# H3Worker Lifecycle Integration Tests
# ---------------------------------------------------------------------------


class TestH3WorkerIntegration:
    """H3Worker with real UDP sockets and TLS certs."""

    def test_worker_starts_and_shuts_down(
        self, tls_certs: tuple[bytes, bytes], tmp_path: Any
    ) -> None:
        """H3Worker starts on a real UDP socket and shuts down cleanly."""
        cert_pem, key_pem = tls_certs
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        cert_file.write_bytes(cert_pem)
        key_file.write_bytes(key_pem)

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
            http3_idle_timeout=5.0,
        )
        sock = _make_udp_socket()
        ext_shutdown = threading.Event()

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        worker = H3Worker(
            config,
            app,
            sock,
            worker_id=0,
            shutdown_event=ext_shutdown,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
        )

        # Run worker in a thread, shut down after brief delay
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        time.sleep(0.3)

        # Signal shutdown
        ext_shutdown.set()
        thread.join(timeout=5.0)
        assert not thread.is_alive(), "Worker thread did not shut down in time"

    def test_worker_receives_udp_datagram(
        self, tls_certs: tuple[bytes, bytes], tmp_path: Any
    ) -> None:
        """H3Worker receives and processes a UDP datagram on a real socket."""
        cert_pem, key_pem = tls_certs
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        cert_file.write_bytes(cert_pem)
        key_file.write_bytes(key_pem)

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
            http3_idle_timeout=5.0,
            access_log=False,
        )
        sock = _make_udp_socket()
        server_addr = sock.getsockname()
        ext_shutdown = threading.Event()

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        worker = H3Worker(
            config,
            app,
            sock,
            worker_id=0,
            shutdown_event=ext_shutdown,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
        )

        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        time.sleep(0.3)

        # Send a datagram to the worker's socket — should not crash the worker
        client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            client_sock.sendto(b"\x00" * 50, server_addr)
            time.sleep(0.2)  # Give worker time to process
        finally:
            client_sock.close()

        # Worker should still be alive after processing invalid datagram
        assert thread.is_alive()

        ext_shutdown.set()
        thread.join(timeout=5.0)
        assert not thread.is_alive()

    def test_multiple_datagrams_dont_crash_worker(
        self, tls_certs: tuple[bytes, bytes], tmp_path: Any
    ) -> None:
        """Sending many datagrams rapidly doesn't crash the H3Worker."""
        cert_pem, key_pem = tls_certs
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        cert_file.write_bytes(cert_pem)
        key_file.write_bytes(key_pem)

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
            http3_idle_timeout=5.0,
            access_log=False,
        )
        sock = _make_udp_socket()
        server_addr = sock.getsockname()
        ext_shutdown = threading.Event()

        async def app(scope: Any, receive: Any, send: Any) -> None:
            pass

        worker = H3Worker(
            config,
            app,
            sock,
            worker_id=0,
            shutdown_event=ext_shutdown,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
        )

        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        time.sleep(0.3)

        # Blast 50 datagrams from different "clients"
        for _i in range(50):
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                client_sock.sendto(b"\x00" * 50, server_addr)
            finally:
                client_sock.close()

        time.sleep(0.5)
        assert thread.is_alive(), "Worker crashed under datagram load"

        ext_shutdown.set()
        thread.join(timeout=5.0)
        assert not thread.is_alive()
