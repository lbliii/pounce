"""
Tests for middleware extension system.

"""

import pytest

from pounce._middleware import (
    CORSMiddleware,
    MiddlewareStack,
    Response,
    SecurityHeadersMiddleware,
)


class TestResponse:
    """Tests for Response dataclass."""

    def test_response_creation(self):
        """Test creating a Response."""
        response = Response(status=200, headers=[(b"content-type", b"text/plain")], body=b"OK")

        assert response.status == 200
        assert response.headers == [(b"content-type", b"text/plain")]
        assert response.body == b"OK"

    def test_response_defaults(self):
        """Test Response with defaults."""
        response = Response(status=404)

        assert response.status == 404
        assert response.headers == []
        assert response.body == b""


class TestPreRequestMiddleware:
    """Tests for pre-request middleware."""

    async def test_pre_request_modifies_scope(self):
        """Test pre-request middleware can modify scope."""

        async def add_header_middleware(scope):
            scope["custom"] = "added"
            return scope

        async def app(scope, receive, send):
            assert scope["custom"] == "added"
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"OK"})

        stack = MiddlewareStack([add_header_middleware], app)

        scope = {"type": "http", "method": "GET", "path": "/"}
        messages_sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages_sent.append(message)

        await stack(scope, receive, send)

        assert len(messages_sent) == 2
        assert messages_sent[0]["status"] == 200

    async def test_pre_request_short_circuit(self):
        """Test pre-request middleware can short-circuit."""
        app_called = False

        async def auth_middleware(scope):
            # Missing auth header
            if b"authorization" not in dict(scope.get("headers", [])):
                return Response(status=401, body=b"Unauthorized")
            return scope

        async def app(scope, receive, send):
            nonlocal app_called
            app_called = True
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"OK"})

        stack = MiddlewareStack([auth_middleware], app)

        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        messages_sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages_sent.append(message)

        await stack(scope, receive, send)

        # App should not have been called
        assert app_called is False

        # Should have sent 401 response
        assert len(messages_sent) == 2
        assert messages_sent[0]["status"] == 401
        assert messages_sent[1]["body"] == b"Unauthorized"

    async def test_multiple_pre_request_middleware(self):
        """Test multiple pre-request middleware execute in order."""
        execution_order = []

        async def first_middleware(scope):
            execution_order.append("first")
            scope["first"] = True
            return scope

        async def second_middleware(scope):
            execution_order.append("second")
            scope["second"] = True
            return scope

        async def app(scope, receive, send):
            execution_order.append("app")
            assert scope["first"] is True
            assert scope["second"] is True
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"OK"})

        stack = MiddlewareStack([first_middleware, second_middleware], app)

        scope = {"type": "http", "method": "GET", "path": "/"}
        messages_sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages_sent.append(message)

        await stack(scope, receive, send)

        assert execution_order == ["first", "second", "app"]


class TestPostResponseMiddleware:
    """Tests for post-response middleware."""

    async def test_post_response_modifies_headers(self):
        """Test post-response middleware can modify headers."""

        async def add_header_middleware(scope, status, headers):
            headers = list(headers)
            headers.append((b"x-custom", b"value"))
            return (status, headers)

        async def app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send({"type": "http.response.body", "body": b"OK"})

        stack = MiddlewareStack([add_header_middleware], app)

        scope = {"type": "http", "method": "GET", "path": "/"}
        messages_sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages_sent.append(message)

        await stack(scope, receive, send)

        # Check headers were modified
        assert len(messages_sent) == 2
        headers_dict = dict(messages_sent[0]["headers"])
        assert headers_dict[b"x-custom"] == b"value"

    async def test_post_response_modifies_status(self):
        """Test post-response middleware can modify status code."""

        async def override_status_middleware(scope, status, headers):
            # Override all 404s to 200
            if status == 404:
                return (200, headers)
            return (status, headers)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"Not Found"})

        stack = MiddlewareStack([override_status_middleware], app)

        scope = {"type": "http", "method": "GET", "path": "/"}
        messages_sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages_sent.append(message)

        await stack(scope, receive, send)

        # Status should be modified
        assert messages_sent[0]["status"] == 200

    async def test_multiple_post_response_middleware(self):
        """Test multiple post-response middleware execute in order."""

        async def first_middleware(scope, status, headers):
            headers = list(headers)
            headers.append((b"x-first", b"1"))
            return (status, headers)

        async def second_middleware(scope, status, headers):
            headers = list(headers)
            headers.append((b"x-second", b"2"))
            return (status, headers)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"OK"})

        stack = MiddlewareStack([first_middleware, second_middleware], app)

        scope = {"type": "http", "method": "GET", "path": "/"}
        messages_sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages_sent.append(message)

        await stack(scope, receive, send)

        # Both headers should be present
        headers_dict = dict(messages_sent[0]["headers"])
        assert headers_dict[b"x-first"] == b"1"
        assert headers_dict[b"x-second"] == b"2"


