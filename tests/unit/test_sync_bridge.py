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


async def _early_hints_app(scope: Scope, receive: object, send: Send) -> None:
    """ASGI app that sends a 103 Early Hints interim response before the final 200."""
    await send(
        {
            "type": "http.response.start",
            "status": 103,
            "headers": [(b"link", b"</style.css>; rel=preload; as=style")],
        }
    )
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"hello", "more_body": False})


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


def test_call_asgi_sync_hands_off_on_103_early_hints() -> None:
    """A 1xx interim response (103) forces handoff to async — the sync bridge
    cannot write an interim status line, so it must defer to the async worker
    that emits 103 via send_informational (parity with the async H1 path)."""
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "asgi": {"version": "3.0"},
    }
    response = call_asgi_sync(_early_hints_app, scope, b"")
    assert response.needs_async


async def _crlf_header_app(scope: Scope, receive: object, send: Send) -> None:
    """ASGI app that emits a header value containing CRLF (injection attempt)."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"x-evil", b"value\r\nInjected: yes"),
                (b"x-ok", b"fine"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b"hello", "more_body": False})


async def _empty_name_header_app(scope: Scope, receive: object, send: Send) -> None:
    """ASGI app whose header name vanishes after CR/LF stripping."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"\r\n", b"orphan"), (b"x-ok", b"fine")],
        }
    )
    await send({"type": "http.response.body", "body": b"hi", "more_body": False})


def test_call_asgi_sync_strips_crlf_in_header_value() -> None:
    """CRLF in a header value is stripped (parity with async/H2/H3 bridges), #120.

    The response succeeds with cleaned headers rather than raising
    HeaderInjectionError at serialization and dropping the connection.
    """
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "asgi": {"version": "3.0"},
    }
    response = call_asgi_sync(_crlf_header_app, scope, b"")
    assert response.status == 200
    assert response.body == b"hello"
    # No CR/LF survives in any header name or value.
    for name, value in response.headers:
        assert b"\r" not in name
        assert b"\n" not in name
        assert b"\r" not in value
        assert b"\n" not in value
    # The injected pseudo-header was collapsed, not added as a separate header.
    assert (b"x-evil", b"valueInjected: yes") in response.headers
    assert (b"x-ok", b"fine") in response.headers


def test_call_asgi_sync_drops_header_with_empty_name_after_strip() -> None:
    """A header whose name is only CR/LF is dropped, matching _sanitize_headers, #120."""
    scope: Scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "asgi": {"version": "3.0"},
    }
    response = call_asgi_sync(_empty_name_header_app, scope, b"")
    assert response.status == 200
    names = [name for name, _ in response.headers]
    assert b"" not in names
    assert (b"x-ok", b"fine") in response.headers
