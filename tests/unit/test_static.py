"""
Tests for static file serving.

"""

from pathlib import Path

import pytest

from pounce._static import StaticFiles, StaticMount, create_static_handler


@pytest.fixture
def temp_static_dir(tmp_path):
    """Create temporary directory with test files."""
    # Create test files
    (tmp_path / "index.html").write_text("<h1>Index</h1>")
    (tmp_path / "style.css").write_text("body { color: red; }")
    (tmp_path / "script.js").write_text("console.log('hello');")

    # Create subdirectory
    subdir = tmp_path / "assets"
    subdir.mkdir()
    (subdir / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    # Create docs subdir for root-mount tests
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "index.html").write_text("<h1>Docs</h1>")

    # Create hidden file (should be blocked)
    (tmp_path / ".env").write_text("SECRET=abc123")

    # Create precompressed variant
    css_gz = tmp_path / "style.css.gz"
    css_gz.write_bytes(b"\x1f\x8b\x08\x00compressed")

    return tmp_path


@pytest.fixture
def static_handler(temp_static_dir):
    """Create static file handler."""
    return create_static_handler({"/static": str(temp_static_dir)})


class TestStaticMount:
    """Tests for StaticMount configuration."""

    def test_mount_creation(self, temp_static_dir):
        """Test creating a static mount."""
        mount = StaticMount(
            url_path="/static",
            directory=temp_static_dir,
        )

        assert mount.url_path == "/static"
        assert mount.directory == temp_static_dir
        assert mount.cache_control == "public, max-age=3600"
        assert mount.precompressed is True
        assert mount.follow_symlinks is False
        assert mount.index_file == "index.html"

    def test_mount_custom_settings(self, temp_static_dir):
        """Test mount with custom settings."""
        mount = StaticMount(
            url_path="/assets",
            directory=temp_static_dir,
            cache_control="public, max-age=7200",
            precompressed=False,
            follow_symlinks=True,
            index_file=None,
        )

        assert mount.url_path == "/assets"
        assert mount.cache_control == "public, max-age=7200"
        assert mount.precompressed is False
        assert mount.follow_symlinks is True
        assert mount.index_file is None


class TestStaticFilesHandler:
    """Tests for StaticFiles handler."""

    def test_handler_creation(self, temp_static_dir):
        """Test creating handler."""
        handler = StaticFiles(
            mounts=[
                StaticMount("/static", temp_static_dir),
            ]
        )

        assert handler._mounts[0].url_path == "/static"

    def test_handler_invalid_directory(self):
        """Test creating handler with non-existent directory."""
        with pytest.raises(ValueError, match="does not exist"):
            StaticFiles(
                mounts=[
                    StaticMount("/static", Path("/nonexistent")),
                ]
            )

    def test_mount_sorting(self, temp_static_dir):
        """Test mounts are sorted by length (longest first)."""
        handler = StaticFiles(
            mounts=[
                StaticMount("/a", temp_static_dir),
                StaticMount("/assets/images", temp_static_dir),
                StaticMount("/assets", temp_static_dir),
            ]
        )

        # Should be sorted: /assets/images, /assets, /a
        assert handler._mounts[0].url_path == "/assets/images"
        assert handler._mounts[1].url_path == "/assets"
        assert handler._mounts[2].url_path == "/a"


class TestFileResolution:
    """Tests for file path resolution."""

    def test_resolve_simple_file(self, static_handler, temp_static_dir):
        """Test resolving a simple file."""
        file = static_handler._resolve_file("/static/index.html", None)

        assert file is not None
        assert file.path == temp_static_dir / "index.html"
        assert file.mime_type == "text/html"
        assert file.size > 0
        assert file.etag.startswith('W/"')

    def test_resolve_subdirectory_file(self, static_handler, temp_static_dir):
        """Test resolving file in subdirectory."""
        file = static_handler._resolve_file("/static/assets/image.png", None)

        assert file is not None
        assert file.path == temp_static_dir / "assets" / "image.png"
        assert file.mime_type == "image/png"

    def test_resolve_directory_index(self, static_handler, temp_static_dir):
        """Test resolving directory returns index.html."""
        file = static_handler._resolve_file("/static/", None)

        assert file is not None
        assert file.path == temp_static_dir / "index.html"

    def test_resolve_root_path(self, temp_static_dir):
        """Test root mount / resolves / and /docs/ to index.html."""
        handler = StaticFiles(mounts=[StaticMount("/", temp_static_dir)])

        file_root = handler._resolve_file("/", None)
        assert file_root is not None
        assert file_root.path == temp_static_dir / "index.html"

        file_docs = handler._resolve_file("/docs/", None)
        assert file_docs is not None
        assert file_docs.path == temp_static_dir / "docs" / "index.html"

    def test_resolve_nonexistent_file(self, static_handler):
        """Test resolving non-existent file returns None."""
        file = static_handler._resolve_file("/static/nonexistent.txt", None)

        assert file is None

    def test_path_traversal_blocked(self, static_handler):
        """Test path traversal attempts are blocked."""
        file = static_handler._resolve_file("/static/../../../etc/passwd", None)

        assert file is None

    def test_hidden_files_blocked(self, static_handler):
        """Test hidden files are blocked."""
        file = static_handler._resolve_file("/static/.env", None)

        assert file is None

    def test_mime_type_detection(self, static_handler):
        """Test MIME type detection."""
        tests = [
            ("/static/index.html", "text/html"),
            ("/static/style.css", "text/css"),
            ("/static/script.js", "text/javascript"),
            ("/static/assets/image.png", "image/png"),
        ]

        for path, expected_mime in tests:
            file = static_handler._resolve_file(path, None)
            assert file is not None
            assert file.mime_type == expected_mime


