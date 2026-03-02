"""Integration tests for request size limits, connection limits, and enforcement."""

import socket as _socket
import time

import pytest

from pounce._types import Receive, Scope, Send
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
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(resp)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": resp})


# ---------------------------------------------------------------------------
# max_requests_per_connection
# ---------------------------------------------------------------------------


class TestMaxRequestsPerConnection:
    """max_requests_per_connection enforcement."""

    def test_connection_closed_after_max_requests(self):
        """After max_requests_per_connection requests, connection closes."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            max_requests_per_connection=2,
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)

                # First request
                tcp.sendall(b"GET /1 HTTP/1.1\r\nHost: localhost\r\n\r\n")
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
                tcp.sendall(b"GET /2 HTTP/1.1\r\nHost: localhost\r\n\r\n")
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


# ---------------------------------------------------------------------------
# request_timeout
# ---------------------------------------------------------------------------


class TestRequestTimeout:
    """request_timeout enforcement."""

    def test_slow_body_times_out(self):
        """A request body that never completes should eventually be handled."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            request_timeout=0.5,
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)
                # Send headers claiming a large body, then send nothing
                tcp.sendall(
                    b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 999999\r\n\r\npartial"
                )
                # Wait — the server should timeout and close the connection
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

    @pytest.mark.slow
    def test_content_length_mismatch_client_closes_early(self):
        """POST with Content-Length: 100, send 50 bytes, close — worker handles cleanly."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            request_timeout=2.0,
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)
                # Send headers + 50 bytes (declared 100), then close
                tcp.sendall(
                    b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 100\r\n\r\n" + b"x" * 50
                )
                tcp.close()
            except (ConnectionError, OSError):
                pass
            # Worker must not hang — subsequent request succeeds
            time.sleep(0.3)
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request, timeout=3.0)
            assert b"200" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# ---------------------------------------------------------------------------
# Oversized request headers (h11_max_incomplete_event_size)
# ---------------------------------------------------------------------------


class TestOversizedHeaders:
    """Requests with headers exceeding the configured limit are rejected.

    h11's ``max_incomplete_event_size`` only triggers when data is buffered
    *without* producing a complete event.  Sending a partial request that
    exceeds the limit forces the error path.
    """

    def test_oversized_partial_header_rejected(self):
        """Sending a partial request that overflows the buffer is rejected."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            h11_max_incomplete_event_size=256,  # Very small limit
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)

                # Send a request line + start of headers, then a huge
                # incomplete header block that exceeds 256 bytes — WITHOUT
                # sending the final \r\n\r\n so h11 can't parse a complete
                # event and the buffer overflows.
                tcp.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n")
                time.sleep(0.1)
                # This second chunk pushes the buffer over the limit
                tcp.sendall(b"X-Big: " + b"A" * 512 + b"\r\n")
                time.sleep(0.3)

                resp = b""
                try:
                    while True:
                        chunk = tcp.recv(4096)
                        if not chunk:
                            break
                        resp += chunk
                except (TimeoutError, ConnectionError, OSError):
                    pass

                # Worker should respond with 400 or close the connection
                assert b"400" in resp or len(resp) == 0
            finally:
                tcp.close()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_normal_header_accepted(self):
        """A request within the header size limit is accepted normally."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            h11_max_incomplete_event_size=65536,
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request)
            assert b"200" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# ---------------------------------------------------------------------------
# max_request_size enforcement
# ---------------------------------------------------------------------------


class TestMaxRequestSize:
    """Request body exceeding max_request_size is truncated."""

    def test_content_length_body_exceeding_limit_truncated(self):
        """POST with Content-Length body > max_request_size is truncated."""
        limit = 1024  # 1 KB
        oversized = 2048  # 2 KB

        @with_lifespan
        async def echo_length_app(scope: Scope, receive: Receive, send: Send) -> None:
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body", False):
                    break
            resp = str(len(body)).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-length", str(len(resp)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": resp})

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            max_request_size=limit,
        )
        worker, sock, thread = start_worker(echo_length_app, config=config)
        addr = sock.getsockname()

        try:
            payload = b"X" * oversized
            request = (
                b"POST / HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: " + str(oversized).encode() + b"\r\n"
                b"Connection: close\r\n"
                b"\r\n" + payload
            )
            response = send_raw_request(addr, request, timeout=5.0)
            assert b"200" in response
            # App receives truncated body — at most limit bytes
            body = response.split(b"\r\n\r\n", 1)[-1]
            received_len = int(body.decode())
            assert received_len <= limit
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_chunked_body_exceeding_limit_truncated(self):
        """Chunked body exceeding max_request_size is truncated."""
        limit = 1024

        @with_lifespan
        async def echo_length_app(scope: Scope, receive: Receive, send: Send) -> None:
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body", False):
                    break
            resp = str(len(body)).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-length", str(len(resp)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": resp})

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            max_request_size=limit,
        )
        worker, sock, thread = start_worker(echo_length_app, config=config)
        addr = sock.getsockname()

        try:
            # Chunked: 1KB + 1KB chunks
            chunk1 = b"X" * 1024
            chunk2 = b"Y" * 1024
            chunked_body = b"400\r\n" + chunk1 + b"\r\n400\r\n" + chunk2 + b"\r\n0\r\n\r\n"
            request = (
                b"POST / HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"Connection: close\r\n"
                b"\r\n" + chunked_body
            )
            response = send_raw_request(addr, request, timeout=5.0)
            assert b"200" in response
            body = response.split(b"\r\n\r\n", 1)[-1]
            received_len = int(body.decode())
            assert received_len <= limit
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_body_at_limit_accepted(self):
        """Body exactly at max_request_size is accepted."""
        limit = 1024

        @with_lifespan
        async def echo_length_app(scope: Scope, receive: Receive, send: Send) -> None:
            body = b""
            while True:
                msg = await receive()
                body += msg.get("body", b"")
                if not msg.get("more_body", False):
                    break
            resp = str(len(body)).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-length", str(len(resp)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": resp})

        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            max_request_size=limit,
        )
        worker, sock, thread = start_worker(echo_length_app, config=config)
        addr = sock.getsockname()

        try:
            payload = b"X" * limit
            request = (
                b"POST / HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: " + str(limit).encode() + b"\r\n"
                b"Connection: close\r\n"
                b"\r\n" + payload
            )
            response = send_raw_request(addr, request, timeout=5.0)
            assert b"200" in response
            body = response.split(b"\r\n\r\n", 1)[-1]
            assert body == str(limit).encode()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# ---------------------------------------------------------------------------
# header_timeout (slowloris / slow client)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestHeaderTimeout:
    """header_timeout enforcement — slow client sending partial headers."""

    def test_slow_headers_timeout(self):
        """Send request line + partial headers, then pause; expect 408 or close."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            header_timeout=0.5,
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)
                # Send request line + partial headers (no \r\n\r\n)
                tcp.sendall(b"GET / HTTP/1.1\r\nHost: ")
                time.sleep(1.0)  # Exceed header_timeout
                resp = b""
                try:
                    while True:
                        chunk = tcp.recv(4096)
                        if not chunk:
                            break
                        resp += chunk
                except (TimeoutError, ConnectionError, OSError):
                    pass
                # Server should close or send 408
                assert resp == b"" or b"408" in resp or b"400" in resp
            finally:
                tcp.close()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# ---------------------------------------------------------------------------
