"""Throughput benchmarks for pounce — measures requests per second."""

import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker
from tests.conftest import _wait_for_ready, with_lifespan


@with_lifespan
async def _minimal_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal app that returns 200 OK."""
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


def _run_requests(addr: tuple[str, int], count: int) -> int:
    """Send count requests sequentially, return number of successful (200) responses."""
    ok = 0
    for _ in range(count):
        try:
            resp = _send_raw(addr, _REQUEST)
            if b"200" in resp:
                ok += 1
        except ConnectionError, OSError, TimeoutError:
            pass
    return ok


@pytest.fixture(scope="module")
def worker_addr():
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
@pytest.mark.timeout(60)
def test_throughput_burst(benchmark, worker_addr: tuple[str, int]) -> None:
    """Measure requests/sec with 50 concurrent connections x 20 requests each."""
    addr = worker_addr
    num_connections = 50
    requests_per_conn = 20

    def _run():
        total_ok = 0
        with ThreadPoolExecutor(max_workers=num_connections) as ex:
            futures = [
                ex.submit(_run_requests, addr, requests_per_conn) for _ in range(num_connections)
            ]
            for f in as_completed(futures, timeout=30):
                total_ok += f.result()
        expected = num_connections * requests_per_conn
        assert total_ok >= expected * 0.9, f"Got {total_ok}/{expected} successful"

    benchmark.pedantic(
        _run,
        rounds=5,
        iterations=1,
        warmup_rounds=1,
    )
