"""Deployment-boundary proof for the built-in readiness endpoint."""

from __future__ import annotations

from typing import Any

from pounce.config import ServerConfig
from tests.conftest import send_raw_request, start_worker


def _parse_response(response: bytes) -> tuple[bytes, dict[bytes, bytes], bytes]:
    head, separator, body = response.partition(b"\r\n\r\n")
    assert separator
    lines = head.split(b"\r\n")
    headers = {
        name.strip().lower(): value.strip()
        for line in lines[1:]
        for name, separator, value in [line.partition(b":")]
        if separator
    }
    return lines[0], headers, body


def test_readiness_get_head_header_parity_over_real_h1_socket() -> None:
    app_http_calls = 0

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        nonlocal app_http_calls
        if scope["type"] == "http":
            app_http_calls += 1

    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        health_check_path="/readyz",
        access_log=False,
    )
    worker, sock, thread = start_worker(app, config=config)
    addr = sock.getsockname()
    try:
        get_response = send_raw_request(
            addr,
            b"GET /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
        )
        head_response = send_raw_request(
            addr,
            b"HEAD /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
        )
    finally:
        worker.shutdown()
        thread.join(timeout=3.0)
        sock.close()

    get_status, get_headers, get_body = _parse_response(get_response)
    head_status, head_headers, head_body = _parse_response(head_response)
    assert get_status == head_status
    assert get_status.startswith(b"HTTP/1.1 200")
    for name in (b"content-type", b"content-length", b"cache-control"):
        assert get_headers[name] == head_headers[name]
    assert get_body
    assert head_body == b""
    assert app_http_calls == 0
