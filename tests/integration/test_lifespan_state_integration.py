"""
Integration tests for lifespan state injection into request scopes.

"""

import asyncio
import contextlib
import socket

from pounce.config import ServerConfig
from pounce.worker import Worker


async def test_request_scope_has_lifespan_state():
    """Test that request scopes contain the lifespan state."""
    request_scope_seen = None

    async def app(scope, receive, send):
        nonlocal request_scope_seen

        if scope["type"] == "http":
            # Store the scope for assertion
            request_scope_seen = scope

            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"OK",
                }
            )

    # Create test socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)

    config = ServerConfig()
    worker = Worker(config, app, sock)

    # Set lifespan state
    lifespan_state = {"db": "mock_db", "config": {"setting": "value"}}
    worker.set_lifespan_state(lifespan_state)

    # Create a simple HTTP request
    request_data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"

    async def make_request():
        reader, writer = await asyncio.open_connection("127.0.0.1", sock.getsockname()[1])
        writer.write(request_data)
        await writer.drain()

        # Read response
        response = await reader.read(1024)
        writer.close()
        await writer.wait_closed()
        return response

    # Start worker in background
    async def run_worker():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(worker._serve(), timeout=2.0)

    worker_task = asyncio.create_task(run_worker())

    # Give worker time to start
    await asyncio.sleep(0.1)

    try:
        # Make request
        response = await make_request()

        # Verify response
        assert b"HTTP/1.1 200" in response
        assert b"OK" in response

        # Verify scope had state
        assert request_scope_seen is not None
        assert "state" in request_scope_seen
        assert request_scope_seen["state"] is lifespan_state
        assert request_scope_seen["state"]["db"] == "mock_db"
        assert request_scope_seen["state"]["config"]["setting"] == "value"

    finally:
        # Cleanup
        worker.shutdown()
        await worker_task
        sock.close()


async def test_multiple_requests_share_same_state():
    """Test that multiple requests see the same state dict."""
    scopes_seen = []

    async def app(scope, receive, send):
        if scope["type"] == "http":
            scopes_seen.append(scope)

            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"OK",
                }
            )

    # Create test socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(10)

    config = ServerConfig()
    worker = Worker(config, app, sock)

    # Set lifespan state
    lifespan_state = {"shared": "data"}
    worker.set_lifespan_state(lifespan_state)

    request_data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"

    async def make_request():
        reader, writer = await asyncio.open_connection("127.0.0.1", sock.getsockname()[1])
        writer.write(request_data)
        await writer.drain()
        await reader.read(1024)
        writer.close()
        await writer.wait_closed()

    async def run_worker():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(worker._serve(), timeout=2.0)

    worker_task = asyncio.create_task(run_worker())
    await asyncio.sleep(0.1)

    try:
        # Make multiple requests
        await make_request()
        await make_request()
        await make_request()

        # All requests should have seen the same state dict
        assert len(scopes_seen) == 3
        for scope in scopes_seen:
            assert "state" in scope
            assert scope["state"] is lifespan_state
            assert scope["state"]["shared"] == "data"

    finally:
        worker.shutdown()
        await worker_task
        sock.close()


async def test_empty_state_when_not_set():
    """Test that requests get empty state dict if not set."""
    request_scope_seen = None

    async def app(scope, receive, send):
        nonlocal request_scope_seen

        if scope["type"] == "http":
            request_scope_seen = scope

            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"OK",
                }
            )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)

    config = ServerConfig()
    worker = Worker(config, app, sock)

    # Don't set lifespan state (simulates no lifespan or failed startup)
    # Worker initializes it to {} by default

    request_data = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"

    async def make_request():
        reader, writer = await asyncio.open_connection("127.0.0.1", sock.getsockname()[1])
        writer.write(request_data)
        await writer.drain()
        await reader.read(1024)
        writer.close()
        await writer.wait_closed()

    async def run_worker():
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(worker._serve(), timeout=2.0)

    worker_task = asyncio.create_task(run_worker())
    await asyncio.sleep(0.1)

    try:
        await make_request()

        # Should still have state, just empty
        assert request_scope_seen is not None
        assert "state" in request_scope_seen
        assert isinstance(request_scope_seen["state"], dict)
        assert len(request_scope_seen["state"]) == 0

    finally:
        worker.shutdown()
        await worker_task
        sock.close()