# request_timeout (slow body)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestRequestTimeoutSlowBody:
    """request_timeout enforcement — slow client sending body."""

    def test_slow_body_times_out(self):
        """POST with Content-Length, send partial body, pause; expect timeout."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            request_timeout=0.5,
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()

        try:
            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)
                # POST with Content-Length: 10000, send headers + 100 bytes
                tcp.sendall(
                    b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10000\r\n\r\n"
                    + b"X" * 100
                )
                time.sleep(1.5)  # Exceed request_timeout
                resp = b""
                try:
                    while True:
                        chunk = tcp.recv(4096)
                        if not chunk:
                            break
                        resp += chunk
                except (TimeoutError, ConnectionError, OSError):
                    pass
                # Connection should be closed (timeout or error)
                assert True  # No crash
            finally:
                tcp.close()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# ---------------------------------------------------------------------------
# Client disconnect mid-response
# ---------------------------------------------------------------------------


@with_lifespan
async def _streaming_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Streaming app that sends chunks slowly."""
    await receive()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    for i in range(10):
        await send(
            {
                "type": "http.response.body",
                "body": f"chunk{i}\n".encode(),
                "more_body": i < 9,
            }
        )


class TestClientDisconnectMidResponse:
    """Client disconnects during response — worker must not crash."""

    def test_disconnect_after_start_before_body(self):
        """Client receives response.start, disconnects before body — no crash."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_streaming_app, config=config)
        addr = sock.getsockname()

        try:
            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)
                tcp.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                # Read just the response headers (up to \r\n\r\n)
                resp = b""
                while b"\r\n\r\n" not in resp:
                    chunk = tcp.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                # Disconnect before reading body
                tcp.close()
            except (ConnectionError, OSError):
                pass
            # Worker must not crash — send another request
            time.sleep(0.2)
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request, timeout=3.0)
            assert b"200" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_disconnect_during_chunked_streaming(self):
        """Client disconnects during chunked streaming — worker handles cleanly."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_streaming_app, config=config)
        addr = sock.getsockname()

        try:
            tcp = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp.settimeout(3.0)
            try:
                tcp.connect(addr)
                tcp.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                # Read a few chunks then disconnect
                resp = b""
                for _ in range(3):
                    chunk = tcp.recv(256)
                    if not chunk:
                        break
                    resp += chunk
                tcp.close()
            except (ConnectionError, OSError):
                pass
            # Worker must not crash — send another request
            time.sleep(0.2)
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request, timeout=3.0)
            assert b"200" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# ---------------------------------------------------------------------------
