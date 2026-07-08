"""Tests for :mod:`pounce.async_pool`.

The AsyncPool is the dedicated event loop that SyncWorkers hand off streaming
and WebSocket connections to. These tests exercise it without a live server by
wrapping a ``socket.socketpair()`` endpoint exactly as production does (via
``loop.connect_accepted_socket``), so the full ASGI -> protocol -> wire path is
covered end-to-end. The lifecycle helpers (serve loop, drain) are driven
directly with real asyncio tasks.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import socket
import threading
import time
from typing import Any

import pytest

from pounce._types import Receive, Scope, Send
from pounce.async_pool import (
    AsyncPool,
    StreamingHandoff,
    WebSocketHandoff,
    _create_h1_protocol,
)
from pounce.config import ServerConfig
from pounce.metrics import PrometheusCollector
from pounce.protocols._base import RequestReceived


async def _echo_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal HTTP app: echo the request body back in a 200 response."""
    msg = await receive()
    body = msg["body"]
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": b"hello-" + body, "more_body": False})


def _drain_socket(sock: socket.socket, timeout: float = 1.0) -> bytes:
    """Read everything the peer wrote until EOF (blocking, bounded by timeout)."""
    sock.setblocking(True)
    sock.settimeout(timeout)
    chunks: list[bytes] = []
    try:
        while True:
            data = sock.recv(8192)
            if not data:
                break
            chunks.append(data)
    except OSError:
        pass
    finally:
        sock.close()
    return b"".join(chunks)


def _streaming_handoff(
    conn: socket.socket,
    *,
    method: str = "GET",
    body: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    request_id: str | None = None,
) -> StreamingHandoff:
    return StreamingHandoff(
        conn=conn,
        scope={
            "type": "http",
            "method": method,
            "path": "/",
            "headers": headers if headers is not None else [(b"host", b"localhost")],
            "http_version": "1.1",
            "server": ("localhost", 80),
        },
        body=body,
        request_id=request_id,
    )


class TestHelpers:
    def test_create_h1_protocol_returns_handler(self) -> None:
        proto = _create_h1_protocol(max_incomplete_event_size=4096)
        # An h11-backed handler can serialise a response.
        assert proto.send_response(200, [(b"content-type", b"text/plain")]).startswith(b"HTTP/1.1")

    def test_streaming_handoff_is_frozen(self) -> None:
        s, c = socket.socketpair()
        try:
            h = _streaming_handoff(s)
            with pytest.raises(dataclasses.FrozenInstanceError):
                h.body = b"mutated"  # ty: ignore[invalid-assignment]
        finally:
            s.close()
            c.close()

    def test_websocket_handoff_carries_request(self) -> None:
        s, c = socket.socketpair()
        try:
            req = RequestReceived(method=b"GET", target=b"/ws", headers=(), http_version="1.1")
            h = WebSocketHandoff(
                conn=s,
                request=req,
                client=("127.0.0.1", 5555),
                server=("localhost", 80),
                scope={"type": "websocket", "path": "/ws"},
            )
            assert h.request is req
            assert h.client == ("127.0.0.1", 5555)
        finally:
            s.close()
            c.close()


class TestAccessors:
    def test_set_lifespan_state_is_passed_to_app(self) -> None:
        captured: dict[str, Any] = {}

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            captured["state"] = scope.get("state")
            await receive()
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})

        async def run() -> None:
            pool = AsyncPool(ServerConfig(compression=False), app)
            pool.set_lifespan_state({"db": "connected"})
            s, c = socket.socketpair()
            s.setblocking(False)
            try:
                await pool._handle_streaming_handoff(_streaming_handoff(s))
                _drain_socket(c)
            finally:
                with contextlib.suppress(OSError):
                    s.close()

        asyncio.run(run())
        assert captured["state"] == {"db": "connected"}

    def test_set_app_swaps_handler(self) -> None:
        pool = AsyncPool(ServerConfig(), _echo_app)

        async def other(scope: Scope, receive: Receive, send: Send) -> None:
            return None

        pool.set_app(other)
        assert pool._app is other

    def test_request_shutdown_sets_pool_event(self) -> None:
        pool = AsyncPool(ServerConfig(), _echo_app)
        assert not pool._pool_shutdown.is_set()
        pool.request_shutdown()
        assert pool._pool_shutdown.is_set()

    def test_accept_handoff_enqueues(self) -> None:
        pool = AsyncPool(ServerConfig(), _echo_app)
        s, c = socket.socketpair()
        try:
            handoff = _streaming_handoff(s)
            pool.accept_handoff(handoff)
            assert pool._queue.get_nowait() is handoff
        finally:
            s.close()
            c.close()


