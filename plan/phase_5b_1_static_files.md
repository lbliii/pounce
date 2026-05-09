# Task 1.1: Static File Serving Implementation

**Status:** Historical implementation plan. Static serving exists, but the active
roadmap now treats Bengal static serving as a public-contract proof and hardening
effort. See
[../docs/plans/ironclad-bengal-chirp.md](../docs/plans/ironclad-bengal-chirp.md).

**Historical note:** This file preserves the original design intent. Current
work should be driven by code, tests, docs, and the active steward synthesis.

**Priority:** P0 — Blocks Bengal SSG deployments
**Complexity:** Medium
**Estimated Time:** 3-5 days
**Dependencies:** None

---

## Goals

1. Serve static files efficiently without external web server
2. Zero-copy sendfile for large files
3. Smart caching with ETag and 304 Not Modified
4. Range request support for media streaming
5. Precompressed file serving (.gz, .zst)
6. Security: path traversal prevention, hidden file protection

---

## Architecture

### Module: `pounce/_static.py`

```python
"""
Static file serving with modern optimizations.

Designed for Bengal SSG output and Chirp static assets. Supports:
- Zero-copy sendfile (os.sendfile on Linux, sendfile on macOS)
- ETag generation from mtime + size
- 304 Not Modified responses
- Range requests (Accept-Ranges, Content-Range, 206)
- Precompressed file serving (.gz, .zst variants)
- MIME type detection
- Security: path traversal prevention, hidden file blocking
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

@dataclass(frozen=True, slots=True)
class StaticMount:
    """Configuration for a static file mount point."""
    url_path: str  # e.g., "/static"
    directory: Path  # e.g., Path("./public")
    cache_control: str = "public, max-age=3600"
    precompressed: bool = True  # Serve .gz/.zst if available
    follow_symlinks: bool = False
    index_file: str | None = "index.html"  # Directory index

class StaticFileHandler:
    """ASGI-compatible static file handler."""

    def __init__(self, mounts: list[StaticMount]) -> None:
        self.mounts = self._prepare_mounts(mounts)

    async def __call__(
        self, scope: dict, receive: Callable, send: Callable
    ) -> bool:
        """
        Handle static file request if path matches a mount.

        Returns:
            True if handled, False if not a static file (pass to app).
        """
        ...

    def _resolve_file(self, url_path: str) -> StaticFile | None:
        """Resolve URL path to file, check security, find precompressed."""
        ...

    async def _send_file(
        self, file: StaticFile, scope: dict, send: Callable
    ) -> None:
        """Send file with sendfile, ETag, Range support."""
        ...

    async def _send_304(
        self, file: StaticFile, send: Callable
    ) -> None:
        """Send 304 Not Modified response."""
        ...

    async def _send_206(
        self, file: StaticFile, ranges: list[tuple[int, int]], send: Callable
    ) -> None:
        """Send 206 Partial Content response."""
        ...

@dataclass(frozen=True, slots=True)
class StaticFile:
    """Resolved static file with metadata."""
    path: Path
    size: int
    mtime: float
    mime_type: str
    etag: str
    encoding: str | None  # "gzip", "zstd", or None
```

---

## Implementation Details

### 1. Path Resolution and Security

