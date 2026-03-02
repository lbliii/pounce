"""Latency benchmarks for pounce — measures request round-trip time."""

import socket
import threading
import time

import pytest

from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker
from tests.conftest import _wait_for_ready, with_lifespan


@with_lifespan
async def _minimal_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal app that returns 200 OK with small body."""
    await receive()
    body = b"ok"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", b"2")],
        }
    )
    await send({"type": "http.response.body", "body": body})


_REQUEST = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"


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
def _worker_addr():
    """Start worker once for the module."""
    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    sock = create_listener(config)
    addr = sock.getsockname()
    worker = Worker(config, _minimal_app, sock, worker_id=0)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    _wait_for_ready(addr)
    yield addr
    worker.shutdown()
    thread.join(timeout=2)
    sock.close()


@pytest.mark.benchmark
@pytest.mark.timeout(30)
def test_latency_simple_get(benchmark, _worker_addr) -> None:
    """Measure latency of simple GET requests (p50/p95/p99)."""
    addr = _worker_addr

    def _run():
        return _send_raw(addr, _REQUEST)

    result = benchmark.pedantic(_run, rounds=1000, iterations=1, warmup_rounds=50)
    assert b"200" in result


@pytest.mark.benchmark
@pytest.mark.timeout(30)
def test_latency_throughput(benchmark, _worker_addr) -> None:
    """Measure requests per second over 1000 sequential requests."""
    addr = _worker_addr

    def _run():
        for _ in range(100):
            _send_raw(addr, _REQUEST)

    benchmark.pedantic(_run, rounds=10, iterations=1, warmup_rounds=2)
    # 100 requests × 10 rounds = 1000 requests total
    # benchmark.stats reports mean/stdev of round time