class TestStreamingHandoff:
    def test_runs_app_and_writes_response(self) -> None:
        async def run() -> bytes:
            pool = AsyncPool(ServerConfig(compression=False), _echo_app)
            s, c = socket.socketpair()
            s.setblocking(False)
            await pool._handle_streaming_handoff(
                _streaming_handoff(s, method="POST", body=b"world")
            )
            return _drain_socket(c)

        out = asyncio.run(run())
        assert b"HTTP/1.1 200" in out
        assert b"hello-world" in out

    def test_records_stream_lifecycle_for_sync_handoff(self) -> None:
        collector = PrometheusCollector()

        async def sse_app(scope: Scope, receive: Receive, send: Send) -> None:
            await receive()
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
                    "body": b"data: done\n\n",
                    "more_body": False,
                }
            )

        async def run() -> None:
            pool = AsyncPool(
                ServerConfig(compression=False),
                sse_app,
                lifecycle_collector=collector,
            )
            s, c = socket.socketpair()
            s.setblocking(False)
            try:
                handoff = dataclasses.replace(
                    _streaming_handoff(s),
                    worker_id=7,
                    connection_id=9,
                )
                await pool._handle_streaming_handoff(handoff)
                _drain_socket(c)
            finally:
                with contextlib.suppress(OSError):
                    s.close()

        asyncio.run(run())
        snapshot = collector.snapshot()
        assert snapshot["streams_active"] == 0
        assert snapshot["stream_duration_count"] == 1
        assert snapshot["duration_count"] == 1

    def test_negotiates_compression_when_accepted(self) -> None:
        async def big_app(scope: Scope, receive: Receive, send: Send) -> None:
            await receive()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"x" * 5000, "more_body": False})

        async def run() -> bytes:
            pool = AsyncPool(ServerConfig(compression=True, compression_min_size=10), big_app)
            s, c = socket.socketpair()
            s.setblocking(False)
            await pool._handle_streaming_handoff(
                _streaming_handoff(s, headers=[(b"accept-encoding", b"gzip")])
            )
            return _drain_socket(c)

        out = asyncio.run(run())
        assert b"content-encoding: gzip" in out.lower()

    def test_app_error_before_start_sends_500(self) -> None:
        async def boom(scope: Scope, receive: Receive, send: Send) -> None:
            await receive()
            raise RuntimeError("boom")

        async def run() -> bytes:
            pool = AsyncPool(ServerConfig(compression=False), boom)
            s, c = socket.socketpair()
            s.setblocking(False)
            await pool._handle_streaming_handoff(_streaming_handoff(s))
            return _drain_socket(c)

        out = asyncio.run(run())
        assert b"HTTP/1.1 500" in out
        assert b"Internal Server Error" in out

    def test_connect_failure_closes_socket(self) -> None:
        # A socket that is already closed cannot be wrapped; the handler must
        # swallow the error and close, never raise.
        async def run() -> None:
            pool = AsyncPool(ServerConfig(compression=False), _echo_app)
            s, c = socket.socketpair()
            c.close()
            s.close()  # closed fd -> connect_accepted_socket raises OSError
            await pool._handle_streaming_handoff(_streaming_handoff(s))

        asyncio.run(run())  # must not raise


class TestWebSocketHandoff:
    def test_completes_handshake(self) -> None:
        async def ws_app(scope: Scope, receive: Receive, send: Send) -> None:
            msg = await receive()
            if msg["type"] == "websocket.connect":
                await send({"type": "websocket.accept"})
            await send({"type": "websocket.close", "code": 1000})

        async def run() -> bytes:
            pool = AsyncPool(ServerConfig(), ws_app)
            s, c = socket.socketpair()
            s.setblocking(False)
            req = RequestReceived(
                method=b"GET",
                target=b"/ws",
                headers=(
                    (b"host", b"localhost"),
                    (b"upgrade", b"websocket"),
                    (b"connection", b"upgrade"),
                    (b"sec-websocket-key", b"dGhlIHNhbXBsZSBub25jZQ=="),
                    (b"sec-websocket-version", b"13"),
                ),
                http_version="1.1",
            )
            handoff = WebSocketHandoff(
                conn=s,
                request=req,
                client=("127.0.0.1", 5555),
                server=("localhost", 80),
                scope={"type": "websocket", "path": "/ws"},
            )
            await asyncio.wait_for(pool._handle_websocket_handoff(handoff), timeout=5)
            return _drain_socket(c)

        out = asyncio.run(run())
        assert b"101" in out
        assert b"switching" in out.lower()

    def test_connect_failure_closes_socket(self) -> None:
        async def run() -> None:
            pool = AsyncPool(ServerConfig(), _echo_app)
            s, c = socket.socketpair()
            c.close()
            s.close()
            req = RequestReceived(method=b"GET", target=b"/ws", headers=(), http_version="1.1")
            handoff = WebSocketHandoff(
                conn=s,
                request=req,
                client=("127.0.0.1", 5555),
                server=("localhost", 80),
                scope={"type": "websocket", "path": "/ws"},
            )
            await pool._handle_websocket_handoff(handoff)

        asyncio.run(run())  # must not raise


