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
from pounce.supervisor import Supervisor

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


def _wait_for_udp_ready(
    addr: tuple[str, int], *, timeout: float = 3.0, interval: float = 0.05
) -> None:
    """Poll until a UDP endpoint responds (or at least accepts a packet)."""
    deadline = time.monotonic() + timeout
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.settimeout(interval)
    try:
        while time.monotonic() < deadline:
            probe.sendto(b"\x00", addr)
            try:
                probe.recvfrom(1024)
                return  # got a response — worker is up
            except TimeoutError, OSError:
                pass
            time.sleep(interval)
    finally:
        probe.close()
    # If we reach here, assume the worker is ready (it processed our probes)


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
        certificate=cert_pem,
        private_key=key_pem,
        idle_timeout=30.0,
    )
    cls = _create_zoomies_datagram_protocol(
        app,
        config,
        logging.getLogger("test"),
        ("127.0.0.1", 4433),
        quic_config,
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
        self,
        tls_certs: tuple[bytes, bytes],
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
        self,
        tls_certs: tuple[bytes, bytes],
    ) -> None:
        """GET /hello through Pounce ASGI app returns 200 with body."""

        async def app(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
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
                        "body": b"hello-from-pounce",
                    }
                )

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
        client_h3.send_headers(
            stream_id,
            [
                (b":method", b"GET"),
                (b":path", b"/hello"),
                (b":scheme", b"https"),
                (b":authority", b"localhost"),
            ],
            end_stream=True,
        )

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
        self,
        tls_certs: tuple[bytes, bytes],
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
        self,
        tls_certs: tuple[bytes, bytes],
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
        self,
        tls_certs: tuple[bytes, bytes],
        tmp_path: Any,
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

        try:
            # Poll until worker is ready (accepts UDP) instead of fixed sleep
            _wait_for_udp_ready(server_addr)

            # Create real QUIC client
            client = QuicConnection(
                QuicConfiguration(
                    is_client=True,
                    verify_mode=False,
                    server_name="localhost",
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

                assert any(isinstance(e, HandshakeComplete) for e in all_events), (
                    f"Handshake did not complete. Events: {[type(e).__name__ for e in all_events]}"
                )
            finally:
                client_sock.close()

            assert thread.is_alive(), "Worker crashed during handshake"
        finally:
            ext_shutdown.set()
            thread.join(timeout=5.0)
            assert not thread.is_alive()


# ---------------------------------------------------------------------------
# Issue #113 — H3 reload/drain deploy contract (orphan-thread proof)
# ---------------------------------------------------------------------------


def _count_h3_worker_threads() -> int:
    """Number of live threads named ``pounce-h3-worker-*`` (any generation)."""
    return sum(
        1 for t in threading.enumerate() if t.is_alive() and t.name.startswith("pounce-h3-worker-")
    )


def _list_h3_worker_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("pounce-h3-worker-")]


def _wait_h3_threads_settle(expected: int, *, timeout: float) -> int:
    """Poll until the live ``pounce-h3-worker-*`` thread count drops to
    ``expected`` (or ``timeout`` elapses). Returns the final count.

    OS threads cannot be force-killed, so ``_drain`` / ``_graceful_reload_impl``
    bound their joins by ``shutdown_timeout`` and may return a beat before a
    worker's own in-loop drain finishes closing its transport. The deploy
    contract is that the thread dies within the bounded window — not the instant
    the supervisor call returns — so we poll across that window.
    """
    deadline = time.monotonic() + timeout
    count = _count_h3_worker_threads()
    while count != expected and time.monotonic() < deadline:
        time.sleep(0.05)
        count = _count_h3_worker_threads()
    return count


def _real_handshake_and_inflight_request(
    server_addr: tuple[str, int],
    *,
    stream_id: int = 0,
    path: bytes = b"/slow",
    timeout: float = 3.0,
) -> tuple[Any, Any, socket.socket]:
    """Complete a real QUIC handshake against a live H3Worker over real UDP and
    fire one GET that the (slow) app holds in-flight.

    Returns (client QuicConnection, client H3Connection, client UDP socket) so
    the caller keeps the request in-flight. Raises AssertionError if the
    handshake does not complete within ``timeout``.
    """
    from zoomies.core import QuicConfiguration, QuicConnection
    from zoomies.events import HandshakeComplete
    from zoomies.h3 import H3Connection

    client = QuicConnection(
        QuicConfiguration(is_client=True, verify_mode=False, server_name="localhost"),
    )
    client.connect()
    now = time.monotonic()
    client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_sock.settimeout(2.0)

    # Send Initial.
    for dg in client.send_datagrams(now=now):
        client_sock.sendto(dg, server_addr)

    # Read server response packets and feed them back (mirrors the proven
    # TestH3WorkerRealHandshake loop). TLS 1.3 needs only a couple of rounds.
    done = False
    for _ in range(20):
        try:
            data, _addr = client_sock.recvfrom(65535)
        except TimeoutError:
            break
        events = client.datagram_received(data, server_addr, now=time.monotonic())
        for dg in client.send_datagrams(now=time.monotonic()):
            client_sock.sendto(dg, server_addr)
        if any(isinstance(e, HandshakeComplete) for e in events):
            done = True
            break

    if not done:
        client_sock.close()
        raise AssertionError("QUIC handshake did not complete within timeout")

    # Fire one GET on a client-initiated bidi stream; the slow app keeps it
    # in-flight (no end-of-response) so it is a genuine in-flight request.
    h3 = H3Connection(sender=client)
    h3.send_headers(
        stream_id,
        [
            (b":method", b"GET"),
            (b":scheme", b"https"),
            (b":authority", b"localhost"),
            (b":path", path),
        ],
        end_stream=True,
    )
    for dg in client.send_datagrams(now=time.monotonic()):
        client_sock.sendto(dg, server_addr)
    return client, h3, client_sock


@pytest.mark.issue(240)
class TestH3ReloadDrainDeployContract:
    """#113: lock the H3 (HTTP/3) reload/drain deploy contract under real load.

    Establish a couple of CONCURRENT in-flight H3 requests against live H3
    workers, then exercise both deploy signals through the Supervisor:

    * SIGTERM-style ``Supervisor._drain`` (graceful shutdown), and
    * SIGHUP-style ``Supervisor._graceful_reload_impl`` (rolling reload that
      rotates the H3 generation onto the reimported app, #111).

    After each, assert that NO ``pounce-h3-worker-*`` thread is orphaned past the
    bounded ``shutdown_timeout`` window — the contract #112's ``drain_connections``
    and #111's rotation must uphold.

    In-flight request disposition: the H3 worker drains in-flight stream tasks
    for up to ``shutdown_timeout`` (#112). Requests whose ASGI app completes
    within that window finish normally; any still running at the deadline are
    abruptly cancelled and the QUIC connection is closed (``CONNECTION_CLOSE``).
    The slow app here blocks past the deadline on purpose to exercise the
    abort-straggler path, so those in-flight requests are closed, not completed.

    Gating: a live zoomies/QUIC runtime is required (``is_h3_available()`` skip);
    the thread-orphan assertion is only meaningful on free-threaded 3.14t in CI,
    where the worker threads truly run concurrently. The module still imports and
    the handshake scaffolding runs anywhere zoomies is installed.
    """

    def _make_h3_supervisor(self, app: Any, tmp_path: Any) -> tuple[Any, list[socket.socket]]:
        """Real thread-mode Supervisor with one live H3 worker on real UDP.

        ``worker_mode='async'`` avoids the sync AsyncPool path so ``_drain`` /
        ``_graceful_reload_impl`` operate purely on the H3 generation plus a
        trivial (stubbed) TCP worker.
        """
        cert_pem, key_pem = _generate_test_certs()
        cert_file = tmp_path / "cert.pem"
        key_file = tmp_path / "key.pem"
        cert_file.write_bytes(cert_pem)
        key_file.write_bytes(key_pem)

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            workers=1,
            worker_mode="async",
            reload_timeout=2.0,
            shutdown_timeout=2.0,
            access_log=False,
            http3_idle_timeout=5.0,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
        )
        sup = Supervisor(
            config,
            app,
            app_path="tests.integration.test_h3_integration:_reload_marker_app",
            mode="thread",
        )
        sup._effective_workers = 1
        udp = _make_udp_socket()
        sup._udp_sockets = [udp]
        return sup, [udp]

    def test_drain_leaves_no_orphan_h3_threads_under_load(
        self, tls_certs: tuple[bytes, bytes], tmp_path: Any
    ) -> None:
        """Concurrent in-flight H3 requests + SIGTERM _drain => no orphan
        pounce-h3-worker-* threads after the bounded shutdown window."""

        async def slow_app(scope: Any, receive: Any, send: Any) -> None:
            if scope.get("type") != "http":
                return
            # Stay in-flight past shutdown_timeout WITHOUT blocking the worker
            # event loop (await, not a blocking wait) so concurrent requests
            # and other connections keep being served. This never completes
            # within shutdown_timeout, forcing the abort-straggler path.
            await asyncio.sleep(30.0)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"late"})

        # Wait for a clean baseline (a previous test's worker may still be
        # tearing down — threads can't be force-killed, only joined).
        baseline = _wait_h3_threads_settle(0, timeout=5.0)
        sup, udps = self._make_h3_supervisor(slow_app, tmp_path)
        clients: list[tuple[Any, socket.socket]] = []
        try:
            sup._spawn_h3_worker(0)
            server_addr = udps[0].getsockname()
            _wait_for_udp_ready(server_addr)
            assert _count_h3_worker_threads() == baseline + 1

            # Establish a couple of concurrent in-flight requests (distinct
            # client connections, each on its own client-initiated bidi stream).
            for _ in range(2):
                client, _h3, csock = _real_handshake_and_inflight_request(server_addr, stream_id=0)
                clients.append((client, csock))

            time.sleep(0.3)  # let requests land in-flight

            # SIGTERM-style graceful shutdown. Bounded by shutdown_timeout; the
            # slow app never releases, so stragglers are aborted at the deadline.
            t0 = time.monotonic()
            # Provide a trivial TCP side so _drain's TCP/AsyncPool joins are no-ops.
            sup._handles = []
            sup._drain()
            elapsed = time.monotonic() - t0

            assert sup._shutdown_event.is_set()
            # In-flight disposition: the slow app never finishes within
            # shutdown_timeout, so its 2 in-flight streams are abruptly
            # cancelled (the worker logs "stream(s) still running ... cancelling")
            # and the QUIC connections are CONNECTION_CLOSE'd — they do NOT
            # complete. The contract: NO orphaned pounce-h3-worker-* thread
            # survives past the bounded shutdown window (threads can't be
            # force-killed, so _drain may return a beat before the worker's own
            # in-loop drain closes its transport — poll across the window).
            settled = _wait_h3_threads_settle(baseline, timeout=sup._config.shutdown_timeout + 2.0)
            assert settled == baseline, (
                f"orphaned H3 threads after _drain: {_list_h3_worker_threads()}"
            )
            # Bounded: drain's H3 join is shutdown_timeout per worker.
            assert elapsed < sup._config.shutdown_timeout + 3.0
        finally:
            sup._shutdown_event.set()
            for h in sup._h3_handles:
                if h.reload_shutdown_event is not None:
                    h.reload_shutdown_event.set()
            for _client, csock in clients:
                csock.close()
            for u in udps:
                u.close()

    def test_graceful_reload_rotates_h3_generation_no_orphans(
        self, tls_certs: tuple[bytes, bytes], tmp_path: Any
    ) -> None:
        """Concurrent in-flight H3 requests + SIGHUP _graceful_reload_impl =>
        the H3 generation is rotated onto the reimported app and no old
        pounce-h3-worker-* thread is orphaned past reload's bounded window."""

        async def slow_app(scope: Any, receive: Any, send: Any) -> None:
            if scope.get("type") != "http":
                return
            # Stay in-flight past shutdown_timeout WITHOUT blocking the worker
            # event loop (await, not a blocking wait) so concurrent requests
            # and other connections keep being served. This never completes
            # within shutdown_timeout, forcing the abort-straggler path.
            await asyncio.sleep(30.0)
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"late"})

        # Wait for a clean baseline (a previous test's worker may still be
        # tearing down — threads can't be force-killed, only joined).
        baseline = _wait_h3_threads_settle(0, timeout=5.0)
        sup, udps = self._make_h3_supervisor(slow_app, tmp_path)
        clients: list[tuple[Any, socket.socket]] = []
        old_handle = None
        try:
            sup._spawn_h3_worker(0)
            server_addr = udps[0].getsockname()
            _wait_for_udp_ready(server_addr)
            old_handle = sup._h3_handles[0]
            assert _count_h3_worker_threads() == baseline + 1

            for _ in range(2):
                client, _h3, csock = _real_handshake_and_inflight_request(server_addr, stream_id=0)
                clients.append((client, csock))

            time.sleep(0.3)

            # Keep the TCP side trivial: idle fake worker, instant-exit thread,
            # so the reload exercises only the H3 rotation path.
            from unittest.mock import patch

            import pounce._importer as importer

            with (
                patch.object(importer, "reimport_app", lambda *_a, **_k: slow_app),
                patch.object(
                    Supervisor,
                    "_create_worker",
                    lambda self, worker_id, socket_index: _IdleReloadWorker(),
                ),
            ):
                t0 = time.monotonic()
                sup._graceful_reload_impl()
                elapsed = time.monotonic() - t0

            # New H3 generation present and rotated (distinct handle).
            assert len(sup._h3_handles) == 1
            new_handle = sup._h3_handles[0]
            assert new_handle is not old_handle, "H3 generation was not rotated"
            # The shared shutdown event must NOT be set (TCP gen untouched).
            assert not sup._shutdown_event.is_set()
            # In-flight disposition: the old generation's 2 in-flight streams are
            # drained for up to shutdown_timeout; the slow app never finishes, so
            # they are abruptly cancelled and the old QUIC connections closed —
            # they do NOT complete. The OLD H3 worker thread must die within the
            # bounded reload window (retired via its per-worker reload event +
            # joined), leaving exactly ONE live H3 worker: the new generation.
            settled = _wait_h3_threads_settle(
                baseline + 1, timeout=sup._config.shutdown_timeout + 2.0
            )
            assert settled == baseline + 1, (
                f"orphaned/extra H3 threads after reload: {_list_h3_worker_threads()}"
            )
            assert not old_handle.target.is_alive(), "old H3 worker thread orphaned"
            assert new_handle.target.is_alive(), "new H3 generation not running"
            assert elapsed < sup._config.reload_timeout + 3.0
        finally:
            sup._shutdown_event.set()
            for h in sup._h3_handles:
                if h.reload_shutdown_event is not None:
                    h.reload_shutdown_event.set()
            if old_handle is not None and old_handle.reload_shutdown_event is not None:
                old_handle.reload_shutdown_event.set()
            for _client, csock in clients:
                csock.close()
            for u in udps:
                u.close()
            # Drain the new generation so no threads leak past the test.
            sup._drain()


