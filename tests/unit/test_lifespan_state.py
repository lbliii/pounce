"""
Tests for ASGI lifespan state sharing (ASGI 3.0 spec).

"""

import asyncio

import pytest

from pounce.asgi.lifespan import run_lifespan
from pounce.config import ServerConfig


async def test_lifespan_state_created():
    """Test that lifespan creates and returns a state dict."""
    app_called = False

    async def app(scope, receive, send):
        nonlocal app_called
        app_called = True

        # Verify state dict is in scope
        assert "state" in scope
        assert isinstance(scope["state"], dict)

        # Wait for startup
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})

        # Wait for shutdown
        message = await receive()
        if message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})

    config = ServerConfig()

    async with run_lifespan(app, config) as state:
        assert app_called is True
        assert isinstance(state, dict)


async def test_lifespan_state_shared_with_app():
    """Test that app can populate state during startup."""
    app_state_seen = None

    async def app(scope, receive, send):
        nonlocal app_state_seen
        app_state_seen = scope.get("state")

        # Wait for startup
        message = await receive()
        if message["type"] == "lifespan.startup":
            # Populate state
            scope["state"]["db_pool"] = "mock_pool"
            scope["state"]["config"] = {"key": "value"}
            await send({"type": "lifespan.startup.complete"})

        # Wait for shutdown
        message = await receive()
        if message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})

    config = ServerConfig()

    async with run_lifespan(app, config) as state:
        # State should be populated
        assert "db_pool" in state
        assert state["db_pool"] == "mock_pool"
        assert "config" in state
        assert state["config"]["key"] == "value"

        # Should be the same object the app saw
        assert state is app_state_seen


async def test_lifespan_state_empty_for_non_supporting_app():
    """Test that apps not supporting lifespan still get empty state dict."""

    async def app(scope, receive, send):
        # App doesn't implement lifespan, just returns
        pass

    config = ServerConfig()

    async with run_lifespan(app, config) as state:
        # State should exist but be empty
        assert isinstance(state, dict)
        assert len(state) == 0


async def test_lifespan_state_persists_across_context():
    """Test that state dict persists throughout the context manager."""
    state_at_start = None
    state_during = None

    async def app(scope, receive, send):
        nonlocal state_at_start

        message = await receive()
        if message["type"] == "lifespan.startup":
            scope["state"]["initialized"] = True
            state_at_start = scope["state"]
            await send({"type": "lifespan.startup.complete"})

        message = await receive()
        if message["type"] == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})

    config = ServerConfig()

    async with run_lifespan(app, config) as state:
        state_during = state
        # State should have what the app set
        assert state["initialized"] is True

    # Should be the same object
    assert state_at_start is state_during


async def test_lifespan_state_modifications_visible():
    """Test that modifications to state are visible to the returned dict."""

    async def app(scope, receive, send):
        message = await receive()
        if message["type"] == "lifespan.startup":
            # Modify state
            scope["state"]["counter"] = 0
            await send({"type": "lifespan.startup.complete"})

        message = await receive()
        if message["type"] == "lifespan.shutdown":
            # Increment counter
            scope["state"]["counter"] += 1
            await send({"type": "lifespan.shutdown.complete"})

    config = ServerConfig()

    async with run_lifespan(app, config) as state:
        # Initial value from startup
        assert state["counter"] == 0

        # Modify from outside the app
        state["counter"] = 5

    # Modifications should persist
    # (Note: shutdown increments it to 6)
    assert state["counter"] == 6