```python
def _resolve_file(self, url_path: str) -> StaticFile | None:
    """
    Security checks:
    1. Normalize path (remove .., ., //)
    2. Verify resolved path is within mount directory
    3. Block hidden files (starting with .)
    4. Check file exists and is regular file
    5. Respect follow_symlinks setting

    Precompressed handling:
    1. Check Accept-Encoding header
    2. If "gzip" supported, try file.gz (if mtime > original)
    3. If "zstd" supported, try file.zst (if mtime > original)
    4. Fall back to original file
    """
    # Normalize URL path
    url_path = os.path.normpath(url_path)

    # Find matching mount
    for mount in self.mounts:
        if url_path.startswith(mount.url_path):
            relative_path = url_path[len(mount.url_path):].lstrip("/")
            file_path = mount.directory / relative_path

            # Security: prevent path traversal
            try:
                resolved = file_path.resolve()
                if not resolved.is_relative_to(mount.directory.resolve()):
                    return None  # Outside mount directory
            except (ValueError, OSError):
                return None

            # Block hidden files
            if any(part.startswith(".") for part in resolved.parts):
                return None

            # Check symlinks
            if resolved.is_symlink() and not mount.follow_symlinks:
                return None

            # Directory index
            if resolved.is_dir() and mount.index_file:
                resolved = resolved / mount.index_file

            # Must be regular file
            if not resolved.is_file():
                return None

            # Check for precompressed variants
            # (implementation below)

            return StaticFile(...)
```

### 2. ETag Generation

```python
def _generate_etag(path: Path, mtime: float, size: int) -> str:
    """
    Generate ETag from mtime + size (cheap, collision-resistant).

    Format: W/"<mtime_hex>-<size_hex>"
    Weak ETag (W/) because we use mtime not content hash.
    """
    mtime_hex = hex(int(mtime * 1_000_000))[2:]
    size_hex = hex(size)[2:]
    return f'W/"{mtime_hex}-{size_hex}"'
```

### 3. Zero-Copy Sendfile

```python
async def _send_file_body(
    self, file: StaticFile, fd: int, offset: int, count: int, send: Callable
) -> None:
    """
    Use os.sendfile for zero-copy transfer on Linux/macOS.

    Falls back to chunked read/send on Windows or if sendfile fails.
    """
    import os
    import sys

    if sys.platform == "linux" or sys.platform == "darwin":
        try:
            # Linux: os.sendfile(out_fd, in_fd, offset, count)
            # macOS: different signature, use ctypes wrapper
            sent = 0
            while sent < count:
                chunk = min(count - sent, 1_048_576)  # 1MB chunks
                # This is pseudo-code; real impl needs socket fd
                n = os.sendfile(socket_fd, fd, offset + sent, chunk)
                if n == 0:
                    break
                sent += n

                # Send as ASGI http.response.body chunks
                # (actual impl passes chunks via send())
        except (OSError, AttributeError):
            # Fall back to read/send
            await self._send_file_chunked(file, fd, offset, count, send)
    else:
        # Windows: no sendfile, use chunked
        await self._send_file_chunked(file, fd, offset, count, send)

async def _send_file_chunked(
    self, file: StaticFile, fd: int, offset: int, count: int, send: Callable
) -> None:
    """Chunked read/send fallback."""
    chunk_size = 65536  # 64 KB
    os.lseek(fd, offset, os.SEEK_SET)
    remaining = count

    while remaining > 0:
        chunk = os.read(fd, min(chunk_size, remaining))
        if not chunk:
            break

        await send({
            "type": "http.response.body",
            "body": chunk,
            "more_body": remaining > len(chunk),
        })
        remaining -= len(chunk)
```

### 4. Precompressed File Selection

```python
def _find_precompressed(
    self, path: Path, accept_encoding: str
) -> tuple[Path, str | None]:
    """
    Check for precompressed variants (.gz, .zst) and return best match.

    Priority: zstd > gzip > identity
    Only use precompressed if mtime > original (fresher).

    Returns:
        (file_path, encoding) where encoding is "gzip", "zstd", or None
    """
    if not self.mount.precompressed:
        return (path, None)

    original_mtime = path.stat().st_mtime
    encodings = []

    if "zstd" in accept_encoding:
        zst_path = path.with_suffix(path.suffix + ".zst")
        if zst_path.exists() and zst_path.stat().st_mtime >= original_mtime:
            encodings.append((zst_path, "zstd"))

    if "gzip" in accept_encoding:
        gz_path = path.with_suffix(path.suffix + ".gz")
        if gz_path.exists() and gz_path.stat().st_mtime >= original_mtime:
            encodings.append((gz_path, "gzip"))

    # Return best encoding or original
    if encodings:
        return encodings[0]  # Already prioritized
    return (path, None)
```

