"""
Smoke tests for example apps.

Imports each example, starts a pounce worker, sends one HTTP request,
and asserts a 200 response.  No throughput measurement — just "does it
work".  If an example breaks after an API change, this catches it.

"""

import pytest

from tests.conftest import send_raw_request, start_worker

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

_GET = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

_GET_SSE = (
    b"GET / HTTP/1.1\r\nHost: localhost\r\nAccept: text/event-stream\r\nConnection: close\r\n\r\n"
)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.timeout(10)
def test_hello_example() -> None:
    """examples/hello.py returns 200 with Hello, World!"""
    from examples.hello import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"Hello, World!" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

@pytest.mark.timeout(10)
def test_lifespan_example() -> None:
    """examples/lifespan.py returns 200 with a request counter."""
    from examples.lifespan import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"request #" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

@pytest.mark.timeout(15)
def test_streaming_sse_example() -> None:
    """examples/streaming_sse.py returns 200 with SSE events."""
    import socket as _socket

    from examples.streaming_sse import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        # SSE streams indefinitely so we can't use send_raw_request (it reads
        # until EOF).  Instead, read a few chunks then close the socket.
        conn = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        conn.settimeout(5.0)
        conn.connect(addr)
        conn.sendall(_GET_SSE)

        response = b""
        for _ in range(5):
            chunk = conn.recv(4096)
            if not chunk:
                break
            response += chunk
            # Stop once we have enough to verify.
            if b"event: heartbeat" in response:
                break

        conn.close()

        assert b"HTTP/1.1 200" in response
        assert b"text/event-stream" in response
        assert b"event: heartbeat" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

@pytest.mark.timeout(10)
def test_compression_demo_example() -> None:
    """examples/compression_demo.py returns 200 with JSON payload."""
    from examples.compression_demo import app
    from pounce.config import ServerConfig

    # Disable compression so we can verify the raw JSON body
    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    worker, sock, thread = start_worker(app, config)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"application/json" in response
        assert b"pounce" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

@pytest.mark.timeout(10)
def test_cpu_parallel_example() -> None:
    """examples/cpu_parallel.py returns 200 with a hash digest."""
    from examples.cpu_parallel import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b'"digest"' in response
        assert b'"iterations"' in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

@pytest.mark.timeout(15)
def test_chirp_app_example() -> None:
    """examples/chirp_app.py returns 200 with chirp response."""
    chirp = pytest.importorskip("chirp")  # noqa: F841

    from examples.chirp_app import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1" in response
        assert b"200" in response
        assert b"Hello from chirp" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()
