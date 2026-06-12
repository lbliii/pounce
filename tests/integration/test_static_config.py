"""Integration tests for static files configured through ServerConfig."""

from __future__ import annotations

import socket
import time

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


def test_http1_static_sendfile_survives_slow_client_backpressure(tmp_path) -> None:
    """A slow reader on a large static file must not crash the sendfile worker.

    Regression test for issue #72: the zero-copy sendfile path used a raw
    ``os.sendfile`` loop in an executor with no EAGAIN handling, so a slow or
    disconnecting client filling the kernel send buffer crashed the worker
    mid-response with an uncaught ``BlockingIOError``.  ``loop.sendfile`` now
    handles the back-pressure via the selector, so the full file transfers.
    """
    public = tmp_path / "public"
    public.mkdir()
    # Comfortably larger than SO_SNDBUF so the send buffer fills mid-transfer.
    body = (bytes(range(256)) * 4) * 4096  # 4 MiB, deterministic content
    (public / "big.bin").write_bytes(body)

    app = StaticFiles(mounts=[StaticMount("/", public)])
    # Compression off + plain HTTP is the exact profile that takes the
    # sendfile path (worker.py advertises pounce.sendfile only then).
    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    worker, sock, thread = start_worker(app, config=config)
    addr = sock.getsockname()

    received = b""
    head = b""
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(30.0)
        client.connect(addr)
        client.sendall(b"GET /big.bin HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        # Read slowly so the kernel send buffer fills faster than it drains,
        # forcing the sendfile path to handle EAGAIN back-pressure.
        while True:
            try:
                chunk = client.recv(8192)
            except TimeoutError:
                break
            if not chunk:
                break
            received += chunk
            if not head and b"\r\n\r\n" in received:
                head, _, received = received.partition(b"\r\n\r\n")
            time.sleep(0.0005)
        client.close()
    finally:
        worker.shutdown()
        thread.join(timeout=5)
        sock.close()

    assert thread.is_alive() is False  # worker exited cleanly, did not hang/crash
    assert b"HTTP/1.1 200" in head
    assert f"content-length: {len(body)}".encode() in head.lower()
    assert received == body  # full file, no truncation


def _read_http1_response(sock: socket.socket, timeout: float = 5.0) -> tuple[bytes, bytes]:
    """Read exactly one HTTP/1.1 keep-alive response: ``(head, body)``.

    Reads headers, then exactly ``Content-Length`` body bytes, leaving any
    subsequent pipelined data in the kernel buffer for the next call.
    """
    sock.settimeout(timeout)
    buf = b""
    # Read until end of headers.
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise AssertionError("connection closed before headers completed")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")

    # Parse Content-Length.
    content_length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            content_length = int(line.split(b":", 1)[1].strip())
            break

    body = rest
    while len(body) < content_length:
        chunk = sock.recv(65536)
        if not chunk:
            raise AssertionError("connection closed before body completed")
        body += chunk
    return head, body[:content_length]


def test_http1_static_sendfile_keepalive_no_stall(tmp_path) -> None:
    """Many sequential keep-alive requests over the sendfile path must not stall.

    Regression test for the Bengal ~9.5 RPS anomaly (issue #126), which was a
    keep-alive sendfile stall: the pre-#73 code ran a raw ``os.sendfile`` loop
    against the fd asyncio still owned on the selector, corrupting the
    transport read state so the connection wedged after 1-2 requests. The fix
    (``loop.sendfile``, #73) is already in main; this locks it.

    The file is sized above ``_static._SENDFILE_MIN_SIZE`` so the request
    actually takes the zero-copy sendfile path (issue #127 makes tiny files use
    read()+write() instead), and no ``Connection: close`` is sent so a single
    socket carries every request.
    """
    public = tmp_path / "public"
    public.mkdir()
    # 64 KiB: comfortably above the sendfile minimum-size gate (16 KiB) so the
    # response is served via the zero-copy sendfile path.
    body = bytes(range(256)) * 256  # 65536 bytes, deterministic
    (public / "page.html").write_bytes(body)

    app = StaticFiles(mounts=[StaticMount("/", public)])
    # Compression off + plain HTTP is the exact profile that advertises
    # pounce.sendfile in the worker.
    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    worker, sock, thread = start_worker(app, config=config)
    addr = sock.getsockname()

    n_requests = 100
    request = b"GET /page.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
    elapsed = 0.0
    try:
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # A tight per-response timeout: a keep-alive stall (the regression)
        # would block here instead of streaming the next response promptly.
        client.settimeout(5.0)
        client.connect(addr)

        start = time.perf_counter()
        for i in range(n_requests):
            client.sendall(request)
            head, received = _read_http1_response(client)
            assert b"HTTP/1.1 200" in head, f"request {i} did not return 200: {head!r}"
            assert b"connection: close" not in head.lower(), (
                f"request {i} closed the keep-alive connection"
            )
            assert received == body, f"request {i} body mismatch/truncation"
        elapsed = time.perf_counter() - start
        client.close()
    finally:
        worker.shutdown()
        thread.join(timeout=5)
        sock.close()

    assert thread.is_alive() is False  # worker exited cleanly
    # Sustained throughput sanity bound: 100 sequential sendfile responses on a
    # single keep-alive connection over loopback complete well under a second
    # in practice. A generous 10s ceiling still catches the regression's
    # per-request multi-second stalls (the anomaly was ~9.5 RPS == >10s here)
    # without being flaky under CI load.
    assert elapsed < 10.0, f"100 keep-alive sendfile requests took {elapsed:.2f}s (possible stall)"
