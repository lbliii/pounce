"""
Middleware extension system for server-level request/response processing.

Provides hooks for pre-request, post-response, and exception handling without
modifying the ASGI bridge or requiring apps to wrap themselves in middleware.

Example:
    async def auth_middleware(scope):
        '''Pre-request hook that can short-circuit.'''
        if not scope["headers"].get(b"authorization"):
            return Response(status=401, body=b"Unauthorized")
        return scope  # Continue to app

    async def cors_middleware(scope, status, headers):
        '''Post-response hook that modifies headers.'''
        headers.append((b"access-control-allow-origin", b"*"))
        return (status, headers)

    config = ServerConfig(middleware=[auth_middleware, cors_middleware])

"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from pounce._types import Receive, Send


@dataclass(frozen=True, slots=True)
class Response:
    """Simple response object for middleware short-circuiting.

    Args:
        status: HTTP status code (e.g., 401, 403, 503)
        headers: List of (name, value) header tuples
        body: Response body bytes

    """

    status: int
    headers: list[tuple[bytes, bytes]] = field(default_factory=list)
    body: bytes = b""


class PreRequestMiddleware(Protocol):
    """Pre-request middleware hook.

    Called before the ASGI app with the request scope. Can inspect/modify
    the scope or short-circuit by returning a Response.

    Returns:
        - Modified scope dict to pass to the next middleware/app
        - Response object to short-circuit (app is not called)

    """

    async def __call__(self, scope: dict[str, Any]) -> dict[str, Any] | Response:
        """Process request before app.

        Args:
            scope: ASGI scope dict

        Returns:
            Modified scope or Response to short-circuit

        """
        ...


class PostResponseMiddleware(Protocol):
    """Post-response middleware hook.

    Called after the app has processed the request but before the response
    is sent. Can modify status code or headers.

    Args:
        scope: The original ASGI scope dict
        status: HTTP status code from app
        headers: Response headers from app

    Returns:
        (status, headers) tuple (possibly modified)

    """

    async def __call__(
        self,
        scope: dict[str, Any],
        status: int,
        headers: list[tuple[bytes, bytes]],
    ) -> tuple[int, list[tuple[bytes, bytes]]]:
        """Process response after app.

        Args:
            scope: ASGI scope dict
            status: HTTP status code
            headers: Response headers

        Returns:
            (status, headers) tuple

        """
        ...


class ExceptionMiddleware(Protocol):
    """Exception middleware hook.

    Called when the ASGI app raises an exception. Can return a custom
    response or None to re-raise.

    Args:
        scope: The ASGI scope dict
        exc: The exception that was raised

    Returns:
        Response object or None to re-raise

    """

    async def __call__(self, scope: dict[str, Any], exc: Exception) -> Response | None:
        """Handle exception from app.

        Args:
            scope: ASGI scope dict
            exc: Exception that was raised

        Returns:
            Response to send, or None to re-raise

        """
        ...


# Union type for all middleware
type Middleware = PreRequestMiddleware | PostResponseMiddleware | ExceptionMiddleware


class MiddlewareStack:
    """Executes middleware hooks in order around an ASGI app call.

    Args:
        middleware: List of middleware callables
        app: The ASGI application

    """

    __slots__ = ("_app", "_middleware")

    def __init__(
        self,
        middleware: list[Middleware],
        app: Callable[[dict[str, Any], Receive, Send], Awaitable[None]],
    ) -> None:
        self._middleware = middleware
        self._app = app

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        """Execute middleware stack around app call.

        1. Run pre-request middleware (can short-circuit)
        2. If not short-circuited, call app
        3. Run post-response middleware (intercept first response.start)
        4. Run exception middleware if app raises

        """
        # Separate middleware by type
        pre_request = []
        post_response = []
        exception_handlers = []

        for mw in self._middleware:
            # Duck-type based on callable signature
            import inspect

            sig = inspect.signature(mw)
            params = list(sig.parameters.keys())

            if len(params) == 1:
                # Single param = pre-request
                pre_request.append(mw)
            elif len(params) == 2:
                # Two params = exception middleware (scope, exc)
                exception_handlers.append(mw)
            elif len(params) == 3:
                # Three params = post-response (scope, status, headers)
                post_response.append(mw)

        # Execute pre-request middleware
        modified_scope = scope
        for mw in pre_request:
            result = await mw(modified_scope)
            if isinstance(result, Response):
                # Short-circuit: send response and return
                await self._send_response(result, send)
                return
            # Result is modified scope
            modified_scope = result

        # Intercept send to run post-response middleware
        response_started = False
        original_status = None
        original_headers = None

        async def intercepting_send(message: dict[str, Any]) -> None:
            nonlocal response_started, original_status, original_headers

            if message["type"] == "http.response.start" and not response_started:
                response_started = True
                original_status = message["status"]
                original_headers = list(message.get("headers", []))

                # Run post-response middleware
                modified_status = original_status
                modified_headers = original_headers
                for mw in post_response:
                    modified_status, modified_headers = await mw(
                        modified_scope, modified_status, modified_headers
                    )

                # Send modified response
                await send(
                    {
                        "type": "http.response.start",
                        "status": modified_status,
                        "headers": modified_headers,
                    }
                )
            else:
                # Pass through other messages
                await send(message)

        # Call app with exception handling
        try:
            await self._app(modified_scope, receive, intercepting_send)
        except Exception as exc:
            # Run exception middleware
            response = None
            for mw in exception_handlers:
                response = await mw(modified_scope, exc)
                if response is not None:
                    break

            if response is not None:
                # Send error response if not already started
                if not response_started:
                    await self._send_response(response, send)
                return

            # No middleware handled it, re-raise
            raise

    async def _send_response(self, response: Response, send: Send) -> None:
        """Send a Response object through ASGI send.

        Args:
            response: Response to send
            send: ASGI send callable

        """
        headers = list(response.headers) if response.headers else []

        await send(
            {
                "type": "http.response.start",
                "status": response.status,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": response.body,
            }
        )


# Built-in middleware examples


class CORSMiddleware:
    """CORS middleware that adds Access-Control headers.

    Args:
        allow_origin: Value for Access-Control-Allow-Origin header
        allow_methods: Comma-separated methods for Allow-Methods header
        allow_headers: Comma-separated headers for Allow-Headers header
        max_age: Max age for preflight cache (seconds)

    """

    __slots__ = ("_allow_headers", "_allow_methods", "_allow_origin", "_max_age")

    def __init__(
        self,
        allow_origin: str = "*",
        allow_methods: str = "GET, POST, PUT, DELETE, OPTIONS",
        allow_headers: str = "*",
        max_age: int = 3600,
    ) -> None:
        self._allow_origin = allow_origin.encode("latin1")
        self._allow_methods = allow_methods.encode("latin1")
        self._allow_headers = allow_headers.encode("latin1")
        self._max_age = str(max_age).encode("latin1")

    async def __call__(
        self,
        scope: dict[str, Any],
        status: int,
        headers: list[tuple[bytes, bytes]],
    ) -> tuple[int, list[tuple[bytes, bytes]]]:
        """Add CORS headers to response."""
        headers = list(headers)
        headers.append((b"access-control-allow-origin", self._allow_origin))
        headers.append((b"access-control-allow-methods", self._allow_methods))
        headers.append((b"access-control-allow-headers", self._allow_headers))
        headers.append((b"access-control-max-age", self._max_age))
        return (status, headers)


class SecurityHeadersMiddleware:
    """Security headers middleware.

    Adds common security headers to all responses:
    - X-Frame-Options: DENY
    - X-Content-Type-Options: nosniff
    - X-XSS-Protection: 1; mode=block

    """

    __slots__ = ()

    async def __call__(
        self,
        scope: dict[str, Any],
        status: int,
        headers: list[tuple[bytes, bytes]],
    ) -> tuple[int, list[tuple[bytes, bytes]]]:
        """Add security headers to response."""
        headers = list(headers)
        headers.append((b"x-frame-options", b"DENY"))
        headers.append((b"x-content-type-options", b"nosniff"))
        headers.append((b"x-xss-protection", b"1; mode=block"))
        return (status, headers)
