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

Example:
    config = ServerConfig(static_files={"/static": "./public"})
    # Requests to /static/* will be served from ./public/

"""

from __future__ import annotations

import mimetypes
import os
import secrets
import stat as stat_mod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pounce._sendfile import SendfileCallable

from pounce._types import ASGIApp, Receive, Send


@dataclass(frozen=True, slots=True)
class StaticMount:
    """Configuration for a static file mount point.

    Args:
        url_path: URL prefix (e.g., "/static", "/assets")
        directory: Filesystem directory to serve
        cache_control: Cache-Control header value
        precompressed: Serve .gz/.zst if available and client supports
        follow_symlinks: Allow serving symlinked files
        index_file: Filename to serve for directories (e.g., "index.html")
        extra_mime_types: Additional extension-to-MIME mappings (e.g., {".wasm": "application/wasm"})

    """

    url_path: str
    directory: Path
    cache_control: str = "public, max-age=3600"
    precompressed: bool = True
    follow_symlinks: bool = False
    index_file: str | None = "index.html"
    extra_mime_types: dict[str, str] = field(default_factory=dict)


# Common modern MIME types not yet in stdlib mimetypes database
_MODERN_MIME_TYPES: dict[str, str] = {
    ".wasm": "application/wasm",
    ".mjs": "text/javascript",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".woff2": "font/woff2",
}


@dataclass(frozen=True, slots=True)
class StaticFile:
    """Resolved static file with metadata.

    Args:
        path: Absolute filesystem path
        size: File size in bytes
        mtime: Last modification time (seconds since epoch)
        mime_type: MIME type (e.g., "text/html")
        etag: ETag header value
        encoding: Content-Encoding (None, "gzip", or "zstd")

    """

    path: Path
    size: int
    mtime: float
    mime_type: str
    etag: str
    encoding: str | None = None
    cache_control: str = "public, max-age=3600"


class StaticFiles:
    """ASGI-compatible static file handler.

    Can be used as middleware or integrated into Worker.

    Example as middleware:
        from pounce import StaticFiles

        app = StaticFiles(
            app,
            mounts=[
                StaticMount("/static", Path("./public")),
                StaticMount("/assets", Path("./dist")),
            ]
        )

    Example in Worker (built-in):
        config = ServerConfig(static_files={"/static": "./public"})

    """

    __slots__ = ("_app", "_mounts")

    def __init__(
        self,
        app: ASGIApp | None = None,
        *,
        mounts: list[StaticMount],
    ) -> None:
        """Initialize static file handler.

        Args:
            app: Optional ASGI app to call if path doesn't match (middleware mode)
            mounts: List of static mount configurations

        """
        self._app = app
        self._mounts = self._prepare_mounts(mounts)

    def _prepare_mounts(self, mounts: list[StaticMount]) -> list[StaticMount]:
        """Normalize and validate mount configurations.

        Returns:
            Sorted list of mounts (longest url_path first for correct matching)

        """
        # Normalize paths
        normalized = []
        for mount in mounts:
            url_path = mount.url_path.rstrip("/")
            if not url_path.startswith("/"):
                url_path = "/" + url_path

            # Ensure directory exists and is absolute
            directory = mount.directory.resolve()
            if not directory.is_dir():
                msg = f"Static directory does not exist: {directory}"
                raise ValueError(msg)

            normalized.append(
                StaticMount(
                    url_path=url_path,
                    directory=directory,
                    cache_control=mount.cache_control,
                    precompressed=mount.precompressed,
                    follow_symlinks=mount.follow_symlinks,
                    index_file=mount.index_file,
                    extra_mime_types=mount.extra_mime_types,
                )
            )

        # Sort by url_path length (longest first) for correct prefix matching
        return sorted(normalized, key=lambda m: len(m.url_path), reverse=True)

    async def __call__(self, scope: dict[str, Any], receive: Receive, send: Send) -> None:
        """Handle ASGI request.

        If path matches a static mount, serve the file. Otherwise, call the app.

        """
        if scope["type"] != "http":
            if self._app is not None:
                await self._app(scope, receive, send)
            return

        # Try to serve static file
        method = scope["method"]
        path = scope["path"]

        # Only handle GET and HEAD
        if method not in ("GET", "HEAD"):
            if self._app is not None:
                await self._app(scope, receive, send)
            return

        # Extract needed headers in a single pass
        headers = scope.get("headers", [])
        if_none_match: bytes | None = None
        range_header: bytes | None = None
        accept_encoding: bytes | None = None
        for hdr_name, hdr_value in headers:
            lower_name = hdr_name.lower()
            if lower_name == b"if-none-match":
                if_none_match = hdr_value
            elif lower_name == b"range":
                range_header = hdr_value
            elif lower_name == b"accept-encoding":
                accept_encoding = hdr_value

        # Check for zero-copy sendfile extension
        sendfile_fn = scope.get("extensions", {}).get("pounce.sendfile")

        # Try to resolve to static file
        file = self._resolve_file(path, accept_encoding)
        if file is None:
            # Not a static file, pass to app if available
            if self._app is not None:
                await self._app(scope, receive, send)
            else:
                # No app and no file found - send 404
                await send(
                    {
                        "type": "http.response.start",
                        "status": 404,
                        "headers": [(b"content-type", b"text/plain")],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b"Not Found",
                    }
                )
            return

        # Check conditional requests (If-None-Match)
        if if_none_match and if_none_match.decode("latin1").strip() == file.etag:
            await self._send_304(file, send)
            return

        # Check Range header
        if range_header and method == "GET":
            ranges = self._parse_range_header(range_header.decode("latin1"), file.size)
            if ranges is not None:
                await self._send_206(file, ranges, send, sendfile_fn=sendfile_fn)
                return

        # Send full file
        await self._send_file(file, method, send, sendfile_fn=sendfile_fn)

    def _resolve_file(self, url_path: str, accept_encoding: bytes | None) -> StaticFile | None:
        """Resolve URL path to static file.

        Returns:
            StaticFile if found and valid, None otherwise

        """
        # Normalize URL path
        url_path = os.path.normpath(url_path)

        # Find matching mount
        for mount in self._mounts:
            # Special case for root mount "/"
            if mount.url_path == "/":
                if url_path == "/":
                    relative_path = ""
                elif url_path.startswith("/"):
                    relative_path = url_path[1:]  # Strip leading /
                else:
                    continue
            elif url_path.startswith(mount.url_path):
                # Extract relative path
                if url_path == mount.url_path:
                    relative_path = ""
                elif url_path.startswith(mount.url_path + "/"):
                    relative_path = url_path[len(mount.url_path) + 1 :]
                else:
                    continue
            else:
                continue

            # Resolve to filesystem path
            file_path = mount.directory / relative_path

            # Security: prevent path traversal
            try:
                resolved = file_path.resolve()
                mount_resolved = mount.directory
                # Check if resolved path is within mount directory
                # Use is_relative_to() which handles symlinks correctly
                if not resolved.is_relative_to(mount_resolved):
                    return None
            except ValueError, OSError:
                return None

            # Block hidden files (anything starting with .) but allow .well-known
            # per RFC 8615 (used by ACME/Let's Encrypt, security.txt, etc.)
            for part in resolved.parts:
                if part.startswith(".") and part != ".well-known":
                    return None

            # Single stat + lstat to derive type flags (avoid multiple syscalls)
            try:
                lst = resolved.lstat()
            except OSError:
                return None

            # Check symlinks via lstat (no extra syscall)
            if stat_mod.S_ISLNK(lst.st_mode) and not mount.follow_symlinks:
                return None

            # After lstat, stat() to follow symlinks for the real mode
            try:
                file_stat = resolved.stat()
            except OSError:
                return None

            # Handle directory index
            if stat_mod.S_ISDIR(file_stat.st_mode):
                if mount.index_file:
                    resolved = resolved / mount.index_file
                    try:
                        file_stat = resolved.stat()
                    except OSError:
                        return None
                else:
                    return None

            # Must be a regular file
            if not stat_mod.S_ISREG(file_stat.st_mode):
                return None

            # Check for precompressed variants
            final_path, encoding = self._find_precompressed(
                resolved, mount, accept_encoding, file_stat
            )

            # Reuse stat if no precompressed variant was found
            if final_path == resolved:
                final_stat = file_stat
            else:
                try:
                    final_stat = final_path.stat()
                except OSError:
                    return None

            # Determine MIME type from original path (not .gz/.zst)
            mime_type = self._get_mime_type(resolved, mount)

            # Generate ETag — include encoding so compressed and uncompressed
            # variants produce distinct ETags (RFC 7232 compliance).
            etag = self._generate_etag(final_stat.st_mtime, final_stat.st_size, encoding)

            return StaticFile(
                path=final_path,
                size=final_stat.st_size,
                mtime=file_stat.st_mtime,
                mime_type=mime_type,
                etag=etag,
                encoding=encoding,
                cache_control=mount.cache_control,
            )

        return None

    def _find_precompressed(
        self,
        path: Path,
        mount: StaticMount,
        accept_encoding: bytes | None,
        original_stat: os.stat_result,
    ) -> tuple[Path, str | None]:
        """Find precompressed variant if available and client supports.

        Priority: zstd > gzip > identity

        Returns:
            (file_path, encoding) where encoding is "gzip", "zstd", or None

        """
        if not mount.precompressed:
            return (path, None)

        if not accept_encoding:
            return (path, None)

        accept_str = accept_encoding.decode("latin1").lower()

        # Check zstd first (better compression)
        if "zstd" in accept_str:
            zst_path = path.with_suffix(path.suffix + ".zst")
            if zst_path.exists() and self._validate_precompressed(zst_path, mount):
                try:
                    zst_stat = zst_path.stat()
                    # Only use if precompressed is newer or same age
                    if zst_stat.st_mtime >= original_stat.st_mtime:
                        return (zst_path, "zstd")
                except OSError:
                    pass

        # Check gzip
        if "gzip" in accept_str:
            gz_path = path.with_suffix(path.suffix + ".gz")
            if gz_path.exists() and self._validate_precompressed(gz_path, mount):
                try:
                    gz_stat = gz_path.stat()
                    if gz_stat.st_mtime >= original_stat.st_mtime:
                        return (gz_path, "gzip")
                except OSError:
                    pass

        return (path, None)

    def _validate_precompressed(self, path: Path, mount: StaticMount) -> bool:
        """Validate a precompressed variant against the same security checks as the original.

        Checks path traversal, hidden files, and symlinks.

        Returns:
            True if the precompressed path is safe to serve

        """
        try:
            resolved = path.resolve()
            if not resolved.is_relative_to(mount.directory):
                return False
        except ValueError, OSError:
            return False

        # Block hidden files (same check as _resolve_file)
        for part in resolved.parts:
            if part.startswith(".") and part != ".well-known":
                return False

        # Check symlinks — use original path (not resolved) so lstat sees the link
        if not mount.follow_symlinks and path.is_symlink():
            return False

        # Must be a regular file
        try:
            file_stat = resolved.stat()
        except OSError:
            return False

        return stat_mod.S_ISREG(file_stat.st_mode)

    def _get_mime_type(self, path: Path, mount: StaticMount | None = None) -> str:
        """Get MIME type for file.

        Lookup order:
        1. Mount-specific extra_mime_types (user overrides)
        2. stdlib mimetypes.guess_type()
        3. _MODERN_MIME_TYPES fallback for modern web extensions
        4. application/octet-stream

        Returns:
            MIME type string

        """
        suffix = path.suffix.lower()

        # 1. User-supplied overrides on the mount
        if mount and suffix in mount.extra_mime_types:
            return mount.extra_mime_types[suffix]

        # 2. stdlib
        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type:
            return mime_type

        # 3. Built-in modern types
        if suffix in _MODERN_MIME_TYPES:
            return _MODERN_MIME_TYPES[suffix]

        return "application/octet-stream"

    def _generate_etag(self, mtime: float, size: int, encoding: str | None = None) -> str:
        """Generate ETag from mtime, size, and encoding.

        Uses weak ETag (W/) because we use mtime, not content hash.
        Encoding is included so that compressed and uncompressed variants
        of the same file produce distinct ETags (RFC 7232).

        Returns:
            ETag header value (e.g., W/"5f3c-1a2b" or W/"5f3c-1a2b-gzip")

        """
        mtime_hex = hex(int(mtime * 1_000_000))[2:]
        size_hex = hex(size)[2:]
        if encoding:
            return f'W/"{mtime_hex}-{size_hex}-{encoding}"'
        return f'W/"{mtime_hex}-{size_hex}"'

    def _check_not_modified(self, headers: list[tuple[bytes, bytes]], file: StaticFile) -> bool:
        """Check if client has cached version (If-None-Match).

        Returns:
            True if client cache is valid (send 304), False otherwise

        """
        if_none_match = self._get_header(headers, b"if-none-match")
        if not if_none_match:
            return False

        client_etag = if_none_match.decode("latin1").strip()
        return client_etag == file.etag

    def _parse_range_header(
        self, range_header: str, file_size: int
    ) -> list[tuple[int, int]] | None:
        """Parse Range header and return list of (start, end) byte ranges.

        Format: "bytes=0-499" or "bytes=500-999" or "bytes=-500"

        Returns:
            List of (start, end) tuples (inclusive), or None if invalid

        """
        if not range_header.startswith("bytes="):
            return None

        ranges = []
        for range_spec in range_header[6:].split(","):
            range_spec = range_spec.strip()

            if "-" not in range_spec:
                return None

            parts = range_spec.split("-", 1)
            start_str, end_str = parts

            try:
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
                    start = max(0, file_size - int(end_str))
                    end = file_size - 1
                else:
                    return None
            except ValueError:
                return None

            # Validate range
            if start < 0 or end >= file_size or start > end:
                return None

            ranges.append((start, end))

        return ranges if ranges else None

    def _get_header(self, headers: list[tuple[bytes, bytes]], name: bytes) -> bytes | None:
        """Get header value by name (case-insensitive).

        Returns:
            Header value as bytes, or None if not found

        """
        name_lower = name.lower()
        for header_name, header_value in headers:
            if header_name.lower() == name_lower:
                return header_value
        return None

    async def _send_304(self, file: StaticFile, send: Send) -> None:
        """Send 304 Not Modified response."""
        headers: list[tuple[bytes, bytes]] = [
            (b"etag", file.etag.encode("latin1")),
            (b"cache-control", file.cache_control.encode("latin1")),
        ]

        # 304 must include Vary when the 200 would (RFC 7232 §4.1)
        if file.encoding:
            headers.append((b"vary", b"accept-encoding"))

        await send(
            {
                "type": "http.response.start",
                "status": 304,
                "headers": headers,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"",
            }
        )

    async def _send_206(
        self,
        file: StaticFile,
        ranges: list[tuple[int, int]],
        send: Send,
        *,
        sendfile_fn: SendfileCallable | None = None,
    ) -> None:
        """Send 206 Partial Content response (RFC 7233).

        Single range: Content-Range header with the range body.
        Multiple ranges: multipart/byteranges body with MIME boundary.

        """
        if len(ranges) == 1:
            await self._send_206_single(file, ranges[0], send, sendfile_fn=sendfile_fn)
        else:
            await self._send_206_multipart(file, ranges, send)

    async def _send_206_single(
        self,
        file: StaticFile,
        range_pair: tuple[int, int],
        send: Send,
        *,
        sendfile_fn: SendfileCallable | None = None,
    ) -> None:
        """Send a single-range 206 response."""
        start, end = range_pair
        content_length = end - start + 1

        headers = [
            (b"content-type", file.mime_type.encode("latin1")),
            (b"content-length", str(content_length).encode("latin1")),
            (b"content-range", f"bytes {start}-{end}/{file.size}".encode("latin1")),
            (b"accept-ranges", b"bytes"),
            (b"etag", file.etag.encode("latin1")),
            (b"cache-control", file.cache_control.encode("latin1")),
        ]

        if file.encoding:
            headers.append((b"content-encoding", file.encoding.encode("latin1")))
            headers.append((b"vary", b"accept-encoding"))

        await send(
            {
                "type": "http.response.start",
                "status": 206,
                "headers": headers,
            }
        )

        await self._send_file_range(file.path, start, content_length, send, sendfile_fn=sendfile_fn)

    async def _send_206_multipart(
        self,
        file: StaticFile,
        ranges: list[tuple[int, int]],
        send: Send,
    ) -> None:
        """Send a multipart/byteranges 206 response (RFC 7233 §4.1).

        Each part has its own Content-Type and Content-Range headers,
        separated by a MIME boundary. Sendfile is not used here because
        the part headers must be interleaved with file data.

        """
        boundary = secrets.token_hex(16)
        boundary_bytes = boundary.encode("ascii")
        mime_type_bytes = file.mime_type.encode("latin1")

        # Pre-build all parts to compute total Content-Length
        parts: list[tuple[bytes, int, int]] = []  # (part_header, start, count)
        for start, end in ranges:
            count = end - start + 1
            part_header = (
                b"--" + boundary_bytes + b"\r\n"
                b"Content-Type: " + mime_type_bytes + b"\r\n"
                b"Content-Range: bytes "
                + f"{start}-{end}/{file.size}".encode("ascii")
                + b"\r\n\r\n"
            )
            parts.append((part_header, start, count))

        closing = b"\r\n--" + boundary_bytes + b"--\r\n"

        total_length = sum(len(ph) + count for ph, _, count in parts)
        # Add \r\n between parts (before each part except the first)
        total_length += 2 * (len(parts) - 1)
        total_length += len(closing)

        headers: list[tuple[bytes, bytes]] = [
            (
                b"content-type",
                f"multipart/byteranges; boundary={boundary}".encode("latin1"),
            ),
            (b"content-length", str(total_length).encode("latin1")),
            (b"accept-ranges", b"bytes"),
            (b"etag", file.etag.encode("latin1")),
            (b"cache-control", file.cache_control.encode("latin1")),
        ]

        if file.encoding:
            headers.append((b"vary", b"accept-encoding"))

        await send(
            {
                "type": "http.response.start",
                "status": 206,
                "headers": headers,
            }
        )

        # Stream each part
        chunk_size = 65536
        for i, (part_header, start, count) in enumerate(parts):
            # CRLF separator between parts (not before the first)
            if i > 0:
                part_header = b"\r\n" + part_header

            await send(
                {
                    "type": "http.response.body",
                    "body": part_header,
                    "more_body": True,
                }
            )

            # Stream file data for this range
            with file.path.open("rb") as f:
                f.seek(start)
                remaining = count
                while remaining > 0:
                    chunk = f.read(min(chunk_size, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    await send(
                        {
                            "type": "http.response.body",
                            "body": chunk,
                            "more_body": True,
                        }
                    )

        # Final boundary
        await send(
            {
                "type": "http.response.body",
                "body": closing,
                "more_body": False,
            }
        )

    async def _send_file(
        self,
        file: StaticFile,
        method: str,
        send: Send,
        *,
        sendfile_fn: SendfileCallable | None = None,
    ) -> None:
        """Send full file response (200 OK)."""
        headers = [
            (b"content-type", file.mime_type.encode("latin1")),
            (b"content-length", str(file.size).encode("latin1")),
            (b"accept-ranges", b"bytes"),
            (b"etag", file.etag.encode("latin1")),
            (b"cache-control", file.cache_control.encode("latin1")),
        ]

        if file.encoding:
            headers.append((b"content-encoding", file.encoding.encode("latin1")))
            headers.append((b"vary", b"accept-encoding"))

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": headers,
            }
        )

        # HEAD request: no body
        if method == "HEAD":
            await send(
                {
                    "type": "http.response.body",
                    "body": b"",
                }
            )
            return

        # Send file body
        await self._send_file_body(file.path, 0, file.size, send, sendfile_fn=sendfile_fn)

    async def _send_file_body(
        self,
        path: Path,
        offset: int,
        count: int,
        send: Send,
        *,
        sendfile_fn: SendfileCallable | None = None,
    ) -> None:
        """Send file body, using zero-copy sendfile when available.

        When sendfile_fn is provided (non-TLS connections on supported platforms),
        the file data is transferred directly from the filesystem to the socket
        via os.sendfile(), bypassing Python memory entirely. An empty ASGI body
        frame is sent afterwards to signal response completion.

        Falls back to chunked reads through ASGI send otherwise.

        """
        if sendfile_fn is not None:
            # Zero-copy path: flush response headers to the socket first,
            # then transfer file data directly via os.sendfile().
            # The empty body with more_body=True forces the ASGI bridge
            # to write the buffered response headers to the writer.
            await send(
                {
                    "type": "http.response.body",
                    "body": b"",
                    "more_body": True,
                }
            )
            await sendfile_fn(path, offset, count)
            # Signal response completion to the protocol layer so that
            # h11 transitions to EndOfMessage and keep-alive works.
            await send(
                {
                    "type": "http.response.body",
                    "body": b"",
                    "more_body": False,
                }
            )
            return

        # Fallback: chunked read through ASGI send
        chunk_size = 65536  # 64 KB chunks

        with path.open("rb") as f:
            f.seek(offset)
            remaining = count

            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break

                await send(
                    {
                        "type": "http.response.body",
                        "body": chunk,
                        "more_body": remaining > len(chunk),
                    }
                )
                remaining -= len(chunk)

    async def _send_file_range(
        self,
        path: Path,
        start: int,
        count: int,
        send: Send,
        *,
        sendfile_fn: SendfileCallable | None = None,
    ) -> None:
        """Send file range (for 206 responses)."""
        await self._send_file_body(path, start, count, send, sendfile_fn=sendfile_fn)


def create_static_handler(
    mounts: dict[str, str],
    cache_control: str = "public, max-age=3600",
    precompressed: bool = True,
    follow_symlinks: bool = False,
    index_file: str | None = "index.html",
) -> StaticFiles:
    """Create StaticFiles handler from simple dict config.

    Args:
        mounts: Dict of {url_path: directory} mappings
        cache_control: Cache-Control header value
        precompressed: Serve .gz/.zst if available
        follow_symlinks: Allow serving symlinked files
        index_file: Filename to serve for directories

    Returns:
        StaticFiles instance ready to use

    Example:
        handler = create_static_handler({
            "/static": "./public",
            "/assets": "./dist",
        })

    """
    mount_list = [
        StaticMount(
            url_path=url_path,
            directory=Path(directory),
            cache_control=cache_control,
            precompressed=precompressed,
            follow_symlinks=follow_symlinks,
            index_file=index_file,
        )
        for url_path, directory in mounts.items()
    ]

    return StaticFiles(mounts=mount_list)
