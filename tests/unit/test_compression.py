"""Tests for pounce._compression — encoding negotiation and compressors."""

import gzip

import pytest

from pounce._compression import (
    GzipCompressor,
    ZstdCompressor,
    _HAS_ZSTD,
    create_compressor,
    negotiate_encoding,
)


class TestNegotiateEncoding:
    """negotiate_encoding() selects the best encoding from Accept-Encoding."""

    def test_zstd_preferred_over_gzip(self):
        if not _HAS_ZSTD:
            pytest.skip("zstd not available")
        result = negotiate_encoding(b"gzip, zstd")
        assert result == "zstd"

    def test_gzip_fallback(self):
        result = negotiate_encoding(b"gzip")
        assert result == "gzip"

    def test_identity_returns_none(self):
        result = negotiate_encoding(b"identity")
        assert result is None

    def test_empty_returns_none(self):
        result = negotiate_encoding(b"")
        assert result is None

    def test_br_not_supported(self):
        """Brotli is excluded — re-enables the GIL on 3.14t."""
        result = negotiate_encoding(b"br")
        assert result is None

    def test_q_value_zero_excluded(self):
        """Encoding with q=0 is excluded."""
        result = negotiate_encoding(b"gzip;q=0, identity")
        assert result is None

    def test_q_value_ordering(self):
        if not _HAS_ZSTD:
            pytest.skip("zstd not available")
        # Even with lower q, zstd is preferred by our priority
        result = negotiate_encoding(b"gzip;q=1.0, zstd;q=0.5")
        assert result == "zstd"

    def test_wildcard_matches(self):
        """Wildcard * includes all encodings."""
        result = negotiate_encoding(b"*")
        # Should match our highest priority
        expected = "zstd" if _HAS_ZSTD else "gzip"
        assert result == expected

    def test_bytes_and_str_input(self):
        result_bytes = negotiate_encoding(b"gzip")
        result_str = negotiate_encoding("gzip")
        assert result_bytes == result_str == "gzip"

    def test_chrome_accept_encoding(self):
        """Real-world Chrome Accept-Encoding header."""
        result = negotiate_encoding(b"gzip, deflate, br, zstd")
        expected = "zstd" if _HAS_ZSTD else "gzip"
        assert result == expected

    def test_firefox_accept_encoding(self):
        """Real-world Firefox Accept-Encoding header."""
        result = negotiate_encoding(b"gzip, deflate, br, zstd")
        expected = "zstd" if _HAS_ZSTD else "gzip"
        assert result == expected

    def test_curl_accept_encoding(self):
        """curl default Accept-Encoding."""
        result = negotiate_encoding(b"gzip, deflate")
        assert result == "gzip"

    def test_invalid_q_value_treated_as_zero(self):
        result = negotiate_encoding(b"gzip;q=invalid")
        assert result is None


class TestGzipCompressor:
    """GzipCompressor wraps zlib for gzip encoding."""

    def test_encoding_name(self):
        c = GzipCompressor()
        assert c.encoding == "gzip"

    def test_roundtrip(self):
        c = GzipCompressor()
        data = b"Hello, World!" * 100
        compressed = c.compress(data) + c.flush()
        decompressed = gzip.decompress(compressed)
        assert decompressed == data

    def test_empty_input(self):
        c = GzipCompressor()
        compressed = c.compress(b"") + c.flush()
        decompressed = gzip.decompress(compressed)
        assert decompressed == b""

    def test_large_input(self):
        c = GzipCompressor()
        data = b"x" * 1_000_000
        compressed = c.compress(data) + c.flush()
        assert len(compressed) < len(data)  # Actually compresses
        assert gzip.decompress(compressed) == data


@pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
class TestZstdCompressor:
    """ZstdCompressor wraps compression.zstd (Python 3.14+)."""

    def test_encoding_name(self):
        c = ZstdCompressor()
        assert c.encoding == "zstd"

    def test_roundtrip(self):
        from compression import zstd

        c = ZstdCompressor()
        data = b"Hello, World!" * 100
        compressed = c.compress(data) + c.flush()
        decompressed = zstd.decompress(compressed)
        assert decompressed == data

    def test_empty_input(self):
        from compression import zstd

        c = ZstdCompressor()
        compressed = c.compress(b"") + c.flush()
        decompressed = zstd.decompress(compressed)
        assert decompressed == b""


class TestCreateCompressor:
    """create_compressor() is a factory for compressor instances."""

    def test_create_gzip(self):
        c = create_compressor("gzip")
        assert c.encoding == "gzip"

    @pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
    def test_create_zstd(self):
        c = create_compressor("zstd")
        assert c.encoding == "zstd"

    def test_unsupported_raises(self):
        with pytest.raises(ValueError, match="Unsupported encoding"):
            create_compressor("deflate")

    def test_br_raises(self):
        """Brotli is excluded — re-enables the GIL on 3.14t."""
        with pytest.raises(ValueError, match="Unsupported encoding"):
            create_compressor("br")
