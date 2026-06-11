"""
Content encoding negotiation and compressor factory.

Parses Accept-Encoding headers, selects the best encoding, and returns
per-request compressor instances. Each compressor is created fresh per
request — never shared between requests or threads.

Encoding priority: zstd > gzip > identity (matching modern browser support).

Zstd uses Python 3.14 stdlib compression.zstd (PEP 784).
Gzip uses stdlib zlib for the raw deflate stream.

Both are stdlib modules that work correctly under free-threading (3.14t).

Note: Brotli (br) is intentionally excluded — the ``brotli`` C extension
re-enables the GIL on Python 3.14t, defeating pounce's free-threading
architecture. Clients that only send ``Accept-Encoding: br`` will receive
uncompressed responses.

Dictionary compression (RFC 9842): When a client sends an
``Available-Dictionary`` header matching a server-loaded dictionary,
responses use ``dcz`` (dictionary-compressed zstd) encoding for
dramatically better compression ratios on repetitive payloads.

"""

from __future__ import annotations

import hashlib
import zlib
from base64 import b64decode, b64encode
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

if TYPE_CHECKING:
    from compression import zstd as _zstd_mod

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


_ENCODING_PRIORITY: Final[tuple[str, ...]] = _build_encoding_priority()


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
        """Flush any buffered compressed data and finalize the stream.

        After calling flush(), the compressor should not be used again.

        Returns:
            Final compressed bytes.
        """
        ...

    def sync_flush(self) -> bytes:
        """Force buffered data out without finalizing the stream.

        Used for streaming responses where each chunk must produce
        compressed output immediately. The compressor remains usable
        after this call.

        Returns:
            Compressed bytes for any internally buffered data.
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

    def sync_flush(self) -> bytes:
        return self._compressor.flush(zlib.Z_SYNC_FLUSH)

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
            raise RuntimeError("Zstd compression requires Python 3.14+ with compression.zstd")
        self._compressor = _zstd.ZstdCompressor(level=level)

    def compress(self, data: bytes) -> bytes:
        return self._compressor.compress(data)

    def flush(self) -> bytes:
        return self._compressor.flush()

    def sync_flush(self) -> bytes:
        return self._compressor.flush(mode=_zstd.ZstdCompressor.FLUSH_BLOCK)

    @property
    def encoding(self) -> str:
        return "zstd"


class DictZstdCompressor:
    """Zstd compressor pre-loaded with a shared dictionary (RFC 9842).

    Uses a ``CompressionDictionary`` to achieve dramatically better
    compression ratios on repetitive payloads (e.g. API JSON responses).
    The ``Content-Encoding`` is ``dcz`` (dictionary-compressed zstd).

    Each request gets its own DictZstdCompressor — the underlying
    ``ZstdDict`` is immutable and safe to share across threads.

    """

    __slots__ = ("_compressor",)

    def __init__(self, zstd_dict: _zstd_mod.ZstdDict, *, level: int = 3) -> None:
        if not _HAS_ZSTD:
            raise RuntimeError("Zstd compression requires Python 3.14+ with compression.zstd")
        self._compressor = _zstd.ZstdCompressor(level=level, zstd_dict=zstd_dict)

    def compress(self, data: bytes) -> bytes:
        return self._compressor.compress(data)

    def flush(self) -> bytes:
        return self._compressor.flush()

    def sync_flush(self) -> bytes:
        return self._compressor.flush(mode=_zstd.ZstdCompressor.FLUSH_BLOCK)

    @property
    def encoding(self) -> str:
        return "dcz"


class CompressionDictionary:
    """A loaded zstd dictionary with its RFC 9842 identity.

    Immutable after creation — safe to share across threads.

    Attributes:
        sf_hash: SHA-256 hash of dict content as sf-binary (e.g. ``:abc=:``).
        match: URL pattern this dictionary applies to (e.g. ``/api/v1/*``).
        zstd_dict: The stdlib ``ZstdDict`` instance for compressor creation.
    """

    __slots__ = ("match", "sf_hash", "zstd_dict")

    def __init__(
        self,
        dict_content: bytes,
        match: str,
    ) -> None:
        if not _HAS_ZSTD:
            raise RuntimeError("Zstd compression requires Python 3.14+ with compression.zstd")
        sha = hashlib.sha256(dict_content).digest()
        self.sf_hash: str = ":" + b64encode(sha).decode() + ":"
        self.match: str = match
        self.zstd_dict: _zstd_mod.ZstdDict = _zstd.ZstdDict(dict_content)


def load_dictionary(path: Path, match: str) -> CompressionDictionary:
    """Load a zstd dictionary from disk.

    Args:
        path: Path to the dictionary file (created by ``zstd --train``).
        match: URL pattern this dictionary applies to.

    Returns:
        A ``CompressionDictionary`` ready for use with ``DictZstdCompressor``.

    Raises:
        FileNotFoundError: If the dictionary file does not exist.
        RuntimeError: If zstd is not available.
    """
    return CompressionDictionary(path.read_bytes(), match)


def parse_sf_binary(value: bytes | str) -> bytes:
    """Parse an RFC 8941 structured field binary value.

    Structured field binary is base64-encoded content between colons:
    ``:base64content=:``

    Args:
        value: The sf-binary value (with or without surrounding whitespace).

    Returns:
        The decoded binary content.

    Raises:
        ValueError: If the value is not valid sf-binary.
    """
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    value = value.strip()
    if not value.startswith(":") or not value.endswith(":"):
        raise ValueError(f"Invalid sf-binary: must be wrapped in colons, got {value!r}")
    return b64decode(value[1:-1])


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
    encodings = _parse_accept_encoding(accept_encoding)

    # Check wildcard
    wildcard_q = encodings.get("*", 0.0)

    # Find best match respecting priority
    for encoding in _ENCODING_PRIORITY:
        q = encodings.get(encoding, wildcard_q)
        if q > 0:
            return encoding

    return None


def _parse_accept_encoding(accept_encoding: bytes | str) -> dict[str, float]:
    """Parse Accept-Encoding into an {encoding: q-value} mapping.

    Encodings explicitly declined with ``q=0`` are omitted from the result.

    Args:
        accept_encoding: The Accept-Encoding header value.

    Returns:
        Mapping of lowercase encoding name to its q-value (all > 0).

    """
    if isinstance(accept_encoding, bytes):
        accept_encoding = accept_encoding.decode("ascii", errors="replace")

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

    return encodings


def accepted_encodings(accept_encoding: bytes | str) -> list[str]:
    """Return acceptable encodings from Accept-Encoding, in our priority order.

    Respects q-values: encodings with ``q=0`` are excluded and a non-zero
    wildcard (``*``) makes any priority encoding acceptable. Unlike
    :func:`negotiate_encoding`, this returns *all* acceptable priority
    encodings (zstd before gzip) so callers can pick the best variant that
    actually exists on disk.

    Args:
        accept_encoding: The Accept-Encoding header value.

    Returns:
        List of acceptable encoding names in descending preference.

    """
    encodings = _parse_accept_encoding(accept_encoding)
    wildcard_q = encodings.get("*", 0.0)
    return [encoding for encoding in _ENCODING_PRIORITY if encodings.get(encoding, wildcard_q) > 0]


def create_compressor(
    encoding: str,
    *,
    dictionary: CompressionDictionary | None = None,
) -> Compressor:
    """Create a compressor instance for the given encoding.

    Args:
        encoding: Encoding name (e.g., "zstd", "gzip", "dcz").
        dictionary: Optional compression dictionary for ``dcz`` encoding.

    Returns:
        A fresh Compressor instance.

    Raises:
        ValueError: If the encoding is not supported, or ``dcz`` requested
            without a dictionary.

    """
    match encoding:
        case "dcz":
            if dictionary is None:
                raise ValueError("dcz encoding requires a CompressionDictionary")
            return DictZstdCompressor(dictionary.zstd_dict)
        case "zstd":
            return ZstdCompressor()
        case "gzip":
            return GzipCompressor()
        case _:
            raise ValueError(f"Unsupported encoding: {encoding!r}")


def negotiate_dictionary(
    available_dictionary: bytes | str,
    dictionaries: tuple[CompressionDictionary, ...],
    request_target: str = "",
) -> CompressionDictionary | None:
    """Match an ``Available-Dictionary`` header to a loaded dictionary.

    Args:
        available_dictionary: The ``Available-Dictionary`` header value (sf-binary hash).
        dictionaries: Server-loaded dictionaries to match against.
        request_target: The request URL path — used to filter by ``match`` pattern.

    Returns:
        The matching ``CompressionDictionary``, or ``None`` if no match.
    """
    if not dictionaries:
        return None

    if isinstance(available_dictionary, bytes):
        available_dictionary = available_dictionary.decode("ascii", errors="replace")
    available_dictionary = available_dictionary.strip()

    if not available_dictionary:
        return None

    for d in dictionaries:
        if d.sf_hash != available_dictionary:
            continue
        if d.match and request_target and not _match_pattern(d.match, request_target):
            continue
        return d
    return None


def _match_pattern(pattern: str, target: str) -> bool:
    """Simple glob-style match: ``/api/v1/*`` matches ``/api/v1/users``."""
    if pattern.endswith("*"):
        return target.startswith(pattern[:-1])
    return target == pattern
