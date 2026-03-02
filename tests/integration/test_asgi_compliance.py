"""ASGI 3.0 compliance tests for pounce.

Validates pounce against the ASGI 3.0 HTTP Connection Scope spec and
Lifespan spec. These tests exercise the real pounce worker end-to-end,
sending raw HTTP and asserting that scope construction, request body
delivery, response protocol, keep-alive, and lifespan behavior all
conform to the specification.

References:
    - https://asgi.readthedocs.io/en/stable/specs/www.html
    - https://asgi.readthedocs.io/en/stable/specs/lifespan.html

"""

import asyncio
import json
import socket

import pytest

from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from tests.conftest import send_raw_request, start_worker

# ---------------------------------------------------------------------------
# Inline ASGI apps for compliance testing
# ---------------------------------------------------------------------------


async def _scope_inspector_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Returns the full ASGI scope as JSON (minus non-serializable bits)."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await receive()

    # Serialize scope — convert bytes to strings for JSON
    serializable: dict = {}
    for key, value in scope.items():
        if key == "headers":
            serializable[key] = [[h[0].decode("latin-1"), h[1].decode("latin-1")] for h in value]
        elif isinstance(value, bytes):
            serializable[key] = value.decode("latin-1")
        elif isinstance(value, tuple):
            serializable[key] = list(value)
        else:
            serializable[key] = value

    body = json.dumps(serializable).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _body_echo_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Reads the full request body and echoes it back."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/octet-stream"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _method_echo_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Returns the HTTP method from the scope."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await receive()
    body = scope["method"].encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _receive_inspector_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Returns the raw receive() message as JSON to validate structure."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    message = await receive()
    # Serialize: convert bytes to latin-1 string for JSON
    serializable = {}
    for key, value in message.items():
        if isinstance(value, bytes):
            serializable[key] = value.decode("latin-1")
        else:
            serializable[key] = value

    body = json.dumps(serializable).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _keepalive_counter_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Returns a simple 200 for counting keep-alive requests."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await receive()
    body = b"ok"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _head_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Handles HEAD requests properly — headers but no body."""
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    await receive()
    # HEAD should still send headers with content-length
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", b"13"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b""})


async def _lifespan_shutdown_failed_app(
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    """App that fails during lifespan shutdown."""
    if scope["type"] != "lifespan":
        await receive()
        body = b"ok"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
        return

    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send(
                {
                    "type": "lifespan.shutdown.failed",
                    "message": "cleanup error",
                }
            )
            return


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_body(response: bytes) -> bytes:
    """Extract body from raw HTTP response (split on double CRLF)."""
    parts = response.split(b"\r\n\r\n", 1)
    return parts[1] if len(parts) > 1 else b""


def _parse_status(response: bytes) -> int:
    """Extract status code from raw HTTP response."""
    first_line = response.split(b"\r\n", 1)[0]
    return int(first_line.split(b" ", 2)[1])


def _parse_scope(response: bytes) -> dict:
    """Parse the JSON scope from a scope_inspector_app response."""
    body = _parse_body(response)
    # Handle chunked transfer-encoding
    if b"\r\n" in body and not body.startswith(b"{"):
        # Chunked: size\r\ndata\r\n...
        chunks = []
        remaining = body
        while remaining:
            size_end = remaining.find(b"\r\n")
            if size_end == -1:
                break
            size = int(remaining[:size_end], 16)
            if size == 0:
                break
            chunks.append(remaining[size_end + 2 : size_end + 2 + size])
            remaining = remaining[size_end + 2 + size + 2 :]
        body = b"".join(chunks)
    return json.loads(body)


def _send_persistent(
    addr: tuple[str, int],
    *requests: bytes,
    timeout: float = 2.0,
) -> bytes:
    """Send multiple requests over a single TCP connection (keep-alive)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(addr)
        response = b""
        for request in requests:
            sock.sendall(request)
            # Small delay to ensure response arrives
            import time

            time.sleep(0.1)
            try:
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response += chunk
            except TimeoutError:
                pass
        return response
    finally:
        sock.close()


# =========================================================================
# 1. HTTP Scope Completeness
# =========================================================================