class TestHandoffDispatch:
    def test_dispatch_exception_closes_connection(self) -> None:
        # A malformed handoff (scope=None) raises inside the handler *after* the
        # socket is wrapped; the outer guard must log and close the conn.
        async def run() -> bool:
            pool = AsyncPool(ServerConfig(compression=False), _echo_app)
            s, c = socket.socketpair()
            s.setblocking(False)
            bad = StreamingHandoff(conn=s, scope=None, body=b"", request_id=None)  # ty: ignore[invalid-argument-type]
            await pool._handle_handoff_async(bad)
            c.close()
            try:
                s.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE)
                return False
            except OSError:
                return True

        assert asyncio.run(run()) is True


class TestServeLoop:
    def test_serve_starts_queued_handoff_then_retires(self) -> None:
        async def run() -> bytes:
            pool = AsyncPool(ServerConfig(compression=False, shutdown_timeout=1.0), _echo_app)
            s, c = socket.socketpair()
            s.setblocking(False)
            pool.accept_handoff(_streaming_handoff(s, body=b"x"))
            pool.request_shutdown()  # retire after draining the queue
            await asyncio.wait_for(pool._serve(), timeout=5)
            return _drain_socket(c)

        out = asyncio.run(run())
        assert b"HTTP/1.1 200" in out
        assert b"hello-x" in out

    def test_serve_self_retires_on_ext_shutdown_safety_deadline(self) -> None:
        # When the supervisor sets ext_shutdown but never calls request_shutdown,
        # the pool must still self-retire via the safety deadline (2x timeout).
        async def run() -> float:
            ext = threading.Event()
            pool = AsyncPool(
                ServerConfig(compression=False, shutdown_timeout=0.05),
                _echo_app,
                shutdown_event=ext,
            )
            ext.set()
            start = time.monotonic()
            await asyncio.wait_for(pool._serve(), timeout=5)
            return time.monotonic() - start

        elapsed = asyncio.run(run())
        # safety_deadline = 2 * 0.05 = 0.1s; comfortably under the 5s guard.
        assert elapsed < 3.0


class TestDrain:
    def test_drain_handoffs_noop_when_idle(self) -> None:
        async def run() -> None:
            pool = AsyncPool(ServerConfig(), _echo_app)
            await pool._drain_handoffs(0.01)  # nothing in flight -> returns at once

        asyncio.run(run())

    def test_drain_handoffs_cancels_stragglers(self) -> None:
        async def run() -> bool:
            pool = AsyncPool(ServerConfig(), _echo_app)
            started = asyncio.Event()

            async def forever() -> None:
                started.set()
                await asyncio.sleep(100)

            task = asyncio.create_task(forever())
            pool._handoff_tasks.add(task)
            task.add_done_callback(pool._handoff_tasks.discard)
            await started.wait()
            await pool._drain_handoffs(0.05)  # straggler exceeds timeout -> cancelled
            return task.cancelled()

        assert asyncio.run(run()) is True

    def test_drain_queued_handoffs_picks_up_late_arrival(self) -> None:
        async def run() -> bytes:
            pool = AsyncPool(ServerConfig(compression=False, shutdown_timeout=1.0), _echo_app)
            pool._loop = asyncio.get_running_loop()
            s, c = socket.socketpair()
            s.setblocking(False)
            pool.accept_handoff(_streaming_handoff(s, body=b"late"))
            await pool._drain_queued_handoffs(1.0)
            await pool._drain_handoffs(1.0)
            return _drain_socket(c)

        out = asyncio.run(run())
        assert b"hello-late" in out
