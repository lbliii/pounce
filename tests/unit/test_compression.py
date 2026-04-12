"""Tests for pounce._compression — encoding negotiation and compressors."""

import gzip
import hashlib
import json
import zlib

import pytest

from pounce._compression import (
    _HAS_ZSTD,
    CompressionDictionary,
    DictZstdCompressor,
    GzipCompressor,
    ZstdCompressor,
    create_compressor,
    negotiate_dictionary,
    negotiate_encoding,
    parse_sf_binary,
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

    def test_sync_flush_produces_output(self):
        """sync_flush() forces buffered data out without finalizing."""
        c = GzipCompressor()
        data = b"small SSE event"
        compressed = c.compress(data) + c.sync_flush()
        assert len(compressed) > 0  # Data actually emitted

        # Decompress with wbits=31 (gzip) — Z_SYNC_FLUSH produces
        # a valid partial stream that zlib can decompress.
        d = zlib.decompressobj(31)
        decompressed = d.decompress(compressed)
        assert decompressed == data

    def test_sync_flush_allows_continued_compression(self):
        """After sync_flush(), the compressor can still accept data."""
        c = GzipCompressor()
        chunk1 = b"first chunk "
        chunk2 = b"second chunk"

        # Compress first chunk and sync flush
        part1 = c.compress(chunk1) + c.sync_flush()

        # Compress second chunk and finalize
        part2 = c.compress(chunk2) + c.flush()

        # Full stream is decompressible
        full = part1 + part2
        decompressed = gzip.decompress(full)
        assert decompressed == chunk1 + chunk2


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

    def test_sync_flush_produces_output(self):
        """sync_flush() forces buffered data out without finalizing."""
        from compression.zstd import ZstdDecompressor

        c = ZstdCompressor()
        data = b"small SSE event"
        compressed = c.compress(data) + c.sync_flush()
        assert len(compressed) > 0  # Data actually emitted

        # FLUSH_BLOCK produces a partial frame — use incremental decompressor
        d = ZstdDecompressor()
        decompressed = d.decompress(compressed)
        assert decompressed == data

    def test_sync_flush_allows_continued_compression(self):
        """After sync_flush(), the compressor can still accept data."""
        from compression import zstd

        c = ZstdCompressor()
        chunk1 = b"first chunk "
        chunk2 = b"second chunk"

        # Compress first chunk and sync flush
        part1 = c.compress(chunk1) + c.sync_flush()

        # Compress second chunk and finalize (completes the frame)
        part2 = c.compress(chunk2) + c.flush()

        # Full stream is decompressible
        full = part1 + part2
        decompressed = zstd.decompress(full)
        assert decompressed == chunk1 + chunk2


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

    @pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
    def test_create_dcz_with_dictionary(self):
        cd = _make_test_dictionary()
        c = create_compressor("dcz", dictionary=cd)
        assert c.encoding == "dcz"

    def test_create_dcz_without_dictionary_raises(self):
        with pytest.raises(ValueError, match="dcz encoding requires"):
            create_compressor("dcz")


# -- Helpers for dictionary tests --


def _make_test_dictionary(match: str = "/api/*") -> CompressionDictionary:
    """Create a test CompressionDictionary from sample JSON data."""
    from compression import zstd

    samples = [
        json.dumps({"id": i, "name": f"item_{i}", "status": "active"}).encode()
        for i in range(200)
    ]
    d = zstd.train_dict(samples, dict_size=8192)
    return CompressionDictionary(d.dict_content, match)


# -- Dictionary compression tests (RFC 9842) --


@pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
class TestCompressionDictionary:
    """CompressionDictionary — dictionary identity and loading."""

    def test_sf_hash_format(self):
        cd = _make_test_dictionary()
        assert cd.sf_hash.startswith(":")
        assert cd.sf_hash.endswith(":")
        # Base64 content between colons
        inner = cd.sf_hash[1:-1]
        assert len(inner) > 0

    def test_sf_hash_matches_sha256(self):
        from compression import zstd

        samples = [b'{"id":1}' for _ in range(100)]
        d = zstd.train_dict(samples, dict_size=4096)
        cd = CompressionDictionary(d.dict_content, "/test")
        expected_hash = hashlib.sha256(d.dict_content).digest()
        actual_hash = parse_sf_binary(cd.sf_hash)
        assert actual_hash == expected_hash

    def test_match_stored(self):
        cd = _make_test_dictionary(match="/api/v2/*")
        assert cd.match == "/api/v2/*"

    def test_zstd_dict_usable(self):
        from compression import zstd

        cd = _make_test_dictionary()
        # Should be a valid ZstdDict
        c = zstd.ZstdCompressor(zstd_dict=cd.zstd_dict)
        compressed = c.compress(b'{"id": 1}') + c.flush()
        d = zstd.ZstdDecompressor(zstd_dict=cd.zstd_dict)
        assert d.decompress(compressed) == b'{"id": 1}'


@pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
class TestDictZstdCompressor:
    """DictZstdCompressor — dictionary-compressed zstd (dcz encoding)."""

    def test_encoding_name(self):
        cd = _make_test_dictionary()
        c = DictZstdCompressor(cd.zstd_dict)
        assert c.encoding == "dcz"

    def test_roundtrip(self):
        from compression import zstd

        cd = _make_test_dictionary()
        c = DictZstdCompressor(cd.zstd_dict)
        data = b'{"id": 42, "name": "test_item", "status": "active"}'
        compressed = c.compress(data) + c.flush()

        d = zstd.ZstdDecompressor(zstd_dict=cd.zstd_dict)
        assert d.decompress(compressed) == data

    def test_empty_input(self):
        from compression import zstd

        cd = _make_test_dictionary()
        c = DictZstdCompressor(cd.zstd_dict)
        compressed = c.compress(b"") + c.flush()

        d = zstd.ZstdDecompressor(zstd_dict=cd.zstd_dict)
        assert d.decompress(compressed) == b""

    def test_sync_flush_produces_output(self):
        from compression.zstd import ZstdDecompressor

        cd = _make_test_dictionary()
        c = DictZstdCompressor(cd.zstd_dict)
        data = b'{"event": "update", "id": 1}'
        compressed = c.compress(data) + c.sync_flush()
        assert len(compressed) > 0

        d = ZstdDecompressor(zstd_dict=cd.zstd_dict)
        assert d.decompress(compressed) == data

    def test_sync_flush_allows_continued_compression(self):
        from compression import zstd

        cd = _make_test_dictionary()
        c = DictZstdCompressor(cd.zstd_dict)
        chunk1 = b'{"id": 1, '
        chunk2 = b'"name": "test"}'

        part1 = c.compress(chunk1) + c.sync_flush()
        part2 = c.compress(chunk2) + c.flush()

        full = part1 + part2
        d = zstd.ZstdDecompressor(zstd_dict=cd.zstd_dict)
        assert d.decompress(full) == chunk1 + chunk2


@pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
class TestNegotiateDictionary:
    """negotiate_dictionary() matches Available-Dictionary to loaded dicts."""

    def test_matching_hash(self):
        cd = _make_test_dictionary(match="/api/*")
        result = negotiate_dictionary(cd.sf_hash, (cd,), "/api/v1/items")
        assert result is cd

    def test_no_match_hash(self):
        cd = _make_test_dictionary()
        result = negotiate_dictionary(":bm9tYXRjaA==:", (cd,), "/api/v1/items")
        assert result is None

    def test_empty_dictionaries(self):
        result = negotiate_dictionary(":abc:", (), "/api/v1")
        assert result is None

    def test_empty_header(self):
        cd = _make_test_dictionary()
        result = negotiate_dictionary(b"", (cd,), "/api/v1")
        assert result is None

    def test_match_pattern_filters(self):
        cd = _make_test_dictionary(match="/api/v1/*")
        # Matching path
        assert negotiate_dictionary(cd.sf_hash, (cd,), "/api/v1/items") is cd
        # Non-matching path
        assert negotiate_dictionary(cd.sf_hash, (cd,), "/api/v2/items") is None

    def test_empty_match_accepts_all(self):
        cd = _make_test_dictionary(match="")
        assert negotiate_dictionary(cd.sf_hash, (cd,), "/anything") is cd

    def test_bytes_header(self):
        cd = _make_test_dictionary()
        result = negotiate_dictionary(cd.sf_hash.encode(), (cd,), "/api/test")
        assert result is cd

    def test_multiple_dictionaries(self):
        cd1 = _make_test_dictionary(match="/api/v1/*")
        cd2 = _make_test_dictionary(match="/api/v2/*")
        # Should match cd1
        assert negotiate_dictionary(cd1.sf_hash, (cd1, cd2), "/api/v1/x") is cd1


class TestParseSfBinary:
    """parse_sf_binary() decodes RFC 8941 structured field binary values."""

    def test_valid(self):
        import base64

        data = b"hello world"
        encoded = ":" + base64.b64encode(data).decode() + ":"
        assert parse_sf_binary(encoded) == data

    def test_bytes_input(self):
        import base64

        data = b"test"
        encoded = b":" + base64.b64encode(data) + b":"
        assert parse_sf_binary(encoded) == data

    def test_invalid_no_colons(self):
        with pytest.raises(ValueError, match="Invalid sf-binary"):
            parse_sf_binary("not-sf-binary")

    def test_strips_whitespace(self):
        import base64

        data = b"test"
        encoded = "  :" + base64.b64encode(data).decode() + ":  "
        assert parse_sf_binary(encoded) == data