class TestHTTPScopeCompleteness:
    """ASGI 3.0 spec: HTTP connection scope must contain required keys."""

    def test_scope_has_all_required_keys(self):
        """Every required scope key is present per ASGI HTTP spec."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET /test HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            required = {
                "type",
                "asgi",
                "http_version",
                "method",
                "path",
                "raw_path",
                "query_string",
                "root_path",
                "headers",
                "server",
                "client",
            }
            assert required.issubset(scope.keys()), f"Missing scope keys: {required - scope.keys()}"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_scope_type_is_http(self):
        """scope['type'] must be 'http' for HTTP requests."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            assert scope["type"] == "http"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_asgi_version(self):
        """scope['asgi']['version'] must be '3.0'."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            assert scope["asgi"]["version"] == "3.0"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_http_version(self):
        """scope['http_version'] must be '1.1' for HTTP/1.1 requests."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            assert scope["http_version"] == "1.1"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_path_is_url_decoded(self):
        """scope['path'] must be URL-decoded."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET /hello%20world HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            assert scope["path"] == "/hello world"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_raw_path_is_not_decoded(self):
        """scope['raw_path'] must be the original bytes, not decoded."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET /hello%20world HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            assert scope["raw_path"] == "/hello%20world"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_query_string_is_raw_bytes(self):
        """scope['query_string'] must be raw (not decoded) bytes."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET /search?q=hello%20world&page=1 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            # query_string should be the raw string, not URL-decoded
            assert scope["query_string"] == "q=hello%20world&page=1"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_empty_query_string(self):
        """scope['query_string'] is empty bytes when no query string."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET /no-query HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            assert scope["query_string"] == ""
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_root_path_default_empty(self):
        """scope['root_path'] defaults to empty string."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            assert scope["root_path"] == ""
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_root_path_from_config(self):
        """scope['root_path'] reflects server config."""
        config = ServerConfig(
            host="127.0.0.1",
            port=0,
            access_log=False,
            root_path="/api/v1",
        )
        worker, sock, thread = start_worker(_scope_inspector_app, config=config)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            assert scope["root_path"] == "/api/v1"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_headers_are_byte_pairs(self):
        """scope['headers'] must be a list of [name, value] byte pairs."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Custom: test-value\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            headers = scope["headers"]
            assert isinstance(headers, list)
            # Find our custom header
            custom = [h for h in headers if h[0] == "x-custom"]
            assert len(custom) == 1
            assert custom[0][1] == "test-value"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_headers_are_lowercased(self):
        """Header names must be lowercased per ASGI spec."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Mixed-Case: value\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            header_names = [h[0] for h in scope["headers"]]
            # All header names should be lowercase
            for name in header_names:
                assert name == name.lower(), f"Header name not lowercased: {name}"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_client_is_tuple(self):
        """scope['client'] must be a [host, port] pair."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            client = scope["client"]
            assert isinstance(client, list)
            assert len(client) == 2
            assert isinstance(client[0], str)
            assert isinstance(client[1], int)
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_server_is_tuple(self):
        """scope['server'] must be a [host, port] pair."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            server = scope["server"]
            assert isinstance(server, list)
            assert len(server) == 2
            assert isinstance(server[0], str)
            assert isinstance(server[1], int)
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_scheme_is_http(self):
        """scope['scheme'] must be 'http' for non-TLS connections."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            scope = _parse_scope(response)
            assert scope["scheme"] == "http"
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# =========================================================================
# 2. HTTP Methods
# =========================================================================


