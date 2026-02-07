"""Integration tests for pounce.worker — end-to-end request handling."""

from pounce._types import ASGIApp
from pounce.config import ServerConfig

from tests.conftest import send_raw_request, start_worker


class TestWorkerHelloWorld:
    """Basic request-response cycle through the worker."""

    def test_get_hello(self, hello_app: ASGIApp):
        worker, sock, thread = start_worker(hello_app)
        addr = sock.getsockname()

        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            assert b"Hello, World!" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


class TestWorkerEcho:
    """Worker passes correct request info to the ASGI app."""

    def test_method_and_path(self, echo_app: ASGIApp):
        worker, sock, thread = start_worker(echo_app)
        addr = sock.getsockname()

        try:
            response = send_raw_request(
                addr,
                b"GET /api/users HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            assert b"GET /api/users" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


class TestWorkerStreaming:
    """Streaming responses are delivered chunk by chunk."""

    def test_chunked_response(self, streaming_app: ASGIApp):
        worker, sock, thread = start_worker(streaming_app)
        addr = sock.getsockname()

        try:
            response = send_raw_request(
                addr,
                b"GET /stream HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            assert b"chunk0" in response
            assert b"chunk1" in response
            assert b"chunk2" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


class TestWorkerErrorHandling:
    """Worker handles ASGI app exceptions gracefully."""

    def test_app_error_returns_500(self, error_app: ASGIApp):
        worker, sock, thread = start_worker(error_app)
        addr = sock.getsockname()

        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"500" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


class TestWorkerMalformedRequest:
    """Worker handles malformed HTTP gracefully."""

    def test_garbage_input(self, hello_app: ASGIApp):
        worker, sock, thread = start_worker(hello_app)
        addr = sock.getsockname()

        try:
            response = send_raw_request(
                addr,
                b"NOT A VALID REQUEST\r\n\r\n",
            )
            assert b"400" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()