class TestExceptionMiddleware:
    """Tests for exception middleware."""

    async def test_exception_middleware_catches_error(self):
        """Test exception middleware can catch and handle errors."""
        app_raised = False

        async def error_handler_middleware(scope, exc):
            if isinstance(exc, ValueError):
                return Response(status=400, body=b"Bad Request")
            return None

        async def app(scope, receive, send):
            nonlocal app_raised
            app_raised = True
            raise ValueError("Invalid input")

        stack = MiddlewareStack([error_handler_middleware], app)

        scope = {"type": "http", "method": "GET", "path": "/"}
        messages_sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages_sent.append(message)

        await stack(scope, receive, send)

        # App should have raised
        assert app_raised is True

        # Should have sent error response
        assert len(messages_sent) == 2
        assert messages_sent[0]["status"] == 400
        assert messages_sent[1]["body"] == b"Bad Request"

    async def test_exception_middleware_reraises(self):
        """Test exception middleware can choose to re-raise."""

        async def selective_handler(scope, exc):
            # Only handle ValueError
            if isinstance(exc, ValueError):
                return Response(status=400, body=b"Bad Request")
            return None  # Re-raise other exceptions

        async def app(scope, receive, send):
            raise RuntimeError("Unexpected error")

        stack = MiddlewareStack([selective_handler], app)

        scope = {"type": "http", "method": "GET", "path": "/"}

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            pass

        # Should re-raise RuntimeError
        with pytest.raises(RuntimeError, match="Unexpected error"):
            await stack(scope, receive, send)


class TestBuiltInMiddleware:
    """Tests for built-in middleware."""

    async def test_cors_middleware(self):
        """Test CORS middleware adds appropriate headers."""
        cors = CORSMiddleware(
            allow_origin="https://example.com",
            allow_methods="GET, POST",
            allow_headers="Content-Type",
            max_age=7200,
        )

        scope = {"type": "http"}
        status = 200
        headers = [(b"content-type", b"application/json")]

        modified_status, modified_headers = await cors(scope, status, headers)

        assert modified_status == 200
        headers_dict = dict(modified_headers)
        assert headers_dict[b"access-control-allow-origin"] == b"https://example.com"
        assert headers_dict[b"access-control-allow-methods"] == b"GET, POST"
        assert headers_dict[b"access-control-allow-headers"] == b"Content-Type"
        assert headers_dict[b"access-control-max-age"] == b"7200"

    async def test_security_headers_middleware(self):
        """Test security headers middleware adds appropriate headers."""
        security = SecurityHeadersMiddleware()

        scope = {"type": "http"}
        status = 200
        headers = []

        modified_status, modified_headers = await security(scope, status, headers)

        assert modified_status == 200
        headers_dict = dict(modified_headers)
        assert headers_dict[b"x-frame-options"] == b"DENY"
        assert headers_dict[b"x-content-type-options"] == b"nosniff"
        assert headers_dict[b"x-xss-protection"] == b"1; mode=block"


class TestMixedMiddleware:
    """Tests for mixing different middleware types."""

    async def test_pre_and_post_middleware(self):
        """Test combining pre-request and post-response middleware."""

        async def pre_middleware(scope):
            scope["processed_by_pre"] = True
            return scope

        async def post_middleware(scope, status, headers):
            if scope.get("processed_by_pre"):
                headers = list(headers)
                headers.append((b"x-pre-processed", b"true"))
            return (status, headers)

        async def app(scope, receive, send):
            assert scope.get("processed_by_pre") is True
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"OK"})

        stack = MiddlewareStack([pre_middleware, post_middleware], app)

        scope = {"type": "http", "method": "GET", "path": "/"}
        messages_sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages_sent.append(message)

        await stack(scope, receive, send)

        headers_dict = dict(messages_sent[0]["headers"])
        assert headers_dict[b"x-pre-processed"] == b"true"

    async def test_all_middleware_types(self):
        """Test combining all three middleware types."""
        execution_log = []

        async def pre_middleware(scope):
            execution_log.append("pre")
            return scope

        async def post_middleware(scope, status, headers):
            execution_log.append("post")
            return (status, headers)

        async def exception_middleware(scope, exc):
            execution_log.append("exception")
            return Response(status=500, body=b"Error handled")

        async def app(scope, receive, send):
            execution_log.append("app_start")
            raise ValueError("Test error")

        stack = MiddlewareStack([pre_middleware, post_middleware, exception_middleware], app)

        scope = {"type": "http", "method": "GET", "path": "/"}
        messages_sent = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            messages_sent.append(message)

        await stack(scope, receive, send)

        # Pre runs, app runs and raises, exception handler catches
        assert "pre" in execution_log
        assert "app_start" in execution_log
        assert "exception" in execution_log
        assert (
            "post" not in execution_log
        )  # Post doesn't run when exception is raised before response

        # Error response sent
        assert messages_sent[0]["status"] == 500
        assert messages_sent[1]["body"] == b"Error handled"
