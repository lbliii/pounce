"""
Smoke tests for example apps.

Imports each example, starts a pounce worker, sends one HTTP request,
and asserts a 200 response.  No throughput measurement — just "does it
work".  If an example breaks after an API change, this catches it.

"""

import subprocess
import tomllib
from pathlib import Path

import pytest

from tests.conftest import send_raw_request, start_worker

# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------

_GET = b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

_GET_SSE = (
    b"GET / HTTP/1.1\r\nHost: localhost\r\nAccept: text/event-stream\r\nConnection: close\r\n\r\n"
)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_hello_example() -> None:
    """examples/hello.py returns 200 with Hello, World!"""
    from examples.hello import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"Hello, World!" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_lifespan_example() -> None:
    """examples/lifespan.py returns 200 with a request counter."""
    from examples.lifespan import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"request #" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(15)
def test_streaming_sse_example() -> None:
    """examples/streaming_sse.py returns 200 with SSE events."""
    import socket as _socket

    from examples.streaming_sse import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        # SSE streams indefinitely so we can't use send_raw_request (it reads
        # until EOF).  Instead, read a few chunks then close the socket.
        conn = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        conn.settimeout(5.0)
        conn.connect(addr)
        conn.sendall(_GET_SSE)

        response = b""
        for _ in range(5):
            chunk = conn.recv(4096)
            if not chunk:
                break
            response += chunk
            # Stop once we have enough to verify.
            if b"event: heartbeat" in response:
                break

        conn.close()

        assert b"HTTP/1.1 200" in response
        assert b"text/event-stream" in response
        assert b"event: heartbeat" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_compression_demo_example() -> None:
    """examples/compression_demo.py returns 200 with JSON payload."""
    from examples.compression_demo import app
    from pounce.config import ServerConfig

    # Disable compression so we can verify the raw JSON body
    config = ServerConfig(host="127.0.0.1", port=0, access_log=False, compression=False)
    worker, sock, thread = start_worker(app, config)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"application/json" in response
        assert b"pounce" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_cpu_parallel_example() -> None:
    """examples/cpu_parallel.py returns 200 with a hash digest."""
    from examples.cpu_parallel import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b'"digest"' in response
        assert b'"iterations"' in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_factory_app_example() -> None:
    """examples/factory_app.py create_app() returns a working ASGI app."""
    from examples.factory_app import create_app

    app = create_app()
    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"Hello from factory!" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_factory_app_via_importer() -> None:
    """import_app() resolves factory pattern 'module:create_app()' correctly."""
    from pounce._importer import import_app

    app = import_app("examples.factory_app:create_app()")
    assert callable(app)

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"Hello from factory!" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(15)
def test_chirp_app_example() -> None:
    """examples/chirp_app.py returns 200 with chirp response."""
    chirp = pytest.importorskip("chirp")  # noqa: F841

    from examples.chirp_app import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1" in response
        assert b"200" in response
        assert b"Hello from chirp" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_websocket_echo_http_fallback() -> None:
    """examples/websocket_echo.py returns 426 for plain HTTP requests."""
    from examples.websocket_echo import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"426" in response
        assert b"WebSocket" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_programmatic_server_example() -> None:
    """examples/programmatic_server.py app returns 200."""
    from examples.programmatic_server import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"Hello from programmatic server!" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_production_server_root() -> None:
    """examples/production_server.py GET / returns 200 with the features JSON."""
    from examples.production_server import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"Hello from production pounce!" in response
        # The dead "sentry" claim must not be advertised in the live response.
        assert b"sentry" not in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_production_server_health() -> None:
    """examples/production_server.py GET /health returns 200 healthy."""
    from examples.production_server import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    request = b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

    try:
        response = send_raw_request(addr, request)
        assert b"HTTP/1.1 200" in response
        assert b"healthy" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_websocket_chat_serves_html() -> None:
    """examples/websocket_chat.py GET / returns 200 with the chat HTML page."""
    from examples.websocket_chat import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"text/html" in response
        assert b"pounce chat" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_file_upload_serves_html() -> None:
    """examples/file_upload.py GET / returns 200 with the upload form."""
    from examples.file_upload import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"text/html" in response
        assert b"file upload" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_file_upload_post() -> None:
    """examples/file_upload.py POST /upload returns 200 with byte stats."""
    from examples.file_upload import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    body = b"hello pounce upload test"
    request = (
        b"POST /upload HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"Content-Type: application/octet-stream\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"Connection: close\r\n"
        b"\r\n" + body
    )

    try:
        response = send_raw_request(addr, request)
        assert b"HTTP/1.1 200" in response
        assert b'"bytes_received"' in response
        assert b'"chunks"' in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_mini_router_index() -> None:
    """examples/mini_router.py GET / returns 200 with routes JSON."""
    from examples.mini_router import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        response = send_raw_request(addr, _GET)
        assert b"HTTP/1.1 200" in response
        assert b"application/json" in response
        assert b"mini_router" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_mini_router_user() -> None:
    """examples/mini_router.py GET /users/42 returns 200 with user data."""
    from examples.mini_router import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    request = b"GET /users/42 HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

    try:
        response = send_raw_request(addr, request)
        assert b"HTTP/1.1 200" in response
        assert b"Douglas Adams" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_mini_router_404() -> None:
    """examples/mini_router.py GET /nonexistent returns 404."""
    from examples.mini_router import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    request = b"GET /nonexistent HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

    try:
        response = send_raw_request(addr, request)
        assert b"HTTP/1.1 404" in response
        assert b"not found" in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


