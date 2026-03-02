"""Tests for pounce.asgi.lifespan — ASGI lifespan protocol handler."""

import asyncio
from typing import Any

import pytest

from pounce._errors import LifespanError
from pounce._types import Receive, Scope, Send
from pounce.asgi.lifespan import run_lifespan
from pounce.config import ServerConfig


async def _lifespan_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal ASGI app that handles lifespan properly."""
    assert scope["type"] == "lifespan"

    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def _failing_startup_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that fails during startup."""
    assert scope["type"] == "lifespan"

    message = await receive()
    if message["type"] == "lifespan.startup":
        await send(
            {
                "type": "lifespan.startup.failed",
                "message": "Database connection refused",
            }
        )


async def _no_lifespan_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that doesn't support lifespan — raises immediately."""
    if scope["type"] == "lifespan":
        raise TypeError("This app doesn't support lifespan")


async def _silent_return_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that silently returns for non-HTTP scopes (like chirp).

    This pattern is common: the app only handles ``scope["type"] == "http"``
    and returns without sending any lifespan messages.
    """
    if scope["type"] != "http":
        return


async def _slow_shutdown_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app with slow shutdown (for timeout testing)."""
    assert scope["type"] == "lifespan"

    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            # Simulate slow shutdown — never sends complete
            await asyncio.sleep(100)


async def _slow_startup_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that blocks during startup without sending complete."""
    assert scope["type"] == "lifespan"
    message = await receive()
    if message["type"] == "lifespan.startup":
        # Block forever without sending startup.complete
        await asyncio.sleep(100)


class TestLifespanStartupTimeout:
    """Startup times out if app doesn't respond."""

    @pytest.mark.asyncio
    async def test_startup_timeout(self):
        """App receives lifespan.startup then blocks; server times out and proceeds."""
        config = ServerConfig(startup_timeout=0.1)
        async with run_lifespan(_slow_startup_app, config):
            pass  # Startup will timeout — should not hang


class TestLifespanHappyPath:
    """Normal lifespan startup and shutdown."""

    @pytest.mark.asyncio
    async def test_startup_and_shutdown(self):
        config = ServerConfig()
        async with run_lifespan(_lifespan_app, config):
            pass  # Server runs here

    @pytest.mark.asyncio
    async def test_app_receives_correct_scope(self):
        received_scope: dict[str, Any] = {}

        async def capture_app(scope: Scope, receive: Receive, send: Send) -> None:
            received_scope.update(scope)
            # Still handle the protocol
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return

        config = ServerConfig()
        async with run_lifespan(capture_app, config):
            pass

        assert received_scope["type"] == "lifespan"
        assert received_scope["asgi"]["version"] == "3.0"


class TestLifespanStartupFailure:
    """App sends lifespan.startup.failed."""

    @pytest.mark.asyncio
    async def test_raises_lifespan_error(self):
        config = ServerConfig()
        with pytest.raises(LifespanError, match="Database connection refused"):
            async with run_lifespan(_failing_startup_app, config):
                pass

    @pytest.mark.asyncio
    async def test_failure_without_message(self):
        async def fail_no_msg(scope: Scope, receive: Receive, send: Send) -> None:
            await receive()
            await send({"type": "lifespan.startup.failed"})

        config = ServerConfig()
        with pytest.raises(LifespanError, match="Lifespan startup failed"):
            async with run_lifespan(fail_no_msg, config):
                pass


class TestLifespanNotSupported:
    """App that doesn't support lifespan — treated as no-op."""

    @pytest.mark.asyncio
    async def test_no_lifespan_is_noop(self):
        config = ServerConfig()
        async with run_lifespan(_no_lifespan_app, config):
            pass  # Should not raise

    @pytest.mark.asyncio
    async def test_silent_return_is_noop(self):
        """App that returns without raising for non-HTTP scopes (chirp pattern).

        Some frameworks check ``scope["type"] != "http"`` and return silently
        instead of raising. The server must not deadlock waiting for a
        startup.complete that will never arrive.
        """
        config = ServerConfig()
        async with run_lifespan(_silent_return_app, config):
            pass  # Must not hang


class TestLifespanShutdownTimeout:
    """Shutdown times out if app doesn't respond."""

    @pytest.mark.asyncio
    async def test_shutdown_timeout(self):
        config = ServerConfig(shutdown_timeout=0.1)
        async with run_lifespan(_slow_shutdown_app, config):
            pass  # Shutdown will timeout — should not hang
