"""
Sync ASGI bridge — run ASGI apps from a synchronous context.

For simple request-response (no streaming receive, no streaming send),
collects the response in memory and returns it as a SyncResponse.

If the app sends more_body=True (streaming) or the scope type is
websocket, raises NeedsAsyncError so the caller can hand off to the async pool.

"""

import asyncio
from dataclasses import dataclass
from typing import Any

from pounce._types import ASGIApp
from pounce.asgi.bridge import _sanitize_headers


class NeedsAsyncError(Exception):
    """Raised when the ASGI app requires async (streaming, WebSocket).

    The SyncWorker catches this and hands off the connection to the
    AsyncPool for multiplexed I/O handling.

    """


# Backwards compatibility
NeedsAsync = NeedsAsyncError


@dataclass(slots=True)
class SyncResponse:
    """Complete HTTP response from a sync ASGI invocation.

    Attributes:
        status: HTTP status code.
        headers: Response headers as list of (name, value) byte pairs.
        body: Full response body bytes.
        needs_async: True if the app indicated streaming (more_body=True).

    """

    status: int
    headers: list[tuple[bytes, bytes]]
    body: bytes
    needs_async: bool = False


def call_asgi_sync(
    app: ASGIApp,
    scope: dict[str, Any],
    body: bytes,
    *,
    runner: asyncio.Runner | None = None,
) -> SyncResponse:
    """Run an ASGI app from a sync context.

    For simple request-response (no streaming receive, no streaming send),
    this collects the response in memory and returns it as a SyncResponse.

    If the app sends more_body=True (streaming), sets needs_async=True
    on the response so the caller can hand off. The response may be
    partial (headers + first body chunk).

    Raises NeedsAsyncError immediately for WebSocket scopes (caller must
    hand off before invoking the app).

    Args:
        app: The ASGI application.
        scope: ASGI scope dict.
        body: Full request body (for non-streaming requests).
        runner: Reusable asyncio.Runner owned by the calling worker thread.
            Avoids creating/destroying an event loop per request. When None,
            a temporary Runner is created (slow fallback).

    Returns:
        SyncResponse with status, headers, body. If the app indicated
        streaming, needs_async is True and the caller should hand off.

    """
    if scope.get("type") == "websocket":
        raise NeedsAsyncError()

    response_started = False
    status = 200
    headers: list[tuple[bytes, bytes]] = []
    body_parts: list[bytes] = []
    needs_async = False

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal response_started, status, headers, body_parts, needs_async
        msg_type = message["type"]
        if msg_type == "http.response.start":
            status_code = message["status"]
            # 1xx interim responses (e.g. 103 Early Hints, RFC 8297) must write
            # an interim status line *before* the final response — which the
            # buffering sync bridge cannot do. Hand off to the async worker,
            # which emits informational responses via protocol.send_informational,
            # keeping H1 103 behavior consistent across the sync and async paths.
            if 100 <= status_code < 200:
                needs_async = True
                raise NeedsAsyncError()
            response_started = True
            status = status_code
            raw_headers = message.get("headers", [])
            # Defense-in-depth: strip CR/LF and drop empty names, matching the
            # async/H2/H3 bridges. Without this, an app-supplied CRLF header
            # would raise HeaderInjectionError at serialization and abruptly
            # drop the connection instead of returning a sanitized response.
            headers = _sanitize_headers(
                [
                    (
                        h[0] if isinstance(h[0], bytes) else h[0].encode(),
                        h[1] if isinstance(h[1], bytes) else h[1].encode(),
                    )
                    for h in raw_headers
                ]
            )
        elif msg_type == "http.response.body":
            body_parts.append(message.get("body", b""))
            if message.get("more_body", False):
                needs_async = True
                raise NeedsAsyncError()

    async def run_app() -> None:
        await app(scope, receive, send)

    runner_impl: asyncio.Runner
    if runner is not None:
        runner_impl = runner
        owns_runner = False
    else:
        runner_impl = asyncio.Runner()
        owns_runner = True
    try:
        runner_impl.run(run_app())
    except NeedsAsyncError:
        pass  # Expected — caller will hand off
    finally:
        if owns_runner:
            runner_impl.close()

    return SyncResponse(
        status=status,
        headers=headers,
        body=b"".join(body_parts),
        needs_async=needs_async,
    )
