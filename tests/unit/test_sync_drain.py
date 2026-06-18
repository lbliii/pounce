"""Drain-path tests for SyncWorker and AcceptDistributor (issues #100, #101).

#100: the keep-alive loop must honour ``_drain_event`` — finish the in-flight
request, emit ``Connection: close``, then exit so ``is_idle()`` flips True
well before the supervisor's reload_timeout.

#101: a draining SyncWorker accept/queue path and a draining AcceptDistributor
must answer a *new* connection with a bounded, actionable 503 (not a silent
drop / orphaned queue entry).
"""

from __future__ import annotations

import queue
import socket
import threading

import pytest

from pounce._drain import DRAIN_503_RESPONSE
from pounce.accept_distributor import AcceptDistributor
from pounce.sync_worker import SyncWorker

# Reuse the established mock-socket harness from the main sync-worker tests.
from tests.unit.test_sync_worker import (  # type: ignore[import-not-found]
    MockSocket,
    _build_http_request,
    _make_config,
    _make_worker,
    _simple_asgi_app,
)


def _response_blocks(sent: bytes) -> list[bytes]:
    """Split a stream of concatenated HTTP responses on the head boundary."""
    # Cheap split: each response begins with "HTTP/1.1 ".
    parts = sent.split(b"HTTP/1.1 ")
    return [b"HTTP/1.1 " + p for p in parts if p]


