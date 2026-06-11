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


class TestHiddenMountDirectory:
    """Regression tests for #74: mounts rooted under a hidden (dot) path.

    The hidden-component guard must only inspect path components *below* the
    mount root. A directory the operator explicitly mounted must be fully
    serveable even if one of its ancestors is a dotfile dir (e.g. Bengal's
    ``<root>/.bengal/staging`` dev double-buffer).
    """

    def _make_handler(self, root: Path) -> StaticFiles:
        return create_static_handler({"/": str(root)})

    def test_serves_file_under_hidden_mount_dir(self, tmp_path):
        """Files under a mount whose ancestor is a dotfile dir are served."""
        mount_dir = tmp_path / ".bengal" / "staging"
        css = mount_dir / "assets" / "style.css"
        css.parent.mkdir(parents=True)
        css.write_text("body{}")

        handler = self._make_handler(mount_dir)
        file = handler._resolve_file("/assets/style.css", None)

        assert file is not None
        assert file.path == mount_dir / "assets" / "style.css"

    def test_serves_root_file_under_hidden_mount_dir(self, tmp_path):
        """A file directly under a hidden mount root is served."""
        mount_dir = tmp_path / ".bengal" / "staging"
        mount_dir.mkdir(parents=True)
        (mount_dir / "index.html").write_text("<h1>Hi</h1>")

        handler = self._make_handler(mount_dir)
        file = handler._resolve_file("/index.html", None)

        assert file is not None
        assert file.path == mount_dir / "index.html"

    def test_hidden_file_below_hidden_mount_still_blocked(self, tmp_path):
        """A dotfile *below* the mount root is still blocked (security)."""
        mount_dir = tmp_path / ".bengal" / "staging"
        mount_dir.mkdir(parents=True)
        (mount_dir / ".env").write_text("SECRET=abc123")
        (mount_dir / "nested").mkdir()
        (mount_dir / "nested" / ".secret").write_text("nope")

        handler = self._make_handler(mount_dir)

        assert handler._resolve_file("/.env", None) is None
        assert handler._resolve_file("/nested/.secret", None) is None

    def test_well_known_below_hidden_mount_allowed(self, tmp_path):
        """.well-known is still allowed below a hidden mount root."""
        mount_dir = tmp_path / ".bengal" / "staging"
        wk = mount_dir / ".well-known" / "security.txt"
        wk.parent.mkdir(parents=True)
        wk.write_text("Contact: mailto:a@b.c")

        handler = self._make_handler(mount_dir)
        file = handler._resolve_file("/.well-known/security.txt", None)

        assert file is not None
        assert file.path == wk

    def test_precompressed_served_under_hidden_mount_dir(self, tmp_path):
        """Precompressed variants are served under a hidden mount root.

        Exercises the duplicated guard in ``_validate_precompressed``.
        """
        mount_dir = tmp_path / ".bengal" / "staging"
        mount_dir.mkdir(parents=True)
        (mount_dir / "style.css").write_text("body{}")
        (mount_dir / "style.css.gz").write_bytes(b"\x1f\x8b\x08\x00compressed")

        handler = self._make_handler(mount_dir)
        file = handler._resolve_file("/style.css", b"gzip")

        assert file is not None
        assert file.encoding == "gzip"
        assert file.path == mount_dir / "style.css.gz"


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

    def test_precompressed_gzip_qvalue_zero(self, static_handler, temp_static_dir):
        """gzip;q=0 explicitly declines gzip -> serve identity, not .gz."""
        file = static_handler._resolve_file("/static/style.css", b"gzip;q=0")

        assert file is not None
        assert file.path == temp_static_dir / "style.css"
        assert file.encoding is None

    def test_precompressed_zstd_preferred_over_gzip(self, temp_static_dir):
        """When both variants exist, zstd wins over gzip by priority."""
        from pounce._compression import _HAS_ZSTD

        if not _HAS_ZSTD:
            pytest.skip("zstd not available")

        (temp_static_dir / "style.css.zst").write_bytes(b"\x28\xb5\x2f\xfd zstd-payload")
        handler = create_static_handler({"/static": str(temp_static_dir)})

        file = handler._resolve_file("/static/style.css", b"gzip, zstd;q=0.9")
        assert file is not None
        assert file.encoding == "zstd"
        assert file.path == temp_static_dir / "style.css.zst"

    def test_precompressed_zstd_declined_falls_back_to_gzip(self, temp_static_dir):
        """zstd;q=0 with gzip accepted falls back to the .gz variant."""
        (temp_static_dir / "style.css.zst").write_bytes(b"\x28\xb5\x2f\xfd zstd-payload")
        handler = create_static_handler({"/static": str(temp_static_dir)})

        file = handler._resolve_file("/static/style.css", b"zstd;q=0, gzip")
        assert file is not None
        assert file.encoding == "gzip"
        assert file.path == temp_static_dir / "style.css.gz"

    def test_precompressed_missing_top_priority_falls_back(self, static_handler, temp_static_dir):
        """zstd accepted but only .gz exists -> serve gzip (no silent identity)."""
        # Only style.css.gz exists in the fixture (no .zst).
        file = static_handler._resolve_file("/static/style.css", b"zstd, gzip")
        assert file is not None
        assert file.encoding == "gzip"
        assert file.path == temp_static_dir / "style.css.gz"