class TestHTTPMethods:
    """ASGI must correctly pass all standard HTTP methods in the scope."""

    @pytest.mark.parametrize("method", [b"GET", b"POST", b"PUT", b"DELETE", b"PATCH", b"OPTIONS"])
    def test_method_in_scope(self, method: bytes):
        """Each HTTP method is correctly reflected in scope['method']."""
        worker, sock, thread = start_worker(_method_echo_app)
        addr = sock.getsockname()
        try:
            request = method + b" / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request)
            body = _parse_body(response)
            assert body == method
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_head_request(self):
        """HEAD request produces correct scope and server returns headers."""
        worker, sock, thread = start_worker(_head_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"HEAD / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            # HEAD response should have headers but body may be empty
            assert b"content-type" in response.lower()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# =========================================================================
# 3. Request Body Protocol
# =========================================================================


class TestRequestBody:
    """ASGI receive() must deliver http.request messages correctly."""

    def test_get_has_empty_body(self):
        """GET requests deliver body=b'' and more_body=False."""
        worker, sock, thread = start_worker(_receive_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            body = _parse_body(response)
            msg = json.loads(body)
            assert msg["type"] == "http.request"
            assert msg["body"] == ""  # empty bytes serialized as empty string
            assert msg["more_body"] is False
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_post_body_echo(self):
        """POST request body is delivered to the app and can be read."""
        worker, sock, thread = start_worker(_body_echo_app)
        addr = sock.getsockname()
        try:
            payload = b"hello=world&foo=bar"
            request = (
                b"POST /submit HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                b"Content-Type: application/x-www-form-urlencoded\r\n"
                b"Connection: close\r\n"
                b"\r\n" + payload
            )
            response = send_raw_request(addr, request)
            assert b"200" in response
            # The echoed payload appears somewhere in the response body
            assert payload in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_expect_100_continue(self):
        """Request with Expect: 100-continue receives a valid response.

        Server may send 100 Continue before body, or go straight to final
        response. Either way, client must not hang.
        """
        worker, sock, thread = start_worker(_body_echo_app)
        addr = sock.getsockname()
        try:
            payload = b"hello"
            request = (
                b"POST / HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Expect: 100-continue\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                b"Connection: close\r\n"
                b"\r\n" + payload
            )
            response = send_raw_request(addr, request, timeout=3.0)
            assert b"HTTP/1.1" in response
            # Either 100 Continue or 200 OK — server must respond
            assert b"100" in response or b"200" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_large_body(self):
        """Large request bodies are delivered correctly."""
        worker, sock, thread = start_worker(_body_echo_app)
        addr = sock.getsockname()
        try:
            # 64KB body — larger than typical buffer sizes
            payload = b"X" * 65536
            request = (
                b"POST / HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                b"Connection: close\r\n"
                b"\r\n" + payload
            )
            response = send_raw_request(addr, request, timeout=5.0)
            assert b"200" in response
            # Verify the full payload was echoed back (appears in response)
            assert payload in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_receive_message_has_required_keys(self):
        """receive() messages must have 'type', 'body', and 'more_body'."""
        worker, sock, thread = start_worker(_receive_inspector_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            body = _parse_body(response)
            msg = json.loads(body)
            assert "type" in msg
            assert "body" in msg
            assert "more_body" in msg
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_put_body(self):
        """PUT request body is delivered correctly."""
        worker, sock, thread = start_worker(_body_echo_app)
        addr = sock.getsockname()
        try:
            payload = b'{"key": "value"}'
            request = (
                b"PUT /resource HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                b"Content-Type: application/json\r\n"
                b"Connection: close\r\n"
                b"\r\n" + payload
            )
            response = send_raw_request(addr, request)
            assert b"200" in response
            assert payload in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_streaming_body_multiple_chunks(self):
        """Request body arriving in multiple socket reads is reassembled."""
        worker, sock, thread = start_worker(_body_echo_app)
        addr = sock.getsockname()
        try:
            # Send a body large enough that it likely spans multiple reads
            # but still fits in a single Content-Length declaration
            payload = b"A" * 4096 + b"B" * 4096 + b"C" * 4096
            request = (
                b"POST /upload HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                b"Connection: close\r\n"
                b"\r\n" + payload
            )
            response = send_raw_request(addr, request, timeout=5.0)
            assert b"200" in response
            assert payload in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# =========================================================================
# 4. Response Protocol
# =========================================================================


class TestResponseProtocol:
    """ASGI response protocol compliance."""

    def test_response_start_then_body(self):
        """Normal response: http.response.start followed by http.response.body."""
        worker, sock, thread = start_worker(_keepalive_counter_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            status = _parse_status(response)
            assert status == 200
            assert b"ok" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_streaming_more_body_true(self):
        """more_body=True keeps the response open for additional chunks."""

        async def streaming_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return
            await receive()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            for i in range(5):
                await send(
                    {
                        "type": "http.response.body",
                        "body": f"part{i}".encode(),
                        "more_body": i < 4,
                    }
                )

        worker, sock, thread = start_worker(streaming_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            for i in range(5):
                assert f"part{i}".encode() in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_more_body_false_completes_response(self):
        """more_body=False (or omitted) completes the response."""
        worker, sock, thread = start_worker(_keepalive_counter_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            # Response should be complete — we got data back
            assert b"200" in response
            assert b"ok" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_status_codes_preserved(self):
        """Various HTTP status codes are passed through correctly."""

        async def status_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return
            await receive()
            # Return 201 Created
            await send(
                {
                    "type": "http.response.start",
                    "status": 201,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"created"})

        worker, sock, thread = start_worker(status_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
            )
            assert b"201" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_custom_response_headers(self):
        """Response headers set by the app are sent to the client."""

        async def header_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return
            await receive()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"x-custom-header", b"custom-value"),
                        (b"content-length", b"2"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

        worker, sock, thread = start_worker(header_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"x-custom-header: custom-value" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# =========================================================================
# 5. Keep-Alive
# =========================================================================


class TestKeepAlive:
    """HTTP/1.1 keep-alive: multiple requests on one TCP connection."""

    def test_multiple_requests_same_connection(self):
        """Two sequential requests on a single keep-alive connection."""
        worker, sock, thread = start_worker(_scope_inspector_app)
        addr = sock.getsockname()

        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.settimeout(3.0)
        try:
            tcp.connect(addr)

            # First request — keep-alive (default for HTTP/1.1)
            tcp.sendall(b"GET /first HTTP/1.1\r\nHost: localhost\r\n\r\n")
            import time

            time.sleep(0.3)
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
            assert b"/first" in resp1

            # Second request on same connection
            tcp.sendall(b"GET /second HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            time.sleep(0.3)
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
            assert b"/second" in resp2

        finally:
            tcp.close()
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_connection_close_header(self):
        """Connection: close terminates the connection after the response."""
        worker, sock, thread = start_worker(_keepalive_counter_app)
        addr = sock.getsockname()

        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.settimeout(2.0)
        try:
            tcp.connect(addr)
            tcp.sendall(b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            response = b""
            try:
                while True:
                    chunk = tcp.recv(4096)
                    if not chunk:
                        break
                    response += chunk
            except TimeoutError:
                pass

            assert b"200" in response

            # Connection should be closed — further recv should get nothing
            try:
                extra = tcp.recv(4096)
                assert extra == b""  # Connection closed by server
            except (ConnectionError, OSError, TimeoutError):
                pass  # Also acceptable — connection is dead

        finally:
            tcp.close()
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_post_then_get_same_connection(self):
        """POST with body followed by GET on a single keep-alive connection."""
        worker, sock, thread = start_worker(_body_echo_app)
        addr = sock.getsockname()

        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp.settimeout(3.0)
        try:
            tcp.connect(addr)

            # First request — POST with body (keep-alive)
            payload = b"keepalive-post-body"
            tcp.sendall(
                b"POST /first HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                b"\r\n" + payload
            )
            import time

            time.sleep(0.3)
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
            assert payload in resp1

            # Second request — GET on same connection (Connection: close)
            tcp.sendall(b"GET /second HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            time.sleep(0.3)
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
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# =========================================================================
# 6. Error Handling
# =========================================================================


class TestErrorHandling:
    """Server handles error conditions per ASGI spec."""

    def test_app_exception_returns_500(self):
        """Unhandled app exception produces a 500 response."""

        async def crash_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return
            raise ValueError("intentional crash")

        worker, sock, thread = start_worker(crash_app)
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

    def test_malformed_request_returns_400(self):
        """Malformed HTTP produces a 400 response."""
        worker, sock, thread = start_worker(_keepalive_counter_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(addr, b"GARBAGE\r\n\r\n")
            assert b"400" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# =========================================================================
# 7. Lifespan Protocol
# =========================================================================


class TestLifespanCompliance:
    """ASGI lifespan protocol compliance."""

    def test_lifespan_scope_structure(self):
        """Lifespan scope must have type='lifespan' and asgi version."""
        from pounce.asgi.lifespan import run_lifespan

        captured_scope: dict = {}

        async def capture_scope_app(
            scope: Scope,
            receive: Receive,
            send: Send,
        ) -> None:
            if scope["type"] == "lifespan":
                captured_scope.update(scope)
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return

        async def _test() -> None:
            config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
            async with run_lifespan(capture_scope_app, config):
                pass

        asyncio.run(_test())

        assert captured_scope["type"] == "lifespan"
        assert "asgi" in captured_scope
        assert captured_scope["asgi"]["version"] == "3.0"

    def test_lifespan_startup_fires_before_serving(self):
        """Server must complete lifespan startup before accepting requests.

        Verified via the lifespan protocol: run_lifespan() must complete
        startup before yielding (which is when the server starts accepting).
        """
        from pounce.asgi.lifespan import run_lifespan

        events: list[str] = []

        async def ordered_app(
            scope: Scope,
            receive: Receive,
            send: Send,
        ) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        events.append("startup")
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return

        async def _test() -> None:
            config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
            async with run_lifespan(ordered_app, config):
                # At this point, startup must have already completed
                events.append("serving")

        asyncio.run(_test())

        assert events[0] == "startup"
        assert events[1] == "serving"

    def test_no_lifespan_app_works(self):
        """Apps that don't support lifespan should still work."""

        async def no_lifespan_app(
            scope: Scope,
            receive: Receive,
            send: Send,
        ) -> None:
            if scope["type"] == "lifespan":
                raise NotImplementedError("No lifespan")
            await receive()
            body = b"works"
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})

        worker, sock, thread = start_worker(no_lifespan_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            assert b"works" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_lifespan_startup_failed(self):
        """lifespan.startup.failed must prevent the server from serving."""

        async def failing_app(
            scope: Scope,
            receive: Receive,
            send: Send,
        ) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send(
                            {
                                "type": "lifespan.startup.failed",
                                "message": "database unavailable",
                            }
                        )
                        return
                return
            await receive()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [],
                }
            )
            await send({"type": "http.response.body", "body": b"should not reach"})

        from pounce._errors import LifespanError
        from pounce.asgi.lifespan import run_lifespan

        async def _test() -> None:
            config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
            with pytest.raises(LifespanError, match="database unavailable"):
                async with run_lifespan(failing_app, config):
                    pass  # Should not reach here

        asyncio.run(_test())


# =========================================================================
# 9. Compression + Request Body Combinations (Phase 4)
# =========================================================================


class TestCompressionWithBody:
    """Compressed responses with request bodies present."""

    def test_post_body_with_gzip_response(self):
        """POST body is read correctly and response is gzip-compressed."""
        import gzip

        worker, sock, thread = start_worker(
            _body_echo_app,
            config=ServerConfig(
                host="127.0.0.1",
                port=0,
                access_log=False,
                compression=True,
                compression_min_size=1,
            ),
        )
        addr = sock.getsockname()
        try:
            # Send a POST with body and Accept-Encoding: gzip
            payload = b"compressed-echo-test-payload" * 20
            request = (
                b"POST /echo HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                b"Accept-Encoding: gzip\r\n"
                b"Connection: close\r\n"
                b"\r\n" + payload
            )
            response = send_raw_request(addr, request)
            assert b"200" in response
            assert b"content-encoding: gzip" in response.lower()

            # Extract the gzip body after the blank line separator
            body_start = response.find(b"\r\n\r\n")
            assert body_start != -1
            raw_body = response[body_start + 4 :]
            # The body may be chunked — strip chunk framing if present
            if b"transfer-encoding: chunked" in response.lower():
                # Simple dechunk: find the actual gzip data
                # Chunks are: size\r\ndata\r\n...0\r\n\r\n
                dechunked = b""
                pos = 0
                while pos < len(raw_body):
                    end = raw_body.find(b"\r\n", pos)
                    if end == -1:
                        break
                    chunk_size = int(raw_body[pos:end], 16)
                    if chunk_size == 0:
                        break
                    dechunked += raw_body[end + 2 : end + 2 + chunk_size]
                    pos = end + 2 + chunk_size + 2
                raw_body = dechunked

            decompressed = gzip.decompress(raw_body)
            assert decompressed == payload
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_post_body_no_accept_encoding_uncompressed(self):
        """POST without Accept-Encoding returns uncompressed response."""
        worker, sock, thread = start_worker(
            _body_echo_app,
            config=ServerConfig(
                host="127.0.0.1",
                port=0,
                access_log=False,
                compression=True,
            ),
        )
        addr = sock.getsockname()
        try:
            payload = b"no-compression-test"
            request = (
                b"POST /echo HTTP/1.1\r\n"
                b"Host: localhost\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n"
                b"Connection: close\r\n"
                b"\r\n" + payload
            )
            response = send_raw_request(addr, request)
            assert b"200" in response
            assert b"content-encoding" not in response.lower()
            assert payload in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()


# =========================================================================
# 10. CRLF Injection Prevention (Production-Grade Security)
# =========================================================================


class TestCRLFInjectionPrevention:
    """Malicious app headers with CRLF must not inject extra headers in response."""

    def test_crlf_in_header_value_not_injected(self):
        """App returns header value with CRLF; response must not contain injected header."""

        async def malicious_headers_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return
            await receive()
            # Attempt CRLF injection: value contains \r\n that could split into X-Injected
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"content-length", b"2"),
                        (b"x-custom", b"value\r\nX-Injected: evil"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

        worker, sock, thread = start_worker(malicious_headers_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            # Injected header must NOT appear as separate header line
            assert b"\r\nX-Injected:" not in response
            assert b"\r\nx-injected:" not in response.lower()
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_crlf_in_header_name_sanitized(self):
        """App returns header name with CRLF; name is sanitized, no injection."""

        async def malicious_name_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
                return
            await receive()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/plain"),
                        (b"content-length", b"2"),
                        (b"x-custom\r\nX-Injected", b"evil"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": b"ok"})

        worker, sock, thread = start_worker(malicious_name_app)
        addr = sock.getsockname()
        try:
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            assert b"\r\nX-Injected:" not in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()