### 5. Range Request Handling

```python
def _parse_range_header(
    self, range_header: str, file_size: int
) -> list[tuple[int, int]] | None:
    """
    Parse Range header and return list of (start, end) byte ranges.

    Format: "bytes=0-499" or "bytes=500-999" or "bytes=-500"
    Returns None if invalid or unsatisfiable.
    """
    if not range_header.startswith("bytes="):
        return None

    ranges = []
    for range_spec in range_header[6:].split(","):
        range_spec = range_spec.strip()

        if "-" not in range_spec:
            return None

        start_str, end_str = range_spec.split("-", 1)

        if start_str and end_str:
            # bytes=0-499
            start = int(start_str)
            end = int(end_str)
        elif start_str:
            # bytes=500- (from 500 to end)
            start = int(start_str)
            end = file_size - 1
        elif end_str:
            # bytes=-500 (last 500 bytes)
            start = file_size - int(end_str)
            end = file_size - 1
        else:
            return None

        # Validate range
        if start < 0 or end >= file_size or start > end:
            return None

        ranges.append((start, end))

    return ranges if ranges else None
```

---

## Integration with Worker

### Option A: Before ASGI App (Recommended)

```python
# In worker.py, before app(scope, receive, send)

# Check if static file handler is configured
if self.config.static_files:
    handled = await self.static_handler(scope, receive, send)
    if handled:
        return  # Static file served, don't call app

# Not a static file, dispatch to ASGI app
await app(scope, receive, send)
```

### Option B: As ASGI Middleware

```python
# Users can wrap their app
from pounce import StaticFiles

app = StaticFiles(
    app,
    mounts={
        "/static": "./public",
        "/assets": "./dist",
    }
)
```

**Decision:** Implement Option A (built-in) with Option B available for manual use.

---

## Configuration API

### ServerConfig Changes

```python
@dataclass(frozen=True, slots=True)
class ServerConfig:
    ...

    # Static file serving (new)
    static_files: dict[str, str] = field(default_factory=dict)
    # Example: {"/static": "./public", "/assets": "./dist"}

    static_cache_control: str = "public, max-age=3600"
    static_precompressed: bool = True
    static_follow_symlinks: bool = False
    static_index_file: str | None = "index.html"
```

### CLI Changes

```bash
pounce myapp:app \
  --static /static:./public \
  --static /assets:./dist \
  --static-cache-control "public, max-age=7200"
```

---

## Testing Plan

### Unit Tests (`tests/unit/test_static.py`)

1. **Path resolution:**
   - Valid paths resolve correctly
   - Path traversal blocked (`../../../etc/passwd`)
   - Hidden files blocked (`.env`, `.git/config`)
   - Symlinks respected/blocked based on config
   - Directory index (index.html)

2. **ETag generation:**
   - Deterministic for same file
   - Changes when file modified
   - Weak ETag format

3. **Precompressed selection:**
   - Prefers zstd over gzip
   - Falls back if Accept-Encoding doesn't match
   - Only uses if mtime >= original

4. **Range parsing:**
   - Single range (`bytes=0-499`)
   - Suffix range (`bytes=-500`)
   - Open-ended (`bytes=500-`)
   - Invalid ranges return None

5. **MIME type detection:**
   - `.html` → `text/html`
   - `.css` → `text/css`
   - `.js` → `application/javascript`
   - `.png` → `image/png`
   - Unknown → `application/octet-stream`

### Integration Tests (`tests/integration/test_static_integration.py`)

1. **End-to-end serving:**
   - GET /static/file.txt returns 200 with correct body
   - GET /static/dir/ returns index.html
   - GET /static/missing returns 404 (or passes to app)

2. **ETag roundtrip:**
   - First request: 200 with ETag header
   - Second request with If-None-Match: 304 with no body

