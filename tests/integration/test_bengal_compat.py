"""Bengal static-site compatibility test."""

from pounce.config import ServerConfig
from tests.conftest import send_raw_request, start_worker


def test_bengal_static_site_on_pounce() -> None:
    """A Bengal-shaped generated site should serve via pounce Worker."""
    from benchmarks.apps.bengal_static import app

    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    worker, sock, thread = start_worker(app, config)
    addr = sock.getsockname()

    try:
        # GET /
        resp_root = send_raw_request(
            addr, b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        assert b"HTTP/1.1" in resp_root
        assert b"200" in resp_root
        assert b"<h1>Bengal Fixture</h1>" in resp_root

        # GET /docs/
        resp_docs = send_raw_request(
            addr, b"GET /docs/ HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        assert b"200" in resp_docs
        assert b"<h1>Docs</h1>" in resp_docs

        # GET /assets/site.css
        resp_css = send_raw_request(
            addr,
            b"GET /assets/site.css HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
        )
        assert b"200" in resp_css
        assert b"color-scheme" in resp_css
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()
