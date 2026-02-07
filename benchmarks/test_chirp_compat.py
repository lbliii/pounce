"""
Chirp compatibility test — verifies a chirp app runs on pounce without modification.

Checks off the Phase 1 success criterion: "Chirp hello-world app runs
without modification."

Skipped if chirp is not installed.

"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from pounce.config import ServerConfig
from pounce.worker import Worker

# Skip the entire module if chirp is not importable
chirp = pytest.importorskip("chirp")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUEST = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"


def _send_raw_request(addr: tuple[str, int], request: bytes, timeout: float = 3.0) -> bytes:
    """Send a raw HTTP request and return the full response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(addr)
        sock.sendall(request)
        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            except TimeoutError:
                break
        return response
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.benchmark
@pytest.mark.timeout(15)
def test_chirp_hello_world_on_pounce() -> None:
    """A minimal chirp App should serve HTTP through a pounce Worker."""
    # Build a minimal chirp application
    app = chirp.App()

    @app.route("/")
    def index() -> str:
        return "Hello from chirp!"

    # Start a pounce worker with the chirp app
    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)
    addr = sock.getsockname()

    worker = Worker(config, app, sock, worker_id=0)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    time.sleep(0.3)

    try:
        response = _send_raw_request(addr, _REQUEST)

        # Verify we got a valid HTTP response
        assert b"HTTP/1.1" in response, f"No HTTP response: {response[:200]}"
        assert b"200" in response, f"Expected 200 status: {response[:200]}"
        assert b"Hello from chirp!" in response, f"Missing chirp body: {response[:500]}"

        print("\n  [chirp compat] chirp App served successfully via pounce Worker")
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()
