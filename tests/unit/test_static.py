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
        file = static_handler._resolve_file("/static/index.html", [])

        assert file is not None
        assert file.path == temp_static_dir / "index.html"
        assert file.mime_type == "text/html"
        assert file.size > 0
        assert file.etag.startswith('W/"')

    def test_resolve_subdirectory_file(self, static_handler, temp_static_dir):
        """Test resolving file in subdirectory."""
        file = static_handler._resolve_file("/static/assets/image.png", [])

        assert file is not None
        assert file.path == temp_static_dir / "assets" / "image.png"
        assert file.mime_type == "image/png"

    def test_resolve_directory_index(self, static_handler, temp_static_dir):
        """Test resolving directory returns index.html."""
        file = static_handler._resolve_file("/static/", [])

        assert file is not None
        assert file.path == temp_static_dir / "index.html"

    def test_resolve_root_path(self, temp_static_dir):
        """Test root mount / resolves / and /docs/ to index.html."""
        handler = StaticFiles(mounts=[StaticMount("/", temp_static_dir)])

        file_root = handler._resolve_file("/", [])
        assert file_root is not None
        assert file_root.path == temp_static_dir / "index.html"

        file_docs = handler._resolve_file("/docs/", [])
        assert file_docs is not None
        assert file_docs.path == temp_static_dir / "docs" / "index.html"

    def test_resolve_nonexistent_file(self, static_handler):
        """Test resolving non-existent file returns None."""
        file = static_handler._resolve_file("/static/nonexistent.txt", [])

        assert file is None

    def test_path_traversal_blocked(self, static_handler):
        """Test path traversal attempts are blocked."""
        file = static_handler._resolve_file("/static/../../../etc/passwd", [])

        assert file is None

    def test_hidden_files_blocked(self, static_handler):
        """Test hidden files are blocked."""
        file = static_handler._resolve_file("/static/.env", [])

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
            file = static_handler._resolve_file(path, [])
            assert file is not None
            assert file.mime_type == expected_mime


class TestETagGeneration:
    """Tests for ETag generation."""

    def test_etag_format(self, static_handler):
        """Test ETag has correct format."""
        file = static_handler._resolve_file("/static/index.html", [])

        assert file is not None
        assert file.etag.startswith('W/"')
        assert file.etag.endswith('"')
        assert "-" in file.etag  # Contains mtime-size

    def test_etag_deterministic(self, static_handler):
        """Test ETag is deterministic for same file."""
        file1 = static_handler._resolve_file("/static/index.html", [])
        file2 = static_handler._resolve_file("/static/index.html", [])

        assert file1 is not None
        assert file2 is not None
        assert file1.etag == file2.etag


class TestPrecompressedFiles:
    """Tests for precompressed file serving."""

    def test_precompressed_gzip(self, static_handler, temp_static_dir):
        """Test serving .gz variant when client supports gzip."""
        headers = [(b"accept-encoding", b"gzip, deflate")]

        file = static_handler._resolve_file("/static/style.css", headers)

        assert file is not None
        assert file.path == temp_static_dir / "style.css.gz"
        assert file.encoding == "gzip"
        # MIME type should be original file, not .gz
        assert file.mime_type == "text/css"

    def test_precompressed_no_encoding(self, static_handler, temp_static_dir):
        """Test serving original when no Accept-Encoding."""
        headers = []

        file = static_handler._resolve_file("/static/style.css", headers)

        assert file is not None
        assert file.path == temp_static_dir / "style.css"
        assert file.encoding is None

    def test_precompressed_unsupported_encoding(self, static_handler, temp_static_dir):
        """Test serving original when client doesn't support compression."""
        headers = [(b"accept-encoding", b"br")]  # Brotli only

        file = static_handler._resolve_file("/static/style.css", headers)

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


class TestCreateStaticHandler:
    """Tests for create_static_handler helper."""

    def test_create_from_dict(self, temp_static_dir):
        """Test creating handler from dict."""
        handler = create_static_handler({
            "/static": str(temp_static_dir),
        })

        assert len(handler._mounts) == 1
        assert handler._mounts[0].url_path == "/static"

    def test_create_multiple_mounts(self, temp_static_dir):
        """Test creating handler with multiple mounts."""
        handler = create_static_handler({
            "/static": str(temp_static_dir),
            "/assets": str(temp_static_dir),
        })

        assert len(handler._mounts) == 2


class TestConditionalRequests:
    """Tests for conditional requests (If-None-Match)."""

    def test_not_modified_match(self, static_handler):
        """Test 304 response when ETag matches."""
        # First, get the ETag
        file = static_handler._resolve_file("/static/index.html", [])
        assert file is not None

        # Then check with If-None-Match
        headers = [(b"if-none-match", file.etag.encode("latin1"))]
        is_not_modified = static_handler._check_not_modified(headers, file)

        assert is_not_modified is True

    def test_not_modified_no_match(self, static_handler):
        """Test full response when ETag doesn't match."""
        file = static_handler._resolve_file("/static/index.html", [])
        assert file is not None

        # Check with different ETag
        headers = [(b"if-none-match", b'W/"different-etag"')]
        is_not_modified = static_handler._check_not_modified(headers, file)

        assert is_not_modified is False

    def test_not_modified_no_header(self, static_handler):
        """Test full response when no If-None-Match header."""
        file = static_handler._resolve_file("/static/index.html", [])
        assert file is not None

        headers = []
        is_not_modified = static_handler._check_not_modified(headers, file)

        assert is_not_modified is False
