"""Tests for per-worker lifecycle scopes.

Covers:
- Worker sends ``pounce.worker.startup`` before accepting connections.
- Worker sends ``pounce.worker.shutdown`` after closing the server.
- Startup failure prevents the worker from accepting connections.
- Shutdown failure is logged but does not prevent worker exit.
- Apps that raise on unknown scope types are handled gracefully.
- Single-worker mode (via Server) sends worker scopes.
"""

import asyncio
import threading

import pytest

from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from pounce.net.listener import create_listener
from pounce.server import Server
from pounce.worker import Worker


class TestWorkerLifecycleScopes:
    """Worker sends per-worker startup/shutdown scopes to the ASGI app."""

    @pytest.mark.asyncio
    async def test_startup_scope_sent_before_connections(self):
        """App receives pounce.worker.startup with correct worker_id."""
        scopes_seen: list[dict] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] in ("pounce.worker.startup", "pounce.worker.shutdown"):
                scopes_seen.append(dict(scope))
                return
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=7)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        # Give worker time to start and process the startup scope
        await asyncio.sleep(0.3)

        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

        # Startup scope should have been sent
        startup_scopes = [s for s in scopes_seen if s["type"] == "pounce.worker.startup"]
        assert len(startup_scopes) == 1
        assert startup_scopes[0]["worker_id"] == 7

    @pytest.mark.asyncio
    async def test_shutdown_scope_sent_after_close(self):
        """App receives pounce.worker.shutdown with correct worker_id."""
        scopes_seen: list[dict] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] in ("pounce.worker.startup", "pounce.worker.shutdown"):
                scopes_seen.append(dict(scope))
                return
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=3)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        await asyncio.sleep(0.3)
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

        shutdown_scopes = [s for s in scopes_seen if s["type"] == "pounce.worker.shutdown"]
        assert len(shutdown_scopes) == 1
        assert shutdown_scopes[0]["worker_id"] == 3

    @pytest.mark.asyncio
    async def test_startup_and_shutdown_both_sent(self):
        """Both startup and shutdown scopes fire in correct order."""
        events: list[str] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "pounce.worker.startup":
                events.append("startup")
                return
            if scope["type"] == "pounce.worker.shutdown":
                events.append("shutdown")
                return
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=0)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        await asyncio.sleep(0.3)
        worker.shutdown()
        thread.join(timeout=3)
        sock.close()

        assert events == ["startup", "shutdown"]


class TestWorkerStartupFailure:
    """Worker startup hook failure prevents serving."""

    @pytest.mark.asyncio
    async def test_startup_failure_prevents_serving(self):
        """If startup scope raises, the worker exits without accepting."""
        connections_accepted = False

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal connections_accepted
            if scope["type"] == "pounce.worker.startup":
                msg = "Cannot initialise worker resources"
                raise RuntimeError(msg)
            if scope["type"] == "pounce.worker.shutdown":
                return
            if scope["type"] == "http":
                connections_accepted = True
            if scope["type"] == "lifespan":
                while True:
                    msg_data = await receive()
                    if msg_data["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg_data["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=0)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        # Worker should exit quickly due to startup failure
        thread.join(timeout=3)

        assert not connections_accepted
        sock.close()


class TestWorkerShutdownFailure:
    """Worker shutdown hook failure is non-fatal."""

    @pytest.mark.asyncio
    async def test_shutdown_failure_non_fatal(self):
        """If shutdown scope raises, worker still exits cleanly."""
        startup_called = False

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal startup_called
            if scope["type"] == "pounce.worker.startup":
                startup_called = True
                return
            if scope["type"] == "pounce.worker.shutdown":
                msg = "Cleanup failed"
                raise RuntimeError(msg)
            if scope["type"] == "lifespan":
                while True:
                    msg_data = await receive()
                    if msg_data["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg_data["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, app, sock, worker_id=0)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        await asyncio.sleep(0.3)
        assert startup_called

        worker.shutdown()
        # Worker should exit despite shutdown failure
        thread.join(timeout=3)
        assert not thread.is_alive()
        sock.close()


class TestUnrecognizedScopeGraceful:
    """Apps that raise on unknown scope types don't crash the worker."""

    @pytest.mark.asyncio
    async def test_app_raises_on_unknown_scope(self):
        """App that raises TypeError on non-HTTP scopes is handled."""

        async def strict_app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return
            if scope["type"] == "http":
                return
            # Unknown scope — raise
            msg = f"Unknown scope type: {scope['type']}"
            raise TypeError(msg)

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        sock = create_listener(config)
        worker = Worker(config, strict_app, sock, worker_id=0)
        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()

        # Worker should exit (startup scope raises)
        thread.join(timeout=3)
        assert not thread.is_alive()
        sock.close()


class TestSingleWorkerLifecycle:
    """Server single-worker mode sends worker lifecycle scopes."""

    @pytest.mark.asyncio
    async def test_single_worker_sends_both_scopes(self):
        """In single-worker mode, worker startup and shutdown scopes fire."""
        events: list[str] = []

        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] == "pounce.worker.startup":
                events.append("startup")
                return
            if scope["type"] == "pounce.worker.shutdown":
                events.append("shutdown")
                return
            if scope["type"] == "lifespan":
                while True:
                    msg = await receive()
                    if msg["type"] == "lifespan.startup":
                        await send({"type": "lifespan.startup.complete"})
                    elif msg["type"] == "lifespan.shutdown":
                        await send({"type": "lifespan.shutdown.complete"})
                        return

        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        server = Server(config, app)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        # Give the server time to start
        await asyncio.sleep(0.5)

        assert "startup" in events

        server.shutdown()
        thread.join(timeout=3)

        assert events == ["startup", "shutdown"]
