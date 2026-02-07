"""Integration tests for pounce.worker — end-to-end request handling."""

import asyncio
import socket
import threading
import time

import pytest

from pounce._types import ASGIApp, Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.worker import Worker


# -- Test ASGI apps --------------------------------------------------------


async def hello_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Simple ASGI app that returns 'Hello, World!'."""
    assert scope["type"] == "http"
    await receive()  # Consume request body
    body = b"Hello, World!"
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })


async def echo_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that echoes the request path and method."""
    assert scope["type"] == "http"
    await receive()  # Consume request body
    body = f"{scope['method']} {scope['path']}".encode()
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({
        "type": "http.response.body",
        "body": body,
    })


async def streaming_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that streams response body in chunks."""
    assert scope["type"] == "http"
    await receive()
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            (b"content-type", b"text/plain"),
            (b"transfer-encoding", b"chunked"),
        ],
    })
    for i in range(3):
        await send({
            "type": "http.response.body",
            "body": f"chunk{i}".encode(),
            "more_body": i < 2,
        })


async def error_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that raises an exception."""
    raise RuntimeError("App crashed!")


# -- Helpers ---------------------------------------------------------------


def _start_worker(app: ASGIApp, config: ServerConfig) -> tuple[Worker, socket.socket, threading.Thread]:
    """Start a worker in a background thread, returning the worker and its socket."""
    sock = create_listener(config)
    worker = Worker(config, app, sock)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    time.sleep(0.15)  # Let the worker start accepting
    return worker, sock, thread


def _send_raw_request(
    addr: tuple[str, int],
    request: bytes,
    timeout: float = 2.0,
) -> bytes:
    """Send a raw HTTP request and return the full response."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(addr)
        sock.sendall(request)
        # Read response
        response = b""
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
            except socket.timeout:
                break
        return response
    finally:
        sock.close()


# -- Tests -----------------------------------------------------------------


class TestWorkerHelloWorld:
    """Basic request-response cycle through the worker."""

    def test_get_hello(self):
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = _start_worker(hello_app, config)
        addr = sock.getsockname()

        try:
            response = _send_raw_request(
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

    def test_method_and_path(self):
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = _start_worker(echo_app, config)
        addr = sock.getsockname()

        try:
            response = _send_raw_request(
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

    def test_chunked_response(self):
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = _start_worker(streaming_app, config)
        addr = sock.getsockname()

        try:
            response = _send_raw_request(
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

    def test_app_error_returns_500(self):
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = _start_worker(error_app, config)
        addr = sock.getsockname()

        try:
            response = _send_raw_request(
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

    def test_garbage_input(self):
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = _start_worker(hello_app, config)
        addr = sock.getsockname()

        try:
            response = _send_raw_request(
                addr,
                b"NOT A VALID REQUEST\r\n\r\n",
            )
            assert b"400" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()
