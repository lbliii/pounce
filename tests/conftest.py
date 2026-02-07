"""
Shared test fixtures for pounce.

Provides reusable ASGI app fixtures and test utilities.

"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

type Scope = dict[str, Any]
type Receive = Callable[[], Awaitable[dict[str, Any]]]
type Send = Callable[[dict[str, Any]], Awaitable[None]]


async def _hello_app(scope: Scope, receive: Receive, send: Send) -> None:
    """Minimal ASGI app that returns 'Hello, World!'."""
    if scope["type"] == "http":
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/plain"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"Hello, World!",
            }
        )


async def _echo_app(scope: Scope, receive: Receive, send: Send) -> None:
    """ASGI app that echoes the request body."""
    if scope["type"] == "http":
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/octet-stream"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": body,
            }
        )


@pytest.fixture
def hello_app() -> Any:
    """Minimal ASGI app for testing."""
    return _hello_app


@pytest.fixture
def echo_app() -> Any:
    """Echo ASGI app for testing."""
    return _echo_app
