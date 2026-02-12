"""
Tests for development error pages with rich tracebacks.

"""

import sys

import pytest

from pounce._debug import (
    _extract_frames,
    _get_source_context,
    _sanitize_locals,
    create_debug_error_response,
    create_production_error_response,
    format_exception_html,
)
from pounce.config import ServerConfig


class TestSourceContext:
    """Tests for source code context extraction."""

    def test_get_source_context(self):
        """Test getting source lines around a line number."""
        # Use this test file as an example
        lines = _get_source_context(__file__, 10, context=2)

        assert len(lines) > 0
        # Should have line numbers and text
        assert all(isinstance(num, int) and isinstance(text, str) for num, text in lines)

    def test_get_source_context_invalid_file(self):
        """Test handling of invalid file paths."""
        lines = _get_source_context("/nonexistent/file.py", 10, context=2)

        assert lines == []

    def test_get_source_context_boundary(self):
        """Test context extraction at file boundaries."""
        lines = _get_source_context(__file__, 1, context=10)

        # Should not have negative line numbers
        assert all(num > 0 for num, _ in lines)


class TestSanitizeLocals:
    """Tests for local variable sanitization."""

    def test_sanitize_normal_variables(self):
        """Test sanitizing normal local variables."""
        locals_dict = {
            "x": 42,
            "name": "Alice",
            "items": [1, 2, 3],
        }

        sanitized = _sanitize_locals(locals_dict)

        assert "x" in sanitized
        assert "name" in sanitized
        assert "items" in sanitized
        assert sanitized["x"] == "42"

    def test_sanitize_sensitive_variables(self):
        """Test that sensitive variables are redacted."""
        locals_dict = {
            "password": "secret123",
            "api_key": "abc123",
            "secret_token": "xyz789",
            "my_private_key": "key123",
            "normal_var": "safe",
        }

        sanitized = _sanitize_locals(locals_dict)

        # Sensitive variables should be redacted
        assert sanitized["password"] == "<redacted>"
        assert sanitized["api_key"] == "<redacted>"
        assert sanitized["secret_token"] == "<redacted>"
        assert sanitized["my_private_key"] == "<redacted>"

        # Normal variables should pass through
        assert sanitized["normal_var"] == "'safe'"

    def test_sanitize_dunders_excluded(self):
        """Test that dunder variables are excluded."""
        locals_dict = {
            "__name__": "test",
            "__file__": "/path/to/file.py",
            "normal": "value",
        }

        sanitized = _sanitize_locals(locals_dict)

        assert "__name__" not in sanitized
        assert "__file__" not in sanitized
        assert "normal" in sanitized

    def test_sanitize_long_values_truncated(self):
        """Test that very long values are truncated."""
        locals_dict = {
            "long_string": "x" * 500,
        }

        sanitized = _sanitize_locals(locals_dict)

        # Should be truncated to 200 chars (plus quotes)
        assert len(sanitized["long_string"]) <= 202

    def test_sanitize_unprintable_values(self):
        """Test handling of values that can't be repr'd."""
        class UnprintableClass:
            def __repr__(self):
                raise ValueError("Can't print this!")

        locals_dict = {
            "bad": UnprintableClass(),
            "good": "normal",
        }

        sanitized = _sanitize_locals(locals_dict)

        assert sanitized["bad"] == "<unavailable>"
        assert sanitized["good"] == "'normal'"


class TestExtractFrames:
    """Tests for traceback frame extraction."""

    def test_extract_frames_from_exception(self):
        """Test extracting frames from a real exception."""
        def inner():
            raise ValueError("Test error")

        def outer():
            inner()

        try:
            outer()
        except ValueError:
            _, _, tb = sys.exc_info()
            frames = _extract_frames(tb)

        # Should have multiple frames
        assert len(frames) >= 2

        # Each frame should have required fields
        for frame in frames:
            assert "filename" in frame
            assert "lineno" in frame
            assert "name" in frame
            assert "source" in frame
            assert "locals" in frame


