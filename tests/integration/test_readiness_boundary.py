"""Deployment-boundary proof for the built-in readiness endpoint."""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from typing import Any

import pytest

from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listener, create_listeners
from pounce.supervisor import Supervisor
from pounce.worker import Worker
from tests.conftest import _wait_for_ready, send_raw_request, start_worker, with_lifespan


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


@pytest.mark.issue(308)
def test_async_full_drain_preserves_readiness_json_over_real_h1_socket() -> None:
    """A live async worker keeps GET/HEAD readiness semantics while draining."""
    entered = threading.Event()
    release = threading.Event()
    shutdown = threading.Event()
    slow_response: list[bytes] = []

    @with_lifespan
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        if scope["path"] == "/slow":
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        body = b"ok"
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        health_check_path="/readyz",
        shutdown_timeout=2.0,
        access_log=False,
    )
    sock = create_listener(config)
    addr = sock.getsockname()
    worker = Worker(config, app, sock, worker_id=7, shutdown_event=shutdown)
    worker_thread = threading.Thread(target=worker.run, daemon=True)
    slow_thread = threading.Thread(
        target=lambda: slow_response.append(
            send_raw_request(
                addr,
                b"GET /slow HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
                timeout=5.0,
            )
        ),
        daemon=True,
    )
    worker_thread.start()
    _wait_for_ready(addr)
    slow_thread.start()
    try:
        assert entered.wait(timeout=2.0)
        shutdown.set()
        worker.start_draining()

        get_response = send_raw_request(
            addr,
            b"GET /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
        )
        head_response = send_raw_request(
            addr,
            b"HEAD /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
        )
    finally:
        release.set()
        worker.shutdown()
        slow_thread.join(timeout=3.0)
        worker_thread.join(timeout=3.0)
        sock.close()

    get_status, get_headers, get_body = _parse_response(get_response)
    head_status, head_headers, head_body = _parse_response(head_response)
    payload = json.loads(get_body)
    assert get_status == head_status == b"HTTP/1.1 503 Service Unavailable"
    for name in (b"content-type", b"content-length", b"cache-control"):
        assert get_headers[name] == head_headers[name]
    assert payload["status"] == "draining"
    assert payload["worker_id"] == 7
    assert payload["active_connections"] >= 1
    assert head_body == b""
    assert slow_response
    assert b"HTTP/1.1 200" in slow_response[0]


@pytest.mark.issue(308)
def test_shared_multiworker_drain_preserves_readiness_json_over_real_h1_socket() -> None:
    """The sync accept distributor keeps readiness JSON for late probes."""
    app_http_calls = 0

    @with_lifespan
    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal app_http_calls
        app_http_calls += 1
        await receive()
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        workers=2,
        worker_mode="sync",
        health_check_path="/readyz",
        access_log=False,
    )
    sockets = create_listeners(config, 2, shared=True)
    addr = sockets[0].getsockname()
    supervisor = Supervisor(config, app, mode="thread")
    supervisor_thread = threading.Thread(
        target=supervisor.run,
        args=(sockets,),
        daemon=True,
    )
    supervisor_thread.start()
    _wait_for_ready(addr)
    try:
        deadline = time.monotonic() + 3.0
        drain_event = supervisor._accept_distributor_drain
        while drain_event is None and time.monotonic() < deadline:
            time.sleep(0.01)
            drain_event = supervisor._accept_distributor_drain
        assert drain_event is not None
        drain_event.set()
        get_response = send_raw_request(
            addr,
            b"GET /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
        )
        head_response = send_raw_request(
            addr,
            b"HEAD /readyz HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n",
        )
    finally:
        supervisor.shutdown()
        supervisor_thread.join(timeout=5.0)
        for listener in set(sockets):
            with contextlib.suppress(OSError):
                listener.close()

    get_status, get_headers, get_body = _parse_response(get_response)
    head_status, head_headers, head_body = _parse_response(head_response)
    payload = json.loads(get_body)
    assert get_status == head_status == b"HTTP/1.1 503 Service Unavailable"
    for name in (b"content-type", b"content-length", b"cache-control"):
        assert get_headers[name] == head_headers[name]
    assert payload == {
        "status": "draining",
        "uptime_seconds": payload["uptime_seconds"],
        "worker_id": 0,
        "active_connections": 0,
    }
    assert head_body == b""
    assert app_http_calls == 0
