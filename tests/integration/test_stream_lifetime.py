"""Long-lived HTTP stream lifetime contract (issue #238)."""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from typing import Any

import pytest

from pounce.config import ServerConfig
from pounce.metrics import PrometheusCollector
from pounce.net.listener import create_listener
from pounce.worker import Worker


class _DrainAwareSSEApp:
    def __init__(self) -> None:
        self._close_stream: asyncio.Event | None = None
        self.draining_scope: dict[str, Any] | None = None
        self.shutdown_seen = threading.Event()
        self.stream_worker_id: int | None = None

    async def __call__(self, scope, receive, send) -> None:
        scope_type = scope["type"]
        if scope_type == "pounce.worker.startup":
            return
        if scope_type == "pounce.worker.draining":
            self.draining_scope = dict(scope)
            assert self._close_stream is not None
            self._close_stream.set()
            return
        if scope_type == "pounce.worker.shutdown":
            self.shutdown_seen.set()
            return

        await receive()
        self.stream_worker_id = scope["extensions"]["pounce.worker"]["worker_id"]
        self._close_stream = asyncio.Event()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"event: ready\ndata: 1\n\n",
                "more_body": True,
            }
        )
        await self._close_stream.wait()
        await send(
            {
                "type": "http.response.body",
                "body": b"event: pounce-close\ndata: reload\n\n",
                "more_body": False,
            }
        )


def _read_until(sock: socket.socket, marker: bytes, *, timeout: float = 3.0) -> bytes:
    deadline = time.monotonic() + timeout
    data = b""
    while marker not in data and time.monotonic() < deadline:
        data += sock.recv(4096)
    return data


@pytest.mark.issue(238)
def test_sse_survives_idle_timers_then_closes_observably_on_reload() -> None:
    app = _DrainAwareSSEApp()
    collector = PrometheusCollector()
    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        access_log=False,
        header_timeout=0.1,
        request_timeout=0.1,
        keep_alive_timeout=0.1,
        write_timeout=0.1,
        shutdown_timeout=2.0,
    )
    listener = create_listener(config)
    worker = Worker(config, app, listener, lifecycle_collector=collector)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()

    client = socket.create_connection(listener.getsockname(), timeout=3.0)
    client.settimeout(3.0)
    try:
        client.sendall(b"GET /events HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        initial = _read_until(client, b"event: ready")
        assert b"HTTP/1.1 200" in initial
        assert b"text/event-stream" in initial

        # A silent, already-open response is active work, not header, body,
        # keep-alive, or blocked-write idleness.
        time.sleep(0.4)
        assert collector.snapshot()["streams_active"] == 1

        started = time.monotonic()
        worker.start_draining()
        final = _read_until(client, b"event: pounce-close")
        thread.join(timeout=config.shutdown_timeout + 1.0)

        assert b"event: pounce-close" in final
        assert not thread.is_alive()
        assert time.monotonic() - started < config.shutdown_timeout + 1.0
        assert app.draining_scope == {
            "type": "pounce.worker.draining",
            "worker_id": 0,
            "generation": 0,
            "reason": "reload",
            "timeout": config.shutdown_timeout,
        }
        assert app.shutdown_seen.is_set()
        assert app.stream_worker_id == app.draining_scope["worker_id"]

        snapshot = collector.snapshot()
        assert snapshot["streams_active"] == 0
        assert snapshot["stream_duration_count"] == 1
        metrics = collector.export()
        assert "http_streams_active 0" in metrics
        assert "http_stream_duration_seconds_count 1" in metrics
    finally:
        client.close()
        if thread.is_alive():
            worker.shutdown()
            thread.join(timeout=3.0)
        listener.close()