# ---------------------------------------------------------------------------
# Railway / PaaS deploy example (#151)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_railway_deploy_reads_port_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """examples/railway_deploy.py build_config() binds 0.0.0.0 on $PORT."""
    from examples.railway_deploy import build_config

    monkeypatch.setenv("PORT", "9137")
    config = build_config()

    assert config.host == "0.0.0.0"
    assert config.port == 9137  # reads the injected PORT
    assert config.health_check_path == "/readyz"
    assert config.workers == 2
    assert config.shutdown_timeout == 10
    assert config.log_format == "json"
    # Platform-terminated TLS: no in-container certs.
    assert config.ssl_certfile is None
    assert config.ssl_keyfile is None
    # Proxy trust OFF by default (forwarded headers stripped).
    assert not config.trusted_hosts


@pytest.mark.timeout(10)
def test_railway_deploy_health(monkeypatch: pytest.MonkeyPatch) -> None:
    """examples/railway_deploy.py GET /readyz returns 200 readiness JSON.

    Serves with the example's documented health_check_path so the built-in
    healthcheck path is exercised (port overridden to 0 for the test bind).
    """
    from dataclasses import replace

    from examples.railway_deploy import app, build_config

    monkeypatch.setenv("PORT", "8000")
    config = replace(build_config(), host="127.0.0.1", port=0, access_log=False)
    worker, sock, thread = start_worker(app, config)
    addr = sock.getsockname()

    request = b"GET /readyz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n"

    try:
        response = send_raw_request(addr, request)
        assert b"HTTP/1.1 200" in response
        assert b'"status": "ok"' in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_railway_deploy_strips_untrusted_forwarded() -> None:
    """Untrusted X-Forwarded-Proto is stripped: app sees scheme 'http'.

    Enforces the example's documented trust boundary — with trusted_hosts empty
    (the example default), forwarded headers from an untrusted peer must not
    influence the scope.
    """
    from examples.railway_deploy import app

    # Default config => trusted_hosts empty => forwarded headers stripped.
    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    request = (
        b"GET / HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"X-Forwarded-Proto: https\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )

    try:
        response = send_raw_request(addr, request)
        assert b"HTTP/1.1 200" in response
        # Spoofed X-Forwarded-Proto must be ignored: scheme stays http.
        assert b'"scheme":"http"' in response
        assert b'"scheme":"https"' not in response
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_railway_probe_reports_build_identity_and_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The public canary exposes only explicit build identity and a finite SSE proof."""
    from examples.railway_deploy import app

    monkeypatch.setenv("POUNCE_DEPLOYMENT_CHANNEL", "main-canary")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
    monkeypatch.setenv("RAILWAY_GIT_BRANCH", "main")
    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    try:
        root = send_raw_request(
            addr,
            b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
        )
        assert b'"channel":"main-canary"' in root
        assert b'"git_commit":"abc123"' in root
        assert b'"git_branch":"main"' in root
        assert b'"pounce_version":' in root
        assert b'"python_version":' in root

        stream = send_raw_request(
            addr,
            b"GET /stream HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
        )
        assert b"content-type: text/event-stream" in stream.lower()
        assert stream.count(b"event: canary\n") == 2
        assert b'"git_commit":"abc123"' in stream
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.issue(248)
@pytest.mark.issue(291)
def test_railway_recipe_assets_encode_the_deployment_contract() -> None:
    """Docker and Railway config keep 3.14t, readiness, and drain aligned."""
    recipe = Path(__file__).parents[2] / "examples" / "deploy" / "railway"
    dockerfile = (recipe / "Dockerfile").read_text(encoding="utf-8")
    canary_dockerfile = (recipe / "Dockerfile.canary").read_text(encoding="utf-8")
    start = (recipe / "start.sh").read_text(encoding="utf-8")
    with (recipe / "railway.toml").open("rb") as file:
        railway = tomllib.load(file)

    assert "FROM python:3.14-slim" in dockerfile
    assert "ARG POUNCE_VERSION=0.9.0" in dockerfile
    assert "UV_PYTHON_INSTALL_DIR=/opt/uv-python" in dockerfile
    assert "uv venv --managed-python --python 3.14t" in dockerfile
    assert '/opt/venv/bin/python -c "import sys; assert not sys._is_gil_enabled()"' in dockerfile
    assert "PYTHON_GIL=0" in dockerfile
    assert "USER pounce" in dockerfile
    assert "COPY src /src/src" in canary_dockerfile
    assert "uv pip install --python /opt/venv/bin/python /src" in canary_dockerfile
    assert "bengal-pounce==" not in canary_dockerfile
    assert "examples/deploy/railway/app.py" in canary_dockerfile
    assert "assert not sys._is_gil_enabled()" in start
    assert "${PORT:?Railway must provide PORT}" in start

    assert railway["build"] == {
        "builder": "DOCKERFILE",
        "dockerfilePath": "Dockerfile",
    }
    deploy = railway["deploy"]
    assert deploy["healthcheckPath"] == "/readyz"
    assert type(deploy["drainingSeconds"]) is int
    assert type(deploy["overlapSeconds"]) is int
    assert deploy["drainingSeconds"] == 15
    assert deploy["overlapSeconds"] == 5
    assert deploy["numReplicas"] == 1

    subprocess.run(["sh", "-n", str(recipe / "start.sh")], check=True)


@pytest.mark.issue(248)
def test_railway_smoke_accepts_current_cli_deployment_json() -> None:
    """The remote smoke runner accepts current list and wrapped JSON shapes."""
    from examples.deploy.railway.smoke import _deployment_items

    deployment = {"id": "dep-1", "status": "SUCCESS"}
    assert _deployment_items(json_payload := '[{"id":"dep-1","status":"SUCCESS"}]') == [
        deployment
    ], json_payload
    assert _deployment_items('{"deployments":[{"id":"dep-1","status":"SUCCESS"}]}') == [deployment]


def test_railway_canary_probe_matches_only_the_full_expected_commit() -> None:
    from examples.deploy.railway.canary_probe import _matches_expected_commit, _verify_runtime

    sha = "a" * 40
    payload = {
        "status": "ok",
        "channel": "main-canary",
        "gil_enabled": False,
        "git_commit": sha,
        "pounce_version": "0.9.0",
        "python_version": "3.14.3",
    }
    assert _matches_expected_commit(payload, sha)
    assert not _matches_expected_commit(payload, "a" * 39)
    _verify_runtime(payload, sha)


def test_railway_main_canary_workflow_is_public_and_main_scoped() -> None:
    workflow = (Path(__file__).parents[2] / ".github/workflows/railway-main-canary.yml").read_text(
        encoding="utf-8"
    )

    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "github.repository == 'lbliii/pounce'" in workflow
    assert "${{ github.sha }}" in workflow
    assert "canary_probe.py" in workflow
    assert "pounce-railway-smoke-production.up.railway.app" in workflow
    assert "secrets." not in workflow


# ---------------------------------------------------------------------------
# Host-based multi-tenant routing example (#152)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_multi_tenant_routes_by_host() -> None:
    """examples/multi_tenant_app.py: different Host values yield different tenants."""
    from examples.multi_tenant_app import app

    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    alpha = b"GET / HTTP/1.1\r\nHost: alpha.example\r\nConnection: close\r\n\r\n"
    beta = b"GET / HTTP/1.1\r\nHost: beta.example\r\nConnection: close\r\n\r\n"

    try:
        resp_alpha = send_raw_request(addr, alpha)
        resp_beta = send_raw_request(addr, beta)
        assert b"HTTP/1.1 200" in resp_alpha
        assert b"Alpha Company" in resp_alpha
        assert b"HTTP/1.1 200" in resp_beta
        assert b"Beta Company" in resp_beta
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_multi_tenant_ignores_untrusted_forwarded_host() -> None:
    """Spoofed X-Forwarded-Host from an untrusted peer is NOT honored.

    With trusted_hosts empty (the safe default), the forwarded header is
    stripped and the tenant resolves from the real Host (alpha.example), not the
    spoofed beta.example.
    """
    from examples.multi_tenant_app import app

    # start_worker default config => trusted_hosts empty.
    worker, sock, thread = start_worker(app)
    addr = sock.getsockname()

    request = (
        b"GET / HTTP/1.1\r\n"
        b"Host: alpha.example\r\n"
        b"X-Forwarded-Host: beta.example\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )

    try:
        response = send_raw_request(addr, request)
        assert b"HTTP/1.1 200" in response
        assert b"Alpha Company" in response  # real Host wins
        assert b"Beta Company" not in response  # spoof ignored
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()


@pytest.mark.timeout(10)
def test_multi_tenant_honors_trusted_forwarded_host() -> None:
    """A trusted peer's X-Forwarded-Host DOES drive tenant selection.

    With the direct peer (127.0.0.1) in trusted_hosts, pounce rewrites the scope
    Host from X-Forwarded-Host before dispatch, so the forwarded beta.example
    resolves to the Beta tenant even though the direct Host is alpha.example.
    """
    from examples.multi_tenant_app import app
    from pounce.config import ServerConfig

    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        access_log=False,
        trusted_hosts=frozenset({"127.0.0.1"}),
    )
    worker, sock, thread = start_worker(app, config)
    addr = sock.getsockname()

    request = (
        b"GET / HTTP/1.1\r\n"
        b"Host: alpha.example\r\n"
        b"X-Forwarded-Host: beta.example\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )

    try:
        response = send_raw_request(addr, request)
        assert b"HTTP/1.1 200" in response
        assert b"Beta Company" in response  # forwarded host honored
    finally:
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()
