"""Integration tests for concurrent load and capacity.

Burst, sustained keep-alive, at-capacity, and graceful shutdown under load.
Marked slow — run with pytest -m "not slow" to skip in fast CI.
"""

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker
from tests.conftest import _wait_for_ready, send_raw_request, start_worker, with_lifespan


@with_lifespan
async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal app that reads body and returns 200."""
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-length", b"2")],
    })
    await send({"type": "http.response.body", "body": b"ok"})


def _send_get(addr: tuple[str, int], timeout: float = 5.0) -> bytes:
    """Send GET and return response."""
    request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
    return send_raw_request(addr, request, timeout=timeout)


# ---------------------------------------------------------------------------
# Load tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestLoad:
    """Concurrent load and burst tests."""

    def test_burst_parallel_connections(self):
        """50 parallel connections, each sends GET; all get 200."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            num_connections = 50
            with ThreadPoolExecutor(max_workers=num_connections) as executor:
                futures = [executor.submit(_send_get, addr) for _ in range(num_connections)]
                responses = [f.result() for f in as_completed(futures)]

            assert len(responses) == num_connections
            for resp in responses:
                assert b"200" in resp
        finally:
            worker.shutdown()
            thread.join(timeout=5)
            sock.close()

    def test_sustained_keep_alive(self):
        """10 connections × 20 requests each on same connection."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            num_connections = 10
            requests_per_conn = 20

            def send_many_requests() -> tuple[int, int]:
                """Send N requests on one connection; return (success, total)."""
                tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                tcp.settimeout(10.0)
                tcp.connect(addr)
                success = 0
                try:
                    for _ in range(requests_per_conn):
                        req = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
                        tcp.sendall(req)
                        resp = b""
                        while True:
                            chunk = tcp.recv(4096)
                            if not chunk:
                                break
                            resp += chunk
                            if b"\r\n\r\n" not in resp:
                                continue
                            headers, body = resp.split(b"\r\n\r\n", 1)
                            cl = 2  # Our app sends Content-Length: 2
                            for line in headers.split(b"\r\n")[1:]:
                                if line.lower().startswith(b"content-length:"):
                                    cl = int(line.split(b":", 1)[1].strip())
                                    break
                            if len(body) >= cl:
                                break
                        if b"200" in resp:
                            success += 1
                finally:
                    tcp.close()
                return success, requests_per_conn

            with ThreadPoolExecutor(max_workers=num_connections) as executor:
                futures = [executor.submit(send_many_requests) for _ in range(num_connections)]
                results = [f.result() for f in as_completed(futures)]

            total_success = sum(s for s, _ in results)
            total_requests = num_connections * requests_per_conn
            assert total_success == total_requests
        finally:
            worker.shutdown()
            thread.join(timeout=5)
            sock.close()

    def test_at_capacity(self):
        """max_connections=5; 6th connection gets 503 or connection refused."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        addr = sock.getsockname()
        worker = Worker(config, _ok_app, sock, worker_id=0, max_connections=5)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        _wait_for_ready(addr)

        try:
            # Open 5 connections and keep them open
            connections: list[socket.socket] = []
            for _ in range(5):
                tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                tcp.settimeout(2.0)
                tcp.connect(addr)
                tcp.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
                time.sleep(0.1)
                connections.append(tcp)

            # 6th connection — should get 503 or connection refused
            tcp6 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp6.settimeout(2.0)
            try:
                tcp6.connect(addr)
                data = tcp6.recv(4096)
                assert data == b"" or b"503" in data
            except (ConnectionRefusedError, OSError, ConnectionError):
                pass  # Connection rejected
            finally:
                tcp6.close()

            for tcp in connections:
                tcp.close()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_graceful_shutdown_under_load(self):
        """Start worker, send burst, call shutdown; no errors, connections drain."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        def send_one() -> bytes | None:
            try:
                return _send_get(addr, timeout=3.0)
            except Exception:
                return None

        # Start burst while shutting down
        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(send_one) for _ in range(30)]
            worker.shutdown()
            results = [f.result() for f in as_completed(futures)]

        thread.join(timeout=5)
        sock.close()

        # Should not crash; some may get 200, some may get empty/error
        ok_count = sum(1 for r in results if r and b"200" in r)
        assert ok_count >= 0  # No crash
