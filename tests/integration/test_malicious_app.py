"""Integration tests for misbehaving ASGI app behavior.

Verifies that the server handles malicious or buggy apps without crashing:
- App never sends http.response.start
- App sends http.response.body before http.response.start
- App sends http.response.body twice with more_body=False
- App raises during send() while streaming
- App returns without calling send()
"""

from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from tests.conftest import send_raw_request, start_worker, with_lifespan

# ---------------------------------------------------------------------------
# Inline ASGI apps for malicious behavior testing
# ---------------------------------------------------------------------------


@with_lifespan
async def _never_sends_start_app(scope: Scope, receive: Receive, send: Send) -> None:
    """App consumes request but never sends http.response.start."""
    await receive()
    # Intentionally never call send() — server must handle


@with_lifespan
async def _body_before_start_app(scope: Scope, receive: Receive, send: Send) -> None:
    """App sends http.response.body before http.response.start."""
    await receive()
    await send({"type": "http.response.body", "body": b"evil", "more_body": False})


@with_lifespan
async def _double_more_body_false_app(scope: Scope, receive: Receive, send: Send) -> None:
    """App sends http.response.body twice with more_body=False."""
    await receive()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"first", "more_body": False})
    await send({"type": "http.response.body", "body": b"second", "more_body": False})


@with_lifespan
async def _raises_during_send_app(scope: Scope, receive: Receive, send: Send) -> None:
    """App raises during send() while streaming."""
    await receive()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"ok", "more_body": True})
    raise RuntimeError("Intentional crash during streaming")


@with_lifespan
async def _returns_without_send_app(scope: Scope, receive: Receive, send: Send) -> None:
    """App returns without ever calling send()."""
    await receive()
    # Intentionally return without sending — server must send 500 or close cleanly


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMaliciousAppBehavior:
    """Server handles misbehaving apps without crashing."""

    def test_app_never_sends_start_gets_500_or_closes(self):
        """App that never sends http.response.start — server sends 500 or closes."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_never_sends_start_app, config=config)
        addr = sock.getsockname()

        try:
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request, timeout=5.0)
            # Server must not crash; expect 500 or connection close
            assert response  # Got some response
            assert b"500" in response or b"Internal Server Error" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_app_sends_body_before_start_gets_500(self):
        """App sends http.response.body before http.response.start — server sends 500."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_body_before_start_app, config=config)
        addr = sock.getsockname()

        try:
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request, timeout=5.0)
            assert b"500" in response
            assert b"Internal Server Error" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_app_sends_body_twice_more_body_false_no_crash(self):
        """App sends http.response.body twice with more_body=False — no crash."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_double_more_body_false_app, config=config)
        addr = sock.getsockname()

        try:
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request, timeout=5.0)
            # Server must not crash; second body raises, expect 500
            assert response
            assert b"500" in response or b"first" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_app_raises_during_send_connection_closed_cleanly(self):
        """App raises during send() while streaming — connection closed cleanly."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_raises_during_send_app, config=config)
        addr = sock.getsockname()

        try:
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request, timeout=5.0)
            # Server sends 500 or partial response; must not hang
            assert response
            assert b"500" in response or b"Internal Server Error" in response or b"ok" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    def test_app_returns_without_send_gets_500_or_closes(self):
        """App returns without calling send() — server sends 500 or closes."""
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_returns_without_send_app, config=config)
        addr = sock.getsockname()

        try:
            request = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
            response = send_raw_request(addr, request, timeout=5.0)
            # Server must not crash; expect 500 or connection close
            assert response
            assert b"500" in response or b"Internal Server Error" in response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()