class TestETagGeneration:
    """Tests for ETag generation."""

    def test_etag_format(self, static_handler):
        """Test ETag has correct format."""
        file = static_handler._resolve_file("/static/index.html", None)

        assert file is not None
        assert file.etag.startswith('W/"')
        assert file.etag.endswith('"')
        assert "-" in file.etag  # Contains mtime-size

    def test_etag_deterministic(self, static_handler):
        """Test ETag is deterministic for same file."""
        file1 = static_handler._resolve_file("/static/index.html", None)
        file2 = static_handler._resolve_file("/static/index.html", None)

        assert file1 is not None
        assert file2 is not None
        assert file1.etag == file2.etag

    def test_etag_differs_for_compressed_variant(self, static_handler):
        """Compressed and uncompressed variants have different ETags (RFC 7232)."""
        plain = static_handler._resolve_file("/static/style.css", None)
        gzipped = static_handler._resolve_file("/static/style.css", b"gzip")

        assert plain is not None
        assert gzipped is not None
        assert plain.etag != gzipped.etag
        assert "gzip" in gzipped.etag
        assert "gzip" not in plain.etag


class TestPrecompressedFiles:
    """Tests for precompressed file serving."""

    def test_precompressed_gzip(self, static_handler, temp_static_dir):
        """Test serving .gz variant when client supports gzip."""
        file = static_handler._resolve_file("/static/style.css", b"gzip, deflate")

        assert file is not None
        assert file.path == temp_static_dir / "style.css.gz"
        assert file.encoding == "gzip"
        # MIME type should be original file, not .gz
        assert file.mime_type == "text/css"

    def test_precompressed_no_encoding(self, static_handler, temp_static_dir):
        """Test serving original when no Accept-Encoding."""
        file = static_handler._resolve_file("/static/style.css", None)

        assert file is not None
        assert file.path == temp_static_dir / "style.css"
        assert file.encoding is None

    def test_precompressed_unsupported_encoding(self, static_handler, temp_static_dir):
        """Test serving original when client doesn't support compression."""
        file = static_handler._resolve_file("/static/style.css", b"br")

        assert file is not None
        assert file.path == temp_static_dir / "style.css"
        assert file.encoding is None


class TestRangeRequests:
    """Tests for Range request parsing."""

    def test_parse_simple_range(self, static_handler):
        """Test parsing simple range."""
        ranges = static_handler._parse_range_header("bytes=0-499", 1000)

        assert ranges == [(0, 499)]

    def test_parse_suffix_range(self, static_handler):
        """Test parsing suffix range (last N bytes)."""
        ranges = static_handler._parse_range_header("bytes=-500", 1000)

        assert ranges == [(500, 999)]

    def test_parse_open_ended_range(self, static_handler):
        """Test parsing open-ended range."""
        ranges = static_handler._parse_range_header("bytes=500-", 1000)

        assert ranges == [(500, 999)]

    def test_parse_invalid_range(self, static_handler):
        """Test invalid range returns None."""
        assert static_handler._parse_range_header("invalid", 1000) is None
        assert static_handler._parse_range_header("bytes=", 1000) is None
        assert static_handler._parse_range_header("bytes=abc-def", 1000) is None

    def test_parse_unsatisfiable_range(self, static_handler):
        """Test unsatisfiable range returns None."""
        # Start beyond file size
        assert static_handler._parse_range_header("bytes=2000-3000", 1000) is None
        # Start > end
        assert static_handler._parse_range_header("bytes=500-100", 1000) is None

    def test_parse_multiple_ranges(self, static_handler):
        """Test parsing multiple comma-separated ranges."""
        ranges = static_handler._parse_range_header("bytes=0-9,20-29", 100)

        assert ranges == [(0, 9), (20, 29)]

    def test_parse_three_ranges(self, static_handler):
        """Test parsing three ranges."""
        ranges = static_handler._parse_range_header("bytes=0-4,10-14,90-99", 100)

        assert ranges == [(0, 4), (10, 14), (90, 99)]