class _IdleReloadWorker:
    """Trivial TCP worker stand-in for reload (always idle, instant exit)."""

    def __init__(self) -> None:
        self._draining = False

    def run(self) -> None:
        return

    def start_draining(self) -> None:
        self._draining = True

    def is_idle(self) -> bool:
        return True

    def set_lifespan_state(self, state: Any) -> None:
        return


async def _reload_marker_app(scope: Any, receive: Any, send: Any) -> None:  # pragma: no cover
    """Module-level app referenced by app_path in the reload supervisor."""
    if scope.get("type") != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"reloaded"})


# ---------------------------------------------------------------------------
# Sprint 2 — QPACK Dynamic Table Integration Tests
# ---------------------------------------------------------------------------


class TestQpackDynamicTable:
    """QPACK dynamic table compression via H3Connection."""

    @pytest.mark.asyncio
    async def test_dynamic_table_enabled_with_capacity(
        self,
        tls_certs: tuple[bytes, bytes],
    ) -> None:
        """H3Connection has encoder/decoder when capacity > 0."""

        async def app(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [
                            (b"content-type", b"text/plain"),
                            (b"x-custom-repeated", b"same-value-every-time"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b"ok"})

        protocol, transport = _build_protocol(
            tls_certs,
            app,
            http3_qpack_max_table_capacity=4096,
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
        self,
        tls_certs: tuple[bytes, bytes],
    ) -> None:
        """With capacity=0, QPACK uses static table only (default behavior)."""

        async def app(scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] == "http":
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send({"type": "http.response.body", "body": b"ok"})

        protocol, transport = _build_protocol(
            tls_certs,
            app,
            http3_qpack_max_table_capacity=0,
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
            certificate=cert_pem,
            private_key=key_pem,
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

    def test_protocol_handles_many_invalid_datagrams(self, tls_certs: tuple[bytes, bytes]) -> None:
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
            b"",  # empty
            b"\x00",  # tiny
            b"\xff" * 1500,  # max-MTU garbage
            b"\xc0\x00\x00\x01" + b"\x00" * 100,  # long-header-ish
            b"GET / HTTP/1.1\r\n",  # TCP data on UDP port
        ]
        for payload in payloads:
            protocol.datagram_received(payload, ("10.0.0.1", 5000))

        # Protocol still functional
        assert protocol._transport is transport

    def test_graceful_shutdown_with_real_certs(self, tls_certs: tuple[bytes, bytes]) -> None:
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

    def test_limit_enforced_with_real_connections(self, tls_certs: tuple[bytes, bytes]) -> None:
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
                quic=quic,
                h3=H3Connection(sender=quic),
                last_addr=addr,
            )
            protocol._connections[addr] = conn

        assert len(protocol._connections) == 3

        # 4th connection should be rejected
        excess_addr = ("10.0.0.1", 6000)
        protocol.datagram_received(b"\x00" * 50, excess_addr)
        assert excess_addr not in protocol._connections
        assert len(protocol._connections) == 3

    def test_server_stable_under_excess_connections(self, tls_certs: tuple[bytes, bytes]) -> None:
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
                quic=quic,
                h3=H3Connection(sender=quic),
                last_addr=addr,
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

        # Run worker in a thread, wait for readiness
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        try:
            _wait_for_udp_ready(server_addr)

            # Signal shutdown
            ext_shutdown.set()
            thread.join(timeout=5.0)
            assert not thread.is_alive(), "Worker thread did not shut down in time"
        finally:
            ext_shutdown.set()
            thread.join(timeout=5.0)

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
        try:
            _wait_for_udp_ready(server_addr)

            # Send a datagram to the worker's socket — should not crash the worker
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                client_sock.sendto(b"\x00" * 50, server_addr)
                time.sleep(0.2)  # Give worker time to process
            finally:
                client_sock.close()

            # Worker should still be alive after processing invalid datagram
            assert thread.is_alive()
        finally:
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
        try:
            _wait_for_udp_ready(server_addr)

            # Blast 50 datagrams from different "clients"
            for _i in range(50):
                client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    client_sock.sendto(b"\x00" * 50, server_addr)
                finally:
                    client_sock.close()

            time.sleep(0.5)
            assert thread.is_alive(), "Worker crashed under datagram load"
        finally:
            ext_shutdown.set()
            thread.join(timeout=5.0)
            assert not thread.is_alive()
