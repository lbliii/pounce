"""Bengal-shaped static-site benchmark."""

import socket
import threading

import pytest

from benchmarks.apps.bengal_static import app
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker
from tests.conftest import _wait_for_ready

_PATHS = (b"/", b"/docs/", b"/posts/launch/", b"/assets/site.css", b"/feed.xml")


def _send_raw(addr: tuple[str, int], path: bytes, timeout: float = 5.0) -> bytes:
    request = b"GET " + path + b" HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
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
def bengal_worker_addr() -> tuple[str, int]:
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
def test_bengal_static_site_paths(benchmark, bengal_worker_addr: tuple[str, int]) -> None:
    """Measure a representative Bengal local-dev page/asset mix."""
    addr = bengal_worker_addr

    def _run() -> None:
        for path in _PATHS:
            response = _send_raw(addr, path)
            assert b"HTTP/1.1 200" in response

    benchmark.pedantic(_run, rounds=100, iterations=1, warmup_rounds=10)
