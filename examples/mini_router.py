"""
Middleware and request routing — building a mini-router on raw ASGI.

Pounce is a server, not a framework.  It takes an ASGI callable and
serves it — it has no opinions about routing, middleware, or request
handling.  Those are **chirp's** job.

This example shows that routing and middleware are just function
composition.  There's no magic: a router is a dict lookup, middleware
is a wrapper function, and an ASGI handler is ``async def(scope,
receive, send)``.  The whole router is ~50 lines.

For real applications, use `chirp <https://github.com/...>`_ —
it gives you this and much more, with proper type safety and
composable middleware.

Run it:
    pounce examples.mini_router:app

Then try:
    curl http://127.0.0.1:8000/
    curl http://127.0.0.1:8000/users/42
    curl -X POST -d "hello" http://127.0.0.1:8000/echo
    curl http://127.0.0.1:8000/nonexistent

"""

import json
import logging
import re
import time
from collections.abc import Callable
from typing import Any

type Scope = dict[str, Any]
type Receive = Any
type Send = Any
type ASGIApp = Callable[[Scope, Receive, Send], Any]

log = logging.getLogger("examples.mini_router")

# ---------------------------------------------------------------------------
# Mini router — ~50 lines of routing on raw ASGI
# ---------------------------------------------------------------------------


class Router:
    """Minimal ASGI request router.

    Maps ``(method, path_pattern)`` pairs to handler functions.
    Supports simple path parameters via ``{name}`` placeholders.

    This is intentionally bare-bones — just enough to show the concept.
    For production routing, use chirp.
    """

    def __init__(self) -> None:
        self._routes: list[tuple[str, re.Pattern[str], ASGIApp]] = []

    def route(self, method: str, path: str) -> Callable[[ASGIApp], ASGIApp]:
        """Decorator to register a route.

        Path parameters use ``{name}`` syntax::

            @router.route("GET", "/users/{id}")
            async def get_user(scope, receive, send): ...

        Matched parameters are added to ``scope["path_params"]``.
        """
        # Convert "/users/{id}" to a regex: "/users/(?P<id>[^/]+)"
        pattern_str = re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path)
        compiled = re.compile(f"^{pattern_str}$")

        def decorator(handler: ASGIApp) -> ASGIApp:
            self._routes.append((method.upper(), compiled, handler))
            return handler

        return decorator

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Dispatch the request to the matching handler."""
        # --- Lifespan -------------------------------------------------------
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return

        # --- HTTP -----------------------------------------------------------
        assert scope["type"] == "http"

        request_method = scope["method"]
        request_path = scope["path"]

        for method, pattern, handler in self._routes:
            if request_method != method:
                continue
            match = pattern.match(request_path)
            if match:
                scope["path_params"] = match.groupdict()
                await handler(scope, receive, send)
                return

        # No route matched — 404
        await receive()
        body = json.dumps({"error": "not found", "path": request_path}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 404,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# Middleware — just wrapper functions
# ---------------------------------------------------------------------------


def with_logging(inner: ASGIApp) -> ASGIApp:
    """Log every HTTP request with method, path, and status.

    Middleware in ASGI is just a function that wraps another ASGI app.
    No base class, no registration, no framework — just composition.
    """

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return

        captured_status: list[int] = []
        original_send = send

        async def capturing_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                captured_status.append(message["status"])
            await original_send(message)

        await inner(scope, receive, capturing_send)

        status = captured_status[0] if captured_status else 0
        log.info("%s %s -> %d", scope["method"], scope["path"], status)

    return middleware


def with_timing(inner: ASGIApp) -> ASGIApp:
    """Inject an ``X-Response-Time`` header with millisecond precision."""

    async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return

        start = time.monotonic()
        original_send = send

        async def timed_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                elapsed_ms = (time.monotonic() - start) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"x-response-time", f"{elapsed_ms:.2f}ms".encode()))
                message = {**message, "headers": headers}
            await original_send(message)

        await inner(scope, receive, timed_send)

    return middleware


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

router = Router()

# Hardcoded user data for the demo.
_USERS: dict[str, dict[str, str]] = {
    "1": {"id": "1", "name": "Ada Lovelace", "role": "engineer"},
    "42": {"id": "42", "name": "Douglas Adams", "role": "author"},
    "100": {"id": "100", "name": "Grace Hopper", "role": "admiral"},
}


def _json_response(
    data: dict[str, object],
) -> tuple[bytes, list[tuple[bytes, bytes]]]:
    body = json.dumps(data, indent=2).encode()
    headers: list[tuple[bytes, bytes]] = [
        (b"content-type", b"application/json; charset=utf-8"),
        (b"content-length", str(len(body)).encode()),
    ]
    return body, headers


@router.route("GET", "/")
async def index(scope: Scope, receive: Receive, send: Send) -> None:
    """Welcome page with available routes."""
    await receive()
    body, headers = _json_response(
        {
            "server": "pounce",
            "example": "mini_router",
            "routes": [
                "GET /",
                "GET /users/{id}",
                "POST /echo",
            ],
            "note": ("This is ~50 lines of routing on raw ASGI. For real apps, use chirp."),
        }
    )
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})


@router.route("GET", "/users/{id}")
async def get_user(scope: Scope, receive: Receive, send: Send) -> None:
    """Look up a user by ID."""
    await receive()
    user_id = scope["path_params"]["id"]
    user = _USERS.get(user_id)

    if user:
        body, headers = _json_response(user)
        status = 200
    else:
        body, headers = _json_response({"error": f"user {user_id} not found"})
        status = 404

    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


@router.route("POST", "/echo")
async def echo(scope: Scope, receive: Receive, send: Send) -> None:
    """Read the request body and echo it back."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        chunk = message.get("body", b"")
        if chunk:
            chunks.append(chunk)
        if not message.get("more_body", False):
            break

    request_body = b"".join(chunks)
    body, headers = _json_response(
        {
            "echoed_bytes": len(request_body),
            "body": request_body.decode("utf-8", errors="replace"),
        }
    )
    await send({"type": "http.response.start", "status": 200, "headers": headers})
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# Stack middleware and export the ASGI app
# ---------------------------------------------------------------------------

# Middleware composes inside-out: timing wraps the router, logging wraps
# timing.  The request flows:  logging → timing → router → handler.
app: ASGIApp = with_logging(with_timing(router))
