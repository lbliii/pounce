"""Integration tests for static files configured through ServerConfig."""

from __future__ import annotations

import httpx

from pounce._config_file import load_config_with_overrides
from pounce._static import StaticFiles, StaticMount
from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.testing import TestServer
from tests.conftest import send_raw_request, start_worker, with_lifespan


@with_lifespan
async def _fallback_app(scope: Scope, receive: Receive, send: Send) -> None:
    await receive()
    body = f"fallback:{scope['path']}".encode()
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


def test_serverconfig_static_files_serve_through_real_server(tmp_path) -> None:
    """ServerConfig.static_files reaches the real Server/Worker request path."""
    public = tmp_path / "public"
    public.mkdir()
    (public / "index.html").write_text("<h1>Bengal</h1>")
    (public / "style.css").write_text("body { color: red; }")

    with TestServer(
        _fallback_app,
        static_files={"/": str(public)},
        static_cache_control="public, max-age=0",
    ) as server:
        root = httpx.get(f"{server.url}/")
        css = httpx.get(f"{server.url}/style.css")
        missing = httpx.get(f"{server.url}/missing")

    assert root.status_code == 200
    assert root.text == "<h1>Bengal</h1>"
    assert root.headers["cache-control"] == "public, max-age=0"
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert css.text == "body { color: red; }"
    assert missing.status_code == 200
    assert missing.text == "fallback:/missing"


def test_toml_static_files_serve_through_real_server(tmp_path) -> None:
    """TOML [static_files] feeds ServerConfig.static_files into dispatch."""
    public = tmp_path / "public"
    public.mkdir()
    (public / "assets").mkdir()
    (public / "assets" / "app.js").write_text("console.log('ok')")
    (tmp_path / "pounce.toml").write_text(
        f'static_cache_control = "public, max-age=120"\n'
        f"[static_files]\n"
        f'"/assets" = "{public / "assets"}"\n'
    )
    config_values = load_config_with_overrides({}, config_path=tmp_path / "pounce.toml")

    with TestServer(_fallback_app, **config_values) as server:
        asset = httpx.get(f"{server.url}/assets/app.js")
        fallback = httpx.get(f"{server.url}/dynamic")

    assert asset.status_code == 200
    assert asset.text == "console.log('ok')"
    assert asset.headers["cache-control"] == "public, max-age=120"
    assert fallback.status_code == 200
    assert fallback.text == "fallback:/dynamic"


def test_http1_static_content_length_body_stays_inside_h11(tmp_path) -> None:
    """HTTP/1 static sendfile keeps h11 Content-Length accounting consistent."""
    public = tmp_path / "public"
    public.mkdir()
    body = b"console.log('pounce')"
    (public / "app.js").write_bytes(body)
    app = StaticFiles(mounts=[StaticMount("/", public)])
    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    worker, sock, thread = start_worker(app, config=config)
    addr = sock.getsockname()

    try:
        response = send_raw_request(
            addr,
            b"GET /app.js HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
        )
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

    head, _, received_body = response.partition(b"\r\n\r\n")
    assert b"HTTP/1.1 200" in head
    assert f"content-length: {len(body)}".encode() in head.lower()
    assert received_body == body