3. **Range requests:**
   - Request with Range header returns 206
   - Content-Range header correct
   - Body contains only requested bytes

4. **Precompressed:**
   - Request with Accept-Encoding: gzip returns .gz variant
   - Content-Encoding: gzip header present
   - Request with Accept-Encoding: zstd returns .zst variant

5. **Security:**
   - Path traversal attempts return 404 (or pass to app)
   - Hidden files return 404
   - Symlinks blocked when configured

6. **Performance:**
   - Benchmark: 10,000 requests to static file
   - Compare sendfile vs chunked read
   - Verify zero-copy with strace

### Bengal Integration Test

```python
async def test_bengal_site():
    """Serve a full Bengal-generated site through pounce."""
    # Assume Bengal site in ./test_site/
    config = ServerConfig(
        static_files={"/": "./test_site/public"},
        static_cache_control="public, max-age=3600",
    )

    # Request index
    response = await client.get("/")
    assert response.status == 200
    assert "text/html" in response.headers["content-type"]

    # Request CSS (precompressed)
    response = await client.get(
        "/assets/style.css",
        headers={"Accept-Encoding": "gzip"},
    )
    assert response.status == 200
    assert response.headers["content-encoding"] == "gzip"

    # Verify ETag caching
    etag = response.headers["etag"]
    response = await client.get(
        "/assets/style.css",
        headers={"If-None-Match": etag},
    )
    assert response.status == 304
```

---

## Performance Targets

1. **Throughput:** Within 10% of Nginx for same workload
2. **Latency:** <1ms for cached small files (<10 KB)
3. **Memory:** No memory leak on 100K requests
4. **Zero-copy:** Confirmed with strace (no read/write syscalls for large files)

---

## Documentation

### New Page: `/docs/features/static-files.md`

```markdown
# Static File Serving

Pounce can serve static files efficiently without requiring Nginx or another
web server in front. Ideal for Bengal SSG sites and Chirp static assets.

## Configuration

```python
import pounce

pounce.run(
    "myapp:app",
    static_files={
        "/static": "./public",
        "/assets": "./dist",
    },
    static_cache_control="public, max-age=7200",
)
```

## Features

- **Zero-copy sendfile** for large files (Linux/macOS)
- **ETag caching** with automatic 304 Not Modified
- **Range requests** for video/audio streaming
- **Precompressed files** — serves .gz/.zst if available
- **Security** — path traversal prevention, hidden file blocking

## Precompressed Files

Pounce automatically serves precompressed variants if:
1. Client supports encoding (Accept-Encoding: gzip, zstd)
2. File exists (e.g., style.css.gz, style.css.zst)
3. Precompressed mtime >= original mtime

Generate precompressed files with:
```bash
gzip -k public/**/*.{css,js,html}
zstd -k public/**/*.{css,js,html}
```

## Bengal Integration

Serve a Bengal site directly:
```python
pounce.run("myapp:app", static_files={"/": "./public"})
```

## Performance

Pounce static file serving is optimized for:
- Small files (<10 KB): <1ms latency
- Large files (>1 MB): zero-copy sendfile
- Cached files: 304 responses skip body transfer
```

---

## Acceptance Criteria

- [ ] All unit tests pass (15+ tests)
- [ ] All integration tests pass (6+ tests)
- [ ] Bengal site serves correctly without Nginx
- [ ] Zero-copy confirmed with strace/dtrace
- [ ] ETag roundtrip returns 304
- [ ] Range requests return 206 with correct bytes
- [ ] Precompressed files served when available
- [ ] Path traversal blocked (security test)
- [ ] Performance within 10% of Nginx
- [ ] Documentation complete

---

## Next Steps After Completion

1. Dogfood with Bengal documentation site
2. Add to examples/ (serve a small Bengal site)
3. Benchmark report (pounce vs Nginx vs Uvicorn + whitenoise)
4. Blog post: "Serving Static Files with Pounce"
5. Move to Task #1.2 (Lifespan State)
