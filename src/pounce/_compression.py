"""
Content encoding negotiation and compressor factory.

Parses Accept-Encoding headers, selects the best encoding, and returns
per-request compressor instances. Each compressor is created fresh per
request — never shared between requests or threads.

Encoding priority: zstd > gzip > identity (matching modern browser support).

Zstd uses Python 3.14 stdlib compression.zstd (PEP 784).
Gzip uses stdlib zlib for the raw deflate stream.

Both are stdlib modules that work correctly under free-threading (3.14t).

"""

from __future__ import annotations

import zlib
from typing import Protocol

# compression.zstd is new in Python 3.14 — import with fallback
try:
    from compression import zstd as _zstd

    _HAS_ZSTD = True
except ImportError:
    _HAS_ZSTD = False


# Encoding priority — highest to lowest preference
def _build_encoding_priority() -> tuple[str, ...]:
    """Build encoding priority based on available libraries."""
    encodings: list[str] = []
    if _HAS_ZSTD:
        encodings.append("zstd")
    encodings.append("gzip")
    return tuple(encodings)


_ENCODING_PRIORITY: tuple[str, ...] = _build_encoding_priority()


class Compressor(Protocol):
    """Contract for content encoders."""

    def compress(self, data: bytes) -> bytes:
        """Compress a chunk of data.

        Args:
            data: Input bytes to compress.

        Returns:
            Compressed bytes (may be empty if buffering internally).
        """
        ...

    def flush(self) -> bytes:
        """Flush any buffered compressed data.

        Returns:
            Final compressed bytes.
        """
        ...

    @property
    def encoding(self) -> str:
        """The Content-Encoding value for this compressor (e.g., 'gzip')."""
        ...


class GzipCompressor:
    """Gzip compressor using stdlib zlib.

    Creates a fresh zlib compressor per instance. Each request gets its own
    GzipCompressor — no shared state.

    """

    __slots__ = ("_compressor",)

    def __init__(self, *, level: int = 6) -> None:
        # wbits=31 produces gzip-format output with header/trailer
        self._compressor = zlib.compressobj(level, zlib.DEFLATED, 31)

    def compress(self, data: bytes) -> bytes:
        return self._compressor.compress(data)

    def flush(self) -> bytes:
        return self._compressor.flush(zlib.Z_FINISH)

    @property
    def encoding(self) -> str:
        return "gzip"


class ZstdCompressor:
    """Zstd compressor using Python 3.14 stdlib compression.zstd.

    Requires Python 3.14+ with compression.zstd available (PEP 784).
    Each request gets its own ZstdCompressor — no shared state.

    """

    __slots__ = ("_compressor",)

    def __init__(self, *, level: int = 3) -> None:
        if not _HAS_ZSTD:
            raise RuntimeError(
                "Zstd compression requires Python 3.14+ with compression.zstd"
            )
        self._compressor = _zstd.ZstdCompressor(level=level)

    def compress(self, data: bytes) -> bytes:
        return self._compressor.compress(data)

    def flush(self) -> bytes:
        return self._compressor.flush()

    @property
    def encoding(self) -> str:
        return "zstd"


def negotiate_encoding(accept_encoding: bytes | str) -> str | None:
    """Parse Accept-Encoding and return the best supported encoding.

    Respects q-values and our encoding priority (zstd > gzip).
    Returns None if no supported encoding matches or the client
    explicitly declines all encodings.

    Args:
        accept_encoding: The Accept-Encoding header value.

    Returns:
        Encoding name (e.g., "zstd", "gzip") or None.

    Example:
        >>> negotiate_encoding(b"gzip, br, zstd;q=0.9")
        'zstd'
        >>> negotiate_encoding(b"identity")
        None

    """
    if isinstance(accept_encoding, bytes):
        accept_encoding = accept_encoding.decode("ascii", errors="replace")

    # Parse into {encoding: q-value} mapping
    encodings: dict[str, float] = {}
    for part in accept_encoding.split(","):
        part = part.strip()
        if not part:
            continue

        # Split on ";" to separate encoding from q-value
        segments = part.split(";")
        name = segments[0].strip().lower()
        q = 1.0

        for segment in segments[1:]:
            segment = segment.strip()
            if segment.startswith("q="):
                try:
                    q = float(segment[2:])
                except ValueError:
                    q = 0.0

        if q > 0:
            encodings[name] = q

    # Check wildcard
    wildcard_q = encodings.get("*", 0.0)

    # Find best match respecting priority
    for encoding in _ENCODING_PRIORITY:
        q = encodings.get(encoding, wildcard_q)
        if q > 0:
            return encoding

    return None


def create_compressor(encoding: str) -> Compressor:
    """Create a compressor instance for the given encoding.

    Args:
        encoding: Encoding name (e.g., "zstd", "gzip").

    Returns:
        A fresh Compressor instance.

    Raises:
        ValueError: If the encoding is not supported.

    """
    if encoding == "zstd":
        return ZstdCompressor()
    if encoding == "gzip":
        return GzipCompressor()
    raise ValueError(f"Unsupported encoding: {encoding!r}")
