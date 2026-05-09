"""Integration smoke tests for the Chirp/LB Sonic-shaped workload."""

from pounce.config import ServerConfig
from tests.conftest import send_raw_request, start_worker


def test_chirp_forum_tenant_html_form_static_and_sse() -> None:
    from benchmarks.apps.chirp_forum import app

    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    worker, sock, thread = start_worker(app, config)
    addr = sock.getsockname()

    try:
        home = send_raw_request(
            addr, b"GET / HTTP/1.1\r\nHost: alpha.example\r\nConnection: close\r\n\r\n"
        )
        assert b"HTTP/1.1 200" in home
        assert b"Alpha Company" in home
        assert b"x-chirp-middleware: forum" in home.lower()

        thread_resp = send_raw_request(
            addr,
            b"GET /threads/1 HTTP/1.1\r\nHost: beta.example\r\nConnection: close\r\n\r\n",
        )
        assert b"HTTP/1.1 200" in thread_resp
        assert b"Session zero planning" in thread_resp

        post = send_raw_request(
            addr,
            (
                b"POST /threads/1/reply HTTP/1.1\r\n"
                b"Host: beta.example\r\n"
                b"Content-Type: application/x-www-form-urlencoded\r\n"
                b"Content-Length: 14\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"body=Scout+now"
            ),
        )
        assert b"HTTP/1.1 200" in post
        assert b"Scout now" in post

        css = send_raw_request(
            addr,
            b"GET /assets/forum.css HTTP/1.1\r\nHost: beta.example\r\nConnection: close\r\n\r\n",
        )
        assert b"HTTP/1.1 200" in css
        assert b"text/css" in css

        sse = send_raw_request(
            addr,
            b"GET /events HTTP/1.1\r\nHost: alpha.example\r\nConnection: close\r\n\r\n",
        )
        assert b"HTTP/1.1 200" in sse
        assert b"text/event-stream" in sse
        assert b"Alpha Company" in sse
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()
