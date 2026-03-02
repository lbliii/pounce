"""
Bengal static-site compatibility test — verifies static-only sites run on pounce.

Mirrors benchmarks/test_chirp_compat.py for static sites. No Bengal dependency;
fixture mimics Bengal SSG output structure (index.html, docs/, style.css, icon.svg).

"""

import pytest

from pounce import create_static_handler
from pounce.config import ServerConfig
from tests.conftest import send_raw_request, start_worker


@pytest.fixture
def bengal_like_dir(tmp_path):
    """Create directory mimicking Bengal SSG output structure."""
    (tmp_path / "index.html").write_text("<h1>Hello World</h1>")
    (tmp_path / "style.css").write_text("body { color: red; }")
    (tmp_path / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.html").write_text("<h1>Docs</h1>")
    return tmp_path


def test_bengal_static_site_on_pounce(bengal_like_dir) -> None:
    """A Bengal-like static site should serve via pounce Worker."""
    app = create_static_handler({"/": str(bengal_like_dir)})
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
        assert b"<h1>Hello World</h1>" in resp_root

        # GET /docs/
        resp_docs = send_raw_request(
            addr, b"GET /docs/ HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        assert b"200" in resp_docs
        assert b"<h1>Docs</h1>" in resp_docs

        # GET /style.css
        resp_css = send_raw_request(
            addr, b"GET /style.css HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"
        )
        assert b"200" in resp_css
        assert b"body { color: red; }" in resp_css
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()