class _DrainAfterFirstSocket(MockSocket):
    """Mock socket that flips the worker's drain flag after the 1st recv.

    Lets the first keep-alive request go through normally, then ensures the
    worker is draining when it reads the *second* request.
    """

    def __init__(self, *args: object, worker_holder: list[SyncWorker], **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._worker_holder = worker_holder
        self._recv_calls = 0

    def recv_into(self, buf: memoryview | bytearray) -> int:
        n = super().recv_into(buf)
        self._recv_calls += 1
        # After the first request's bytes are consumed, start draining so the
        # second request (already buffered) is served with Connection: close.
        if self._recv_calls == 1 and self._worker_holder:
            self._worker_holder[0].start_draining()
        return n


class _DrainAfterFirstResponse(MockSocket):
    """Mock socket that starts draining after the first response is sent."""

    def __init__(
        self,
        first_request: bytes,
        second_request: bytes,
        *,
        worker_holder: list[SyncWorker],
    ) -> None:
        super().__init__(recv_data=first_request)
        self._pending_second = second_request
        self._worker_holder = worker_holder
        self._responses_sent = 0

    def sendall(self, data: bytes | bytearray) -> None:
        super().sendall(data)
        self._responses_sent += 1
        if self._responses_sent == 1:
            if self._worker_holder:
                self._worker_holder[0].start_draining()
            self._recv_data = self._pending_second
            self._recv_offset = 0

    def sendmsg(self, buffers: list[bytes | bytearray]) -> int:
        result = super().sendmsg(buffers)
        self._responses_sent += 1
        if self._responses_sent == 1:
            if self._worker_holder:
                self._worker_holder[0].start_draining()
            self._recv_data = self._pending_second
            self._recv_offset = 0
        return result


def test_keepalive_drain_forces_connection_close() -> None:
    """A keep-alive client mid-connection gets Connection: close once draining."""
    config = _make_config()
    holder: list[SyncWorker] = []
    worker = _make_worker(config=config, app=_simple_asgi_app)
    holder.append(worker)

    # Two pipelined keep-alive requests on one connection.
    req = _build_http_request(headers={"Host": "localhost", "Connection": "keep-alive"})
    sock = _DrainAfterFirstSocket(req + req, worker_holder=holder)

    import asyncio

    runner = asyncio.Runner()
    try:
        worker._handle_connection(sock, ("127.0.0.1", 54321), runner)
    finally:
        runner.close()

    blocks = _response_blocks(bytes(sock.sent_data))
    assert len(blocks) >= 1
    # The last response served before the loop exits must close the connection.
    assert b"connection: close" in blocks[-1].lower()
    # Worker must be idle afterwards (loop exited, _active_connections back to 0).
    assert worker.is_idle()
    assert sock.closed


def test_health_check_returns_503_when_draining() -> None:
    """Keep-alive health probes on a draining worker must not report ready."""
    config = _make_config(health_check_path="/healthz")
    holder: list[SyncWorker] = []
    worker = _make_worker(config=config)
    holder.append(worker)

    first = _build_http_request(path="/healthz", headers={"Host": "localhost", "Connection": "keep-alive"})
    second = _build_http_request(path="/healthz", headers={"Host": "localhost", "Connection": "keep-alive"})
    sock = _DrainAfterFirstResponse(first, second, worker_holder=holder)

    import asyncio

    runner = asyncio.Runner()
    try:
        worker._handle_connection(sock, ("127.0.0.1", 54321), runner)
    finally:
        runner.close()

    blocks = _response_blocks(bytes(sock.sent_data))
    assert len(blocks) >= 2
    assert b"200" in blocks[0]
    assert b'"status": "ok"' in blocks[0] or b'"status":"ok"' in blocks[0]
    assert b"503" in blocks[1]
    assert b"draining" in blocks[1]


def test_keepalive_no_drain_keeps_alive() -> None:
    """Control: without draining, a keep-alive request stays keep-alive."""
    config = _make_config(max_requests_per_connection=1)
    worker = _make_worker(config=config, app=_simple_asgi_app)
    req = _build_http_request(headers={"Host": "localhost", "Connection": "keep-alive"})
    sock = MockSocket(req)

    import asyncio

    runner = asyncio.Runner()
    try:
        worker._handle_connection(sock, ("127.0.0.1", 54321), runner)
    finally:
        runner.close()

    # With max_requests_per_connection=1 the first (only) response carries close,
    # so use a fresh config with no limit to observe keep-alive instead.
    config2 = _make_config()
    worker2 = _make_worker(config=config2, app=_simple_asgi_app)
    sock2 = MockSocket(req)  # single request, recv then EOF
    runner2 = asyncio.Runner()
    try:
        worker2._handle_connection(sock2, ("127.0.0.1", 54321), runner2)
    finally:
        runner2.close()
    first = _response_blocks(bytes(sock2.sent_data))[0]
    assert b"connection: keep-alive" in first.lower()


def test_full_shutdown_queue_worker_serves_in_flight_not_reset() -> None:
    """#104: on FULL shutdown, a draining SyncWorker SERVES a queued connection.

    Full shutdown is ``_ext_shutdown`` set (SIGTERM). A connection still sitting
    in the shared queue was accepted by the AcceptDistributor BEFORE drain — the
    client has already sent its request and is in-flight. The contract requires
    in-flight requests to COMPLETE, so the retiring worker must serve it to a
    real response, not 503 or reset it. (503ing it after the client sent its
    request RSTs the socket — the connection-reset bug this fixes.)
    """
    config = _make_config()
    conn_queue: queue.Queue[tuple[socket.socket, object]] = queue.Queue()
    shutdown = threading.Event()
    shutdown.set()  # FULL shutdown in effect
    worker = SyncWorker(
        config=config,
        app=_simple_asgi_app,
        sock=None,
        worker_id=0,
        shutdown_event=shutdown,
        conn_queue=conn_queue,
    )
    worker.start_draining()

    a, b = socket.socketpair()
    # The in-flight request the client already sent before drain began.
    b.sendall(_build_http_request("GET", "/", headers={"connection": "close"}))
    conn_queue.put((a, ("127.0.0.1", 1234)))

    import asyncio

    runner = asyncio.Runner()
    try:
        # _run_from_queue exits the accept loop immediately (_should_stop() is
        # True) but must then SERVE the queued in-flight connection to
        # completion rather than orphan or reset it.
        worker._run_from_queue(0.05, runner)
    finally:
        runner.close()

    b.setblocking(True)
    b.settimeout(1.0)
    received = b""
    try:
        while True:
            chunk = b.recv(4096)
            if not chunk:
                break
            received += chunk
    except TimeoutError:
        pass
    a.close()
    b.close()
    # The in-flight request was served with a real response, not 503'd/reset.
    assert received.startswith(b"HTTP/1.1 200 "), f"expected served 200, got {received[:80]!r}"
    assert received != DRAIN_503_RESPONSE
    # The queued connection was consumed off the shared queue.
    assert conn_queue.empty()


def test_reload_queue_worker_preserves_queued_conn_for_new_gen() -> None:
    """#102: on graceful RELOAD, a retiring SyncWorker must NOT 503 queued conns.

    Reload is ``_drain_event`` set but ``_ext_shutdown`` UNSET (SIGHUP). The
    AcceptDistributor keeps enqueuing into the SHARED queue for the NEW
    generation, so the retiring worker must leave queued connections in place
    rather than steal them with a 503.

    Fails against the pre-fix branch, where _drain_pending_queue 503s the queue
    unconditionally on every _run_from_queue exit.
    """
    config = _make_config()
    conn_queue: queue.Queue[tuple[socket.socket, object]] = queue.Queue()
    shutdown = threading.Event()  # NOT set -> graceful reload, not full shutdown
    worker = SyncWorker(
        config=config,
        app=_simple_asgi_app,
        sock=None,
        worker_id=0,
        shutdown_event=shutdown,
        conn_queue=conn_queue,
    )
    worker.start_draining()  # reload signals drain, not shutdown

    a, b = socket.socketpair()
    conn_queue.put((a, ("127.0.0.1", 1234)))

    import asyncio

    runner = asyncio.Runner()
    try:
        worker._run_from_queue(0.05, runner)
    finally:
        runner.close()

    # The connection must remain queued for the new generation: not consumed,
    # not 503'd.
    assert conn_queue.qsize() == 1
    queued_conn, _addr = conn_queue.get_nowait()
    assert queued_conn is a
    # Nothing was written to the peer (no premature 503).
    b.setblocking(True)
    b.settimeout(0.3)
    received = b""
    try:
        while True:
            chunk = b.recv(4096)
            if not chunk:
                break
            received += chunk
    except TimeoutError:
        pass
    a.close()
    b.close()
    assert received == b"", f"reload must not 503 a queued conn, got: {received!r}"


def test_accept_distributor_drain_503_no_enqueue() -> None:
    """#101: a draining AcceptDistributor 503s a new conn and never enqueues it."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]

    conn_queue: queue.Queue[tuple[socket.socket, object]] = queue.Queue()
    shutdown = threading.Event()
    drain = threading.Event()
    distributor = AcceptDistributor(
        listener,
        conn_queue,
        shutdown_event=shutdown,
        drain_event=drain,
    )

    t = threading.Thread(target=distributor.run, daemon=True)
    t.start()

    # Begin draining, then connect a brand-new client.
    drain.set()
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(2.0)
    client.connect(("127.0.0.1", port))

    received = b""
    try:
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            received += chunk
    except TimeoutError:
        pass
    client.close()

    shutdown.set()
    t.join(timeout=2.0)
    listener.close()

    assert received == DRAIN_503_RESPONSE
    # The connection must NOT have been enqueued for a worker.
    assert conn_queue.empty()


def _accept_window_worker(listener: socket.socket, shutdown: threading.Event | None) -> SyncWorker:
    """Build a SyncWorker bound to a real listener for accept-window tests."""
    # Short shutdown_timeout keeps the full-shutdown accept window bounded.
    config = _make_config(shutdown_timeout=1.0)
    worker = SyncWorker(
        config=config,
        app=_simple_asgi_app,
        sock=listener,
        worker_id=0,
        shutdown_event=shutdown,
    )
    worker.start_draining()
    return worker


def test_full_shutdown_accept_window_503s_new_connection() -> None:
    """#101: on FULL shutdown, the accept window 503s a brand-new connection.

    Full shutdown (``_ext_shutdown`` set) means the server is going away; a
    client that races in during the bounded grace window must get an actionable
    503, not a silent drop.

    Fails against the pre-fix branch, where the window returns EARLY whenever
    ``_ext_shutdown`` is set — so the new connection would never be answered.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]

    shutdown = threading.Event()
    shutdown.set()  # FULL shutdown
    worker = _accept_window_worker(listener, shutdown)

    t = threading.Thread(target=worker._drain_accept_window, daemon=True)
    t.start()

    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(2.0)
    client.connect(("127.0.0.1", port))
    received = b""
    try:
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            received += chunk
    except TimeoutError:
        pass
    client.close()

    t.join(timeout=3.0)
    assert not t.is_alive(), "accept window did not exit within shutdown_timeout"
    listener.close()
    assert received == DRAIN_503_RESPONSE


