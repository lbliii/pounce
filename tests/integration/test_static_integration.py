"""
Integration tests for static file serving with ASGI.

"""

import pytest

from pounce._static import StaticFiles, StaticMount


@pytest.fixture
def temp_static_dir(tmp_path):
    """Create temporary directory with test files (Bengal-like structure)."""
    # Create test files
    (tmp_path / "index.html").write_text("<h1>Hello World</h1>")
    (tmp_path / "style.css").write_text("body { color: red; }")
    (tmp_path / "data.json").write_text('{"key": "value"}')

    # Create large file for testing
    large_file = tmp_path / "large.txt"
    large_file.write_text("x" * 100_000)  # 100 KB

    # Create subdirectory
    subdir = tmp_path / "assets"
    subdir.mkdir()
    (subdir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    # Create precompressed variant
    css_gz = tmp_path / "style.css.gz"
    # Simulate gzip'd content
    css_gz.write_bytes(b"\x1f\x8b\x08\x00" + b"compressed css content")

    # Bengal-like structure: nested directory indices
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.html").write_text("<h1>Docs</h1>")
    get_started_dir = docs_dir / "get-started"
    get_started_dir.mkdir()
    (get_started_dir / "index.html").write_text("<h1>Get Started</h1>")

    # Bengal-specific file types
    (tmp_path / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    # Minimal valid ICO (22-byte header + 1x1 pixel)
    (tmp_path / "favicon.ico").write_bytes(
        b"\x00\x00\x01\x00\x01\x00\x10\x10\x00\x00\x01\x00\x18\x00(\x04\x00\x00"
        b"\x16\x00\x00\x00(\x00\x00\x00\x10\x00\x00\x00 \x00\x00\x00\x01\x00\x18\x00"
    )
    (tmp_path / "search-index.json").write_text('{"index": [], "docs": []}')

    return tmp_path


@pytest.fixture
def static_app(temp_static_dir):
    """Create static file ASGI app."""
    return StaticFiles(
        mounts=[
            StaticMount("/", temp_static_dir),
        ]
    )


class TestStaticFileServing:
    """Integration tests for serving static files."""

    async def test_serve_simple_file(self, static_app):
        """Test serving a simple file."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/index.html",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200

        # Check headers
        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"text/html"
        assert b"content-length" in headers
        assert headers[b"etag"].startswith(b'W/"')
        assert headers[b"cache-control"] == b"public, max-age=3600"

        # Check body
        body = b"".join(response_body)
        assert body == b"<h1>Hello World</h1>"

    async def test_serve_root_path(self, static_app):
        """Test serving index.html for root path GET /."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200
        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"text/html"
        body = b"".join(response_body)
        assert body == b"<h1>Hello World</h1>"

    async def test_serve_directory_index_trailing_slash(self, static_app):
        """Test serving docs/index.html for GET /docs/."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/docs/",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200
        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"text/html"
        body = b"".join(response_body)
        assert body == b"<h1>Docs</h1>"

    async def test_serve_directory_index_no_trailing_slash(self, static_app):
        """Test serving docs/index.html for GET /docs (no trailing slash)."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/docs",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200
        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"text/html"
        body = b"".join(response_body)
        assert body == b"<h1>Docs</h1>"

    async def test_serve_nested_directory_index(self, static_app):
        """Test serving docs/get-started/index.html for GET /docs/get-started/."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/docs/get-started/",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200
        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"text/html"
        body = b"".join(response_body)
        assert body == b"<h1>Get Started</h1>"

    async def test_serve_css_file(self, static_app):
        """Test serving CSS file."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/style.css",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200

        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"text/css"

        body = b"".join(response_body)
        assert body == b"body { color: red; }"

    async def test_serve_json_file(self, static_app):
        """Test serving JSON file."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/data.json",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200

        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"application/json"

        body = b"".join(response_body)
        assert body == b'{"key": "value"}'

    async def test_serve_subdirectory_file(self, static_app):
        """Test serving file from subdirectory."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/assets/logo.png",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200

        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"image/png"

    async def test_serve_svg(self, static_app):
        """Test serving SVG file (Bengal icon.svg)."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/icon.svg",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200
        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"image/svg+xml"
        body = b"".join(response_body)
        assert b"<svg" in body

    async def test_serve_favicon(self, static_app):
        """Test serving favicon.ico (Bengal favicon)."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/favicon.ico",
            "headers": [],
        }

        response_started = None

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200
        headers = dict(response_started["headers"])
        # Both valid .ico MIME types (mimetypes varies by platform)
        assert headers[b"content-type"] in (b"image/x-icon", b"image/vnd.microsoft.icon")

    async def test_serve_large_json(self, static_app):
        """Test serving search-index.json (Bengal Lunr-style)."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/search-index.json",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200
        headers = dict(response_started["headers"])
        assert headers[b"content-type"] == b"application/json"
        body = b"".join(response_body)
        assert b'"index"' in body
        assert b'"docs"' in body


class TestHeadRequests:
    """Tests for HEAD request handling."""

    async def test_head_request(self, static_app):
        """Test HEAD request returns headers but no body."""
        scope = {
            "type": "http",
            "method": "HEAD",
            "path": "/index.html",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200

        # Check headers are present
        headers = dict(response_started["headers"])
        assert b"content-length" in headers
        assert b"content-type" in headers

        # Body should be empty
        body = b"".join(response_body)
        assert body == b""


class TestConditionalRequests:
    """Tests for conditional requests (304 Not Modified)."""

    async def test_not_modified_response(self, static_app):
        """Test 304 response when ETag matches."""
        # First request to get ETag
        scope1 = {
            "type": "http",
            "method": "GET",
            "path": "/index.html",
            "headers": [],
        }

        response_started1 = None

        async def receive1():
            return {"type": "http.disconnect"}

        async def send1(message):
            nonlocal response_started1
            if message["type"] == "http.response.start":
                response_started1 = message

        await static_app(scope1, receive1, send1)

        # Get ETag from first response
        headers1 = dict(response_started1["headers"])
        etag = headers1[b"etag"]

        # Second request with If-None-Match
        scope2 = {
            "type": "http",
            "method": "GET",
            "path": "/index.html",
            "headers": [
                (b"if-none-match", etag),
            ],
        }

        response_started2 = None
        response_body2 = []

        async def receive2():
            return {"type": "http.disconnect"}

        async def send2(message):
            nonlocal response_started2
            if message["type"] == "http.response.start":
                response_started2 = message
            elif message["type"] == "http.response.body":
                response_body2.append(message["body"])

        await static_app(scope2, receive2, send2)

        # Should be 304 Not Modified
        assert response_started2 is not None
        assert response_started2["status"] == 304

        # Body should be empty
        body = b"".join(response_body2)
        assert body == b""


class TestPrecompressedFiles:
    """Tests for precompressed file serving."""

    async def test_serve_gzipped_variant(self, static_app):
        """Test serving .gz variant when client accepts gzip."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/style.css",
            "headers": [
                (b"accept-encoding", b"gzip, deflate"),
            ],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200

        # Check Content-Encoding header is present
        headers = dict(response_started["headers"])
        assert headers[b"content-encoding"] == b"gzip"

        # MIME type should still be text/css
        assert headers[b"content-type"] == b"text/css"

        # Body should be gzipped content
        body = b"".join(response_body)
        assert body.startswith(b"\x1f\x8b")  # Gzip magic number


class TestRangeRequests:
    """Tests for Range request handling."""

    async def test_range_request(self, static_app):
        """Test Range request returns 206 Partial Content."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/large.txt",
            "headers": [
                (b"range", b"bytes=0-999"),
            ],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 206

        # Check Range headers
        headers = dict(response_started["headers"])
        assert b"content-range" in headers
        assert headers[b"content-length"] == b"1000"
        assert headers[b"accept-ranges"] == b"bytes"

        # Body should be exactly 1000 bytes
        body = b"".join(response_body)
        assert len(body) == 1000
        assert body == b"x" * 1000


class TestLargeFiles:
    """Tests for serving large files."""

    async def test_serve_large_file_chunked(self, static_app):
        """Test serving large file in chunks."""
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/large.txt",
            "headers": [],
        }

        response_started = None
        response_body = []
        chunk_count = 0

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started, chunk_count
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])
                chunk_count += 1

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 200

        # Should be sent in multiple chunks (file is 100KB, chunk size is 64KB)
        assert chunk_count >= 2

        # Body should be complete
        body = b"".join(response_body)
        assert len(body) == 100_000
        assert body == b"x" * 100_000


class TestMiddlewareMode:
    """Tests for using StaticFiles as middleware."""

    async def test_middleware_fallthrough(self, temp_static_dir):
        """Test middleware falls through to app for non-static paths."""
        app_called = False

        async def app(scope, receive, send):
            nonlocal app_called
            app_called = True
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/plain")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"app response",
                }
            )

        # Wrap app with static files
        static_middleware = StaticFiles(
            app,
            mounts=[StaticMount("/static", temp_static_dir)],
        )

        # Request non-static path
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/data",
            "headers": [],
        }

        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_middleware(scope, receive, send)

        # App should have been called
        assert app_called is True

        # Should get app response
        body = b"".join(response_body)
        assert body == b"app response"

    async def test_middleware_intercept(self, temp_static_dir):
        """Test middleware intercepts static file requests."""
        app_called = False

        async def app(scope, receive, send):
            nonlocal app_called
            app_called = True

        # Wrap app with static files
        static_middleware = StaticFiles(
            app,
            mounts=[StaticMount("/static", temp_static_dir)],
        )

        # Request static path
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/static/index.html",
            "headers": [],
        }

        response_started = None

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message

        await static_middleware(scope, receive, send)

        # App should NOT have been called
        assert app_called is False

        # Should get static file response
        assert response_started is not None
        assert response_started["status"] == 200


class TestStaticOnlyMode:
    """Tests for static-only mode (app=None)."""

    async def test_404_for_missing_file(self, temp_static_dir):
        """Test 404 when requesting nonexistent file in static-only mode."""
        static_app = StaticFiles(
            app=None,
            mounts=[StaticMount("/", temp_static_dir)],
        )

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/nonexistent.html",
            "headers": [],
        }

        response_started = None
        response_body = []

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message
            elif message["type"] == "http.response.body":
                response_body.append(message["body"])

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 404
        body = b"".join(response_body)
        assert b"Not Found" in body

    async def test_path_traversal_returns_404(self, temp_static_dir):
        """Test path traversal via HTTP returns 404."""
        static_app = StaticFiles(
            app=None,
            mounts=[StaticMount("/", temp_static_dir)],
        )

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/../../../etc/passwd",
            "headers": [],
        }

        response_started = None

        async def receive():
            return {"type": "http.disconnect"}

        async def send(message):
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = message

        await static_app(scope, receive, send)

        assert response_started is not None
        assert response_started["status"] == 404