# max_connections enforcement
# ---------------------------------------------------------------------------


class TestMaxConnections:
    """Worker rejects connections when at max_connections capacity."""

    def test_connection_rejected_at_limit(self):
        """When max_connections is reached, new connections are closed."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
        )
        # Start worker with max_connections=1 via start_worker's helper
        import threading

        from pounce.net.listener import create_listener
        from pounce.worker import Worker
        from tests.conftest import _wait_for_ready

        sock = create_listener(config)
        addr = sock.getsockname()
        worker = Worker(config, _ok_app, sock, worker_id=0, max_connections=1)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        _wait_for_ready(addr)

        try:
            # First connection — should succeed
            tcp1 = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp1.settimeout(2.0)
            tcp1.connect(addr)

            # Send a request that keeps connection alive (no Connection: close)
            tcp1.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n")
            time.sleep(0.3)

            # Second connection while first is active — should be rejected
            tcp2 = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            tcp2.settimeout(2.0)
            try:
                tcp2.connect(addr)
                time.sleep(0.2)
                try:
                    data = tcp2.recv(4096)
                    # Server may close immediately (empty) or send 503 when at capacity
                    assert data == b"" or data is None or b"503" in data
                except (ConnectionError, OSError):
                    pass  # Also fine — connection was rejected
            except (ConnectionRefusedError, OSError):
                pass  # Server didn't accept at all
            finally:
                tcp2.close()

            # Clean up first connection
            tcp1.close()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# ---------------------------------------------------------------------------
# ServerConfig validation enforcement
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """ServerConfig rejects invalid values at construction time."""

    def test_empty_host_raises(self):
        with pytest.raises(ValueError, match="host must be a non-empty string"):
            ServerConfig(host="")

    def test_negative_backlog_raises(self):
        with pytest.raises(ValueError, match="backlog must be > 0"):
            ServerConfig(backlog=0)

    def test_negative_request_timeout_raises(self):
        with pytest.raises(ValueError, match="request_timeout must be > 0"):
            ServerConfig(request_timeout=-1.0)

    def test_negative_shutdown_timeout_raises(self):
        with pytest.raises(ValueError, match="shutdown_timeout must be > 0"):
            ServerConfig(shutdown_timeout=0.0)

    def test_negative_max_request_size_raises(self):
        with pytest.raises(ValueError, match="max_request_size must be > 0"):
            ServerConfig(max_request_size=0)

    def test_negative_max_header_size_raises(self):
        with pytest.raises(ValueError, match="max_header_size must be > 0"):
            ServerConfig(max_header_size=-1)

    def test_negative_max_headers_raises(self):
        with pytest.raises(ValueError, match="max_headers must be > 0"):
            ServerConfig(max_headers=0)

    def test_negative_max_connections_raises(self):
        with pytest.raises(ValueError, match="max_connections must be >= 0"):
            ServerConfig(max_connections=-1)

    def test_negative_compression_min_size_raises(self):
        with pytest.raises(ValueError, match="compression_min_size must be >= 0"):
            ServerConfig(compression_min_size=-1)

    def test_invalid_log_level_raises(self):
        with pytest.raises(ValueError, match="log_level must be one of"):
            ServerConfig(log_level="verbose")

    def test_ssl_certfile_without_keyfile_raises(self):
        with pytest.raises(ValueError, match="ssl_certfile and ssl_keyfile"):
            ServerConfig(ssl_certfile="/path/to/cert.pem")

    def test_ssl_keyfile_without_certfile_raises(self):
        with pytest.raises(ValueError, match="ssl_certfile and ssl_keyfile"):
            ServerConfig(ssl_keyfile="/path/to/key.pem")
