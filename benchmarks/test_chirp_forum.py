"""Chirp/LB Sonic-shaped forum workload benchmark."""

import socket
import threading

import pytest

from benchmarks.apps.chirp_forum import app
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker
from tests.conftest import _wait_for_ready

_REQUESTS = (
    b"GET / HTTP/1.1\r\nHost: alpha.example\r\nConnection: close\r\n\r\n",
    b"GET /threads/1 HTTP/1.1\r\nHost: alpha.example\r\nConnection: close\r\n\r\n",
    (
        b"POST /threads/1/reply HTTP/1.1\r\n"
        b"Host: alpha.example\r\n"
        b"Content-Type: application/x-www-form-urlencoded\r\n"
        b"Content-Length: 14\r\n"
        b"Connection: close\r\n"
        b"\r\n"
        b"body=Next+move"
    ),
    b"GET /assets/forum.css HTTP/1.1\r\nHost: alpha.example\r\nConnection: close\r\n\r\n",
)


def _send_raw(addr: tuple[str, int], request: bytes, timeout: float = 5.0) -> bytes:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(addr)
        sock.sendall(request)
        response = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            response += chunk
        return response
    finally:
        sock.close()


@pytest.fixture(scope="module")
def chirp_worker_addr() -> tuple[str, int]:
    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    sock = create_listener(config)
    addr = sock.getsockname()
    worker = Worker(config, app, sock, worker_id=0)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    _wait_for_ready(addr)
    yield addr
    worker.shutdown()
    thread.join(timeout=2)
    sock.close()


@pytest.mark.benchmark
@pytest.mark.timeout(30)
def test_chirp_forum_request_mix(benchmark, chirp_worker_addr: tuple[str, int]) -> None:
    """Measure a representative host-routed forum request mix."""
    addr = chirp_worker_addr

    def _run() -> None:
        for request in _REQUESTS:
            response = _send_raw(addr, request)
            assert b"HTTP/1.1 200" in response

    benchmark.pedantic(_run, rounds=100, iterations=1, warmup_rounds=10)