class TestFormatExceptionHTML:
    """Tests for HTML formatting."""

    def test_format_exception_basic(self):
        """Test basic exception formatting."""
        try:
            raise ValueError("Test error message")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            html = format_exception_html(
                exc_type,
                exc_value,
                exc_tb,
                request_method="GET",
                request_path="/test",
            )

        # Should contain key elements
        assert "ValueError" in html
        assert "Test error message" in html
        assert "GET /test" in html
        assert "<html>" in html
        assert "</html>" in html

    def test_format_exception_with_headers(self):
        """Test exception formatting with request headers."""
        try:
            raise RuntimeError("Error")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            html = format_exception_html(
                exc_type,
                exc_value,
                exc_tb,
                request_headers=[(b"user-agent", b"test-client")],
            )

        assert "Request Details" in html
        assert "user-agent" in html

    def test_format_exception_sanitizes_auth_headers(self):
        """Test that sensitive headers are not shown."""
        try:
            raise RuntimeError("Error")
        except RuntimeError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            html = format_exception_html(
                exc_type,
                exc_value,
                exc_tb,
                request_headers=[
                    (b"authorization", b"Bearer secret"),
                    (b"cookie", b"session=123"),
                    (b"user-agent", b"test-client"),
                ],
            )

        # Sensitive headers should not appear
        assert "authorization" not in html.lower() or "Bearer secret" not in html
        assert "cookie" not in html or "session=123" not in html

        # Non-sensitive headers should appear
        assert "user-agent" in html


class TestCreateDebugErrorResponse:
    """Tests for debug error response creation."""

    def test_create_debug_error_response(self):
        """Test creating a debug error response."""
        try:
            raise ValueError("Test error")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            status, headers, body = create_debug_error_response(
                exc_type,
                exc_value,
                exc_tb,
                request_method="POST",
                request_path="/api/test",
            )

        assert status == 500
        assert any(name == b"content-type" for name, _ in headers)
        assert b"text/html" in dict(headers)[b"content-type"]
        assert len(body) > 0
        assert b"ValueError" in body


class TestCreateProductionErrorResponse:
    """Tests for production error response."""

    def test_create_production_error_response(self):
        """Test creating a simple production error."""
        status, headers, body = create_production_error_response()

        assert status == 500
        assert dict(headers)[b"content-type"] == b"text/plain; charset=utf-8"
        assert body == b"Internal Server Error"


class TestServerConfig:
    """Tests for debug configuration."""

    def test_default_debug_false(self):
        """Test that debug defaults to False."""
        config = ServerConfig()

        assert config.debug is False

    def test_enable_debug(self):
        """Test enabling debug mode."""
        config = ServerConfig(debug=True)

        assert config.debug is True


class TestSecurityConsiderations:
    """Security tests to ensure debug pages don't leak in production."""

    def test_production_response_has_no_source_code(self):
        """Test that production errors don't expose source code."""
        status, headers, body = create_production_error_response()

        # Should not contain any Python source code hints
        assert b"raise" not in body
        assert b"def " not in body
        assert b"class " not in body
        assert b".py" not in body

    def test_debug_response_not_created_with_production_flag(self):
        """Test that debug is explicitly controlled by config."""
        config_prod = ServerConfig(debug=False)
        config_dev = ServerConfig(debug=True)

        assert config_prod.debug is False
        assert config_dev.debug is True

    def test_sensitive_data_redacted_in_locals(self):
        """Test that passwords and secrets are redacted."""
        # This is critical for security
        locals_dict = {
            "user_password": "secret123",
            "api_secret": "xyz",
            "database_password": "db123",
        }

        sanitized = _sanitize_locals(locals_dict)

        # All should be redacted
        for value in sanitized.values():
            assert value == "<redacted>"


class TestErrorPageFormatting:
    """Tests for error page visual formatting."""

    def test_html_structure_valid(self):
        """Test that generated HTML has valid structure."""
        try:
            raise ValueError("Test")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            html = format_exception_html(exc_type, exc_value, exc_tb)

        # Basic HTML validity checks
        assert html.count("<html>") == 1
        assert html.count("</html>") == 1
        assert html.count("<head>") == 1
        assert html.count("</head>") == 1
        assert html.count("<body>") == 1
        assert html.count("</body>") == 1

    def test_css_styling_included(self):
        """Test that error pages include CSS styling."""
        try:
            raise ValueError("Test")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            html = format_exception_html(exc_type, exc_value, exc_tb)

        assert "<style>" in html
        assert "</style>" in html
        assert "background:" in html  # Has CSS rules

    def test_source_code_highlighted(self):
        """Test that source code is shown in frames."""
        try:
            x = 1  # noqa: F841
            raise ValueError("Test")
        except ValueError:
            exc_type, exc_value, exc_tb = sys.exc_info()
            html = format_exception_html(exc_type, exc_value, exc_tb)

        # Should show source lines
        assert "source-line" in html
        assert "line-number" in html
