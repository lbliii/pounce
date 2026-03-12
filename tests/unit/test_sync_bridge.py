"""Tests for pounce.asgi.sync_bridge — sync ASGI invocation."""

from pounce._types import Scope, Send
from pounce.asgi.sync_bridge import SyncResponse, call_asgi_sync


async def _simple_app(scope: Scope, receive: object, send: Send) -> None:
    """ASGI app that returns a simple response."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"hello", "more_body": False})


async def _streaming_app(scope: Scope, receive: object, send: Send) -> None:
    """ASGI app that streams response (triggers NeedsAsyncError)."""
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"chunk1", "more_body": True})


def test_call_asgi_sync_simple_response() -> None:
    """call_asgi_sync returns complete response for non-streaming app."""
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "asgi": {"version": "3.0"},
    }
    response = call_asgi_sync(_simple_app, scope, b"")
    assert isinstance(response, SyncResponse)
    assert response.status == 200
    assert response.body == b"hello"
    assert not response.needs_async


def test_call_asgi_sync_streaming_sets_needs_async() -> None:
    """call_asgi_sync returns needs_async=True when app sends more_body=True."""
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "asgi": {"version": "3.0"},
    }
    response = call_asgi_sync(_streaming_app, scope, b"")
    assert response.needs_async
    assert response.status == 200
    assert response.body == b"chunk1"