def test_reload_accept_window_returns_early_no_accept() -> None:
    """#102: on graceful RELOAD, the accept window returns early (no accept).

    Reload (``_drain_event`` set, ``_ext_shutdown`` UNSET) hands the listener to
    the NEW generation. The retiring worker must NOT accept/503 new connections
    (it would steal them) and must return promptly (no full shutdown_timeout
    spin).

    Fails against the pre-fix branch, where the window only returns early when
    ``_ext_shutdown`` is set — so on reload it would (wrongly) accept and 503.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]

    shutdown = threading.Event()  # NOT set -> graceful reload
    worker = _accept_window_worker(listener, shutdown)

    start = __import__("time").monotonic()
    t = threading.Thread(target=worker._drain_accept_window, daemon=True)
    t.start()
    t.join(timeout=3.0)
    elapsed = __import__("time").monotonic() - start
    assert not t.is_alive(), "reload accept window must return promptly"
    # Returned early — far below shutdown_timeout (1.0s), proving no accept spin.
    assert elapsed < 0.5, f"reload window spun for {elapsed:.2f}s (should return early)"

    # A connection that arrives now is left for the NEW generation: the retiring
    # worker's window has already exited without ever accepting it.
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.settimeout(0.5)
    client.connect(("127.0.0.1", port))
    received = b""
    try:
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            received += chunk
    except TimeoutError:
        pass
    client.close()
    listener.close()
    # The retiring worker never wrote a 503; the connection is untouched (the
    # real server's NEW generation would accept it from the same listener).
    assert received == b"", f"reload window must not 503 new conns, got: {received!r}"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
