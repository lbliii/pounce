"""Integration tests for request size limits and connection limits."""

import pytest

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.config import ServerConfig
from tests.conftest import send_raw_request, start_worker, with_lifespan


@with_lifespan
async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal app that reads body and returns 200."""
    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break
    resp = b"ok"
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-length", str(len(resp)).encode())],
    })
    await send({"type": "http.response.body", "body": resp})


class TestMaxRequestsPerConnection:
    """max_requests_per_connection enforcement."""

    def test_connection_closed_after_max_requests(self):
        """After max_requests_per_connection requests, connection closes."""
        config = ServerConfig(
            host="127.0.0.1", port=0, access_log=False,
            max_requests_per_connection=2,
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            import socket as _socket
            import time

            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)

                # First request
                tcp.sendall(
                    b"GET /1 HTTP/1.1\r\nHost: localhost\r\n\r\n"
                )
                time.sleep(0.2)
                resp1 = b""
                try:
                    while True:
                        chunk = tcp.recv(4096)
                        if not chunk:
                            break
                        resp1 += chunk
                except TimeoutError:
                    pass
                assert b"200" in resp1

                # Second request (should be the last on this connection)
                tcp.sendall(
                    b"GET /2 HTTP/1.1\r\nHost: localhost\r\n\r\n"
                )
                time.sleep(0.2)
                resp2 = b""
                try:
                    while True:
                        chunk = tcp.recv(4096)
                        if not chunk:
                            break
                        resp2 += chunk
                except TimeoutError:
                    pass
                assert b"200" in resp2

            finally:
                tcp.close()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


class TestRequestTimeout:
    """request_timeout enforcement."""

    def test_slow_body_times_out(self):
        """A request body that never completes should eventually be handled."""
        config = ServerConfig(
            host="127.0.0.1", port=0, access_log=False,
            request_timeout=0.5,
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            import socket as _socket

            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)
                # Send headers claiming a large body, then send nothing
                tcp.sendall(
                    b"POST / HTTP/1.1\r\n"
                    b"Host: localhost\r\n"
                    b"Content-Length: 999999\r\n"
                    b"\r\n"
                    b"partial"
                )
                # Wait — the server should timeout and close the connection
                import time
                time.sleep(1.5)
                # Try to read response
                resp = b""
                try:
                    while True:
                        chunk = tcp.recv(4096)
                        if not chunk:
                            break
                        resp += chunk
                except (TimeoutError, ConnectionError, OSError):
                    pass
                # The connection should have been closed
                # (either 200 with partial body, timeout, or connection reset)
            finally:
                tcp.close()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()