class TestMultipartRangeResponse:
    """Tests for multipart/byteranges 206 responses (RFC 7233 §4.1)."""

    @pytest.fixture
    def range_dir(self, tmp_path):
        # 26 bytes: "abcdefghijklmnopqrstuvwxyz"
        (tmp_path / "alpha.txt").write_text("abcdefghijklmnopqrstuvwxyz")
        return tmp_path

    @pytest.fixture
    def range_handler(self, range_dir):
        return StaticFiles(mounts=[StaticMount(url_path="/files", directory=range_dir)])

    def _scope(self, path="/files/alpha.txt", range_header=None):
        scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": [],
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
        }
        if range_header:
            scope["headers"] = [(b"range", range_header.encode("latin1"))]
        return scope

    @pytest.mark.asyncio
    async def test_single_range_no_multipart(self, range_handler):
        """Single range produces simple 206, not multipart."""
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        await range_handler(self._scope(range_header="bytes=0-4"), None, mock_send)

        assert sent[0]["status"] == 206
        ct = dict(sent[0]["headers"]).get(b"content-type")
        assert ct == b"text/plain"  # Not multipart
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert body == b"abcde"

    @pytest.mark.asyncio
    async def test_multipart_two_ranges(self, range_handler):
        """Two ranges produce multipart/byteranges response."""
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        await range_handler(self._scope(range_header="bytes=0-4,21-25"), None, mock_send)

        assert sent[0]["status"] == 206
        headers_dict = dict(sent[0]["headers"])
        ct = headers_dict[b"content-type"].decode("latin1")
        assert ct.startswith("multipart/byteranges; boundary=")

        # Extract boundary
        boundary = ct.split("boundary=")[1]

        # Collect full body
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        body_str = body.decode("latin1")

        # Verify structure
        assert f"--{boundary}" in body_str
        assert f"--{boundary}--" in body_str
        assert "Content-Range: bytes 0-4/26" in body_str
        assert "Content-Range: bytes 21-25/26" in body_str
        assert "abcde" in body_str
        assert "vwxyz" in body_str

    @pytest.mark.asyncio
    async def test_multipart_three_ranges(self, range_handler):
        """Three ranges produce correct multipart body."""
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        await range_handler(self._scope(range_header="bytes=0-2,10-12,23-25"), None, mock_send)

        assert sent[0]["status"] == 206
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        body_str = body.decode("latin1")

        assert "Content-Range: bytes 0-2/26" in body_str
        assert "Content-Range: bytes 10-12/26" in body_str
        assert "Content-Range: bytes 23-25/26" in body_str
        assert "abc" in body_str
        assert "klm" in body_str
        assert "xyz" in body_str

    @pytest.mark.asyncio
    async def test_multipart_content_length_accurate(self, range_handler):
        """Content-Length matches actual body size."""
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        await range_handler(self._scope(range_header="bytes=0-4,21-25"), None, mock_send)

        headers_dict = dict(sent[0]["headers"])
        declared_length = int(headers_dict[b"content-length"])
        actual_body = b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )
        assert len(actual_body) == declared_length

    @pytest.mark.asyncio
    async def test_multipart_last_frame_more_body_false(self, range_handler):
        """Last body frame has more_body=False."""
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        await range_handler(self._scope(range_header="bytes=0-4,21-25"), None, mock_send)

        body_frames = [m for m in sent if m["type"] == "http.response.body"]
        assert body_frames[-1]["more_body"] is False
        # All preceding body frames should have more_body=True
        for frame in body_frames[:-1]:
            assert frame["more_body"] is True

    @pytest.mark.asyncio
    async def test_multipart_has_etag(self, range_handler):
        """Multipart response includes ETag header."""
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        await range_handler(self._scope(range_header="bytes=0-4,21-25"), None, mock_send)

        headers_dict = dict(sent[0]["headers"])
        assert b"etag" in headers_dict


class TestCreateStaticHandler:
    """Tests for create_static_handler helper."""

    def test_create_from_dict(self, temp_static_dir):
        """Test creating handler from dict."""
        handler = create_static_handler(
            {
                "/static": str(temp_static_dir),
            }
        )

        assert len(handler._mounts) == 1
        assert handler._mounts[0].url_path == "/static"

    def test_create_multiple_mounts(self, temp_static_dir):
        """Test creating handler with multiple mounts."""
        handler = create_static_handler(
            {
                "/static": str(temp_static_dir),
                "/assets": str(temp_static_dir),
            }
        )

        assert len(handler._mounts) == 2


class TestConditionalRequests:
    """Tests for conditional requests (If-None-Match)."""

    def test_not_modified_match(self, static_handler):
        """Test 304 response when ETag matches."""
        # First, get the ETag
        file = static_handler._resolve_file("/static/index.html", None)
        assert file is not None

        # Then check with If-None-Match
        headers = [(b"if-none-match", file.etag.encode("latin1"))]
        is_not_modified = static_handler._check_not_modified(headers, file)

        assert is_not_modified is True

    def test_not_modified_no_match(self, static_handler):
        """Test full response when ETag doesn't match."""
        file = static_handler._resolve_file("/static/index.html", None)
        assert file is not None

        # Check with different ETag
        headers = [(b"if-none-match", b'W/"different-etag"')]
        is_not_modified = static_handler._check_not_modified(headers, file)

        assert is_not_modified is False

    def test_not_modified_no_header(self, static_handler):
        """Test full response when no If-None-Match header."""
        file = static_handler._resolve_file("/static/index.html", None)
        assert file is not None

        headers = []
        is_not_modified = static_handler._check_not_modified(headers, file)

        assert is_not_modified is False