class TestVaryHeader:
    """Vary: accept-encoding is emitted on every response from a precompressed mount."""

    @pytest.fixture
    def vary_handler(self, temp_static_dir):
        return create_static_handler({"/static": str(temp_static_dir)})

    @pytest.fixture
    def no_precompress_handler(self, temp_static_dir):
        return create_static_handler({"/static": str(temp_static_dir)}, precompressed=False)

    def _scope(self, path, method="GET", headers=None):
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "headers": headers or [],
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
        }
        return scope

    async def _run(self, handler, scope):
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        await handler(scope, None, mock_send)
        return sent

    @pytest.mark.asyncio
    async def test_vary_present_on_identity_200(self, vary_handler):
        """Absent Accept-Encoding still serves identity but advertises Vary."""
        sent = await self._run(vary_handler, self._scope("/static/style.css"))

        assert sent[0]["status"] == 200
        headers = dict(sent[0]["headers"])
        assert b"content-encoding" not in headers  # identity
        assert headers.get(b"vary") == b"accept-encoding"

    @pytest.mark.asyncio
    async def test_no_vary_when_precompress_disabled(self, no_precompress_handler):
        """A non-negotiating mount does not emit Vary (no content negotiation)."""
        sent = await self._run(no_precompress_handler, self._scope("/static/style.css"))

        assert sent[0]["status"] == 200
        headers = dict(sent[0]["headers"])
        assert b"vary" not in headers

    @pytest.mark.asyncio
    async def test_vary_present_on_304(self, vary_handler):
        """304 from a precompressed mount carries Vary (RFC 7232 §4.1)."""
        file = vary_handler._resolve_file("/static/style.css", None)
        assert file is not None
        headers = [(b"if-none-match", file.etag.encode("latin1"))]
        sent = await self._run(vary_handler, self._scope("/static/style.css", headers=headers))

        assert sent[0]["status"] == 304
        assert dict(sent[0]["headers"]).get(b"vary") == b"accept-encoding"

    @pytest.mark.asyncio
    async def test_vary_present_on_206_single(self, vary_handler):
        """206 single-range from a precompressed mount carries Vary."""
        headers = [(b"range", b"bytes=0-4")]
        sent = await self._run(vary_handler, self._scope("/static/style.css", headers=headers))

        assert sent[0]["status"] == 206
        assert dict(sent[0]["headers"]).get(b"vary") == b"accept-encoding"

    @pytest.mark.asyncio
    async def test_vary_present_on_416(self, vary_handler):
        """416 from a precompressed mount carries Vary."""
        headers = [(b"range", b"bytes=9999-99999")]
        sent = await self._run(vary_handler, self._scope("/static/style.css", headers=headers))

        assert sent[0]["status"] == 416
        assert dict(sent[0]["headers"]).get(b"vary") == b"accept-encoding"


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
        """Valid-but-unsatisfiable ranges signal 416; malformed ones are ignored."""
        from pounce._static import _RANGE_NOT_SATISFIABLE

        # Start beyond file size -> 416 (RFC 7233 §4.4), not a silent full 200.
        assert static_handler._parse_range_header("bytes=2000-3000", 1000) is _RANGE_NOT_SATISFIABLE
        # Start exactly at file size is also unsatisfiable.
        assert static_handler._parse_range_header("bytes=1000-1000", 1000) is _RANGE_NOT_SATISFIABLE
        # Start > end is malformed -> ignore the header (serve full 200).
        assert static_handler._parse_range_header("bytes=500-100", 1000) is None

    def test_parse_end_past_eof_clamped(self, static_handler):
        """An explicit end past EOF is clamped, not rejected."""
        # bytes=500-999 on a 1000-byte file: end == file_size-1, satisfiable.
        assert static_handler._parse_range_header("bytes=500-999", 1000) == [(500, 999)]
        # bytes=900-5000 clamps the end to 999.
        assert static_handler._parse_range_header("bytes=900-5000", 1000) == [(900, 999)]
        # Open-ended bytes=500- runs to EOF.
        assert static_handler._parse_range_header("bytes=500-", 1000) == [(500, 999)]

    def test_parse_empty_file_range_unsatisfiable(self, static_handler):
        """Any byte range against an empty file is unsatisfiable."""
        from pounce._static import _RANGE_NOT_SATISFIABLE

        assert static_handler._parse_range_header("bytes=0-0", 0) is _RANGE_NOT_SATISFIABLE

    def test_parse_too_many_ranges_ignored(self, static_handler):
        """A request with more than _MAX_RANGES parts is ignored (served as 200)."""
        from pounce._static import _MAX_RANGES

        spec = "bytes=" + ",".join(f"{i}-{i}" for i in range(_MAX_RANGES + 5))
        assert static_handler._parse_range_header(spec, 1000) is None
        # At the cap it is still honored (and coalesced).
        ok = "bytes=" + ",".join(f"{i * 2}-{i * 2}" for i in range(_MAX_RANGES))
        result = static_handler._parse_range_header(ok, 1000)
        assert isinstance(result, list)
        assert len(result) == _MAX_RANGES

    def test_parse_overlapping_ranges_coalesced(self, static_handler):
        """Overlapping and adjacent ranges are merged."""
        # Overlapping: 0-50 and 40-90 -> 0-90.
        assert static_handler._parse_range_header("bytes=0-50,40-90", 1000) == [(0, 90)]
        # Adjacent: 0-9 and 10-19 -> 0-19.
        assert static_handler._parse_range_header("bytes=0-9,10-19", 1000) == [(0, 19)]
        # Out of order, disjoint, with a duplicate -> sorted and merged.
        assert static_handler._parse_range_header("bytes=80-89,0-9,0-9", 1000) == [(0, 9), (80, 89)]

    def test_parse_suffix_zero_malformed(self, static_handler):
        """bytes=-0 is malformed and ignored (no negative-length range)."""
        assert static_handler._parse_range_header("bytes=-0", 1000) is None

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

    @pytest.mark.asyncio
    async def test_unsatisfiable_range_returns_416(self, range_handler):
        """A range entirely past EOF returns 416 with Content-Range: bytes */size."""
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        # alpha.txt is 26 bytes; bytes=100-200 is fully out of range.
        await range_handler(self._scope(range_header="bytes=100-200"), None, mock_send)

        assert sent[0]["status"] == 416
        headers_dict = dict(sent[0]["headers"])
        assert headers_dict[b"content-range"] == b"bytes */26"
        assert headers_dict[b"content-length"] == b"0"
        assert headers_dict[b"accept-ranges"] == b"bytes"
        assert b"etag" in headers_dict
        # Body must be empty.
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert body == b""

    @pytest.mark.asyncio
    async def test_malformed_range_serves_full_200(self, range_handler):
        """A reversed (malformed) range is ignored: full 200, not 416."""
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        await range_handler(self._scope(range_header="bytes=20-5"), None, mock_send)

        assert sent[0]["status"] == 200
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert body == b"abcdefghijklmnopqrstuvwxyz"

    @pytest.mark.asyncio
    async def test_many_tiny_ranges_not_amplified(self, range_handler):
        """A flood of tiny ranges does not produce a response larger than the file."""
        from pounce._static import _MAX_RANGES

        # Far more ranges than the cap: must be ignored and served as a 200.
        spec = "bytes=" + ",".join("0-0" for _ in range(_MAX_RANGES + 50))
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        await range_handler(self._scope(range_header=spec), None, mock_send)

        assert sent[0]["status"] == 200
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        # Response body is the file itself (26 bytes), not 26 * many multipart parts.
        assert len(body) == 26

    @pytest.mark.asyncio
    async def test_overlapping_ranges_merged_in_response(self, range_handler):
        """Overlapping ranges coalesce into a single 206 (not multipart)."""
        sent = []

        async def mock_send(msg):
            sent.append(msg)

        # 0-4 and 3-9 overlap -> single range 0-9.
        await range_handler(self._scope(range_header="bytes=0-4,3-9"), None, mock_send)

        assert sent[0]["status"] == 206
        headers_dict = dict(sent[0]["headers"])
        # Coalesced to one range -> simple 206, not multipart.
        assert headers_dict[b"content-range"] == b"bytes 0-9/26"
        body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        assert body == b"abcdefghij"


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
