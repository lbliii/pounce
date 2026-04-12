"""Tests for pounce._request_pipeline — dictionary-aware compression negotiation."""

import json

import pytest

from pounce._compression import _HAS_ZSTD, CompressionDictionary
from pounce._request_pipeline import negotiate_compressor
from pounce.config import ServerConfig


def _make_dict(match: str = "/api/*") -> CompressionDictionary:
    from compression import zstd

    samples = [
        json.dumps({"id": i, "name": f"item_{i}", "status": "active"}).encode()
        for i in range(200)
    ]
    d = zstd.train_dict(samples, dict_size=8192)
    return CompressionDictionary(d.dict_content, match)


class TestNegotiateCompressorBasic:
    """Baseline negotiation (no dictionaries) returns (compressor, None)."""

    def test_compression_disabled(self):
        config = ServerConfig(compression=False)
        compressor, dictionary = negotiate_compressor(
            config, [(b"accept-encoding", b"gzip")]
        )
        assert compressor is None
        assert dictionary is None

    def test_no_accept_encoding(self):
        config = ServerConfig(compression=True)
        compressor, dictionary = negotiate_compressor(config, [])
        assert compressor is None
        assert dictionary is None

    def test_gzip(self):
        config = ServerConfig(compression=True)
        compressor, dictionary = negotiate_compressor(
            config, [(b"accept-encoding", b"gzip")]
        )
        assert compressor is not None
        assert compressor.encoding == "gzip"
        assert dictionary is None

    @pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
    def test_zstd(self):
        config = ServerConfig(compression=True)
        compressor, dictionary = negotiate_compressor(
            config, [(b"accept-encoding", b"zstd, gzip")]
        )
        assert compressor is not None
        assert compressor.encoding == "zstd"
        assert dictionary is None


@pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
class TestNegotiateCompressorDictionary:
    """Dictionary negotiation (RFC 9842) returns (DictZstdCompressor, dict)."""

    def test_matching_dictionary(self):
        cd = _make_dict(match="/api/*")
        config = ServerConfig(compression_dictionaries=(cd,))
        compressor, dictionary = negotiate_compressor(
            config,
            [
                (b"accept-encoding", b"zstd, gzip"),
                (b"available-dictionary", cd.sf_hash.encode()),
            ],
            request_target="/api/v1/items",
        )
        assert compressor is not None
        assert compressor.encoding == "dcz"
        assert dictionary is cd

    def test_no_available_dictionary_header(self):
        """Without Available-Dictionary, falls through to generic zstd."""
        cd = _make_dict()
        config = ServerConfig(compression_dictionaries=(cd,))
        compressor, dictionary = negotiate_compressor(
            config,
            [(b"accept-encoding", b"zstd, gzip")],
            request_target="/api/v1/items",
        )
        assert compressor is not None
        assert compressor.encoding == "zstd"
        assert dictionary is None

    def test_wrong_hash(self):
        """Non-matching hash falls through to generic."""
        cd = _make_dict()
        config = ServerConfig(compression_dictionaries=(cd,))
        compressor, dictionary = negotiate_compressor(
            config,
            [
                (b"accept-encoding", b"zstd"),
                (b"available-dictionary", b":bm9tYXRjaA==:"),
            ],
            request_target="/api/v1/items",
        )
        assert compressor is not None
        assert compressor.encoding == "zstd"
        assert dictionary is None

    def test_path_mismatch(self):
        """Dictionary match pattern doesn't match request target."""
        cd = _make_dict(match="/api/v1/*")
        config = ServerConfig(compression_dictionaries=(cd,))
        compressor, dictionary = negotiate_compressor(
            config,
            [
                (b"accept-encoding", b"zstd"),
                (b"available-dictionary", cd.sf_hash.encode()),
            ],
            request_target="/api/v2/items",
        )
        assert compressor is not None
        assert compressor.encoding == "zstd"
        assert dictionary is None

    def test_no_zstd_in_accept_encoding(self):
        """Client doesn't accept zstd — can't use dictionary compression."""
        cd = _make_dict()
        config = ServerConfig(compression_dictionaries=(cd,))
        compressor, dictionary = negotiate_compressor(
            config,
            [
                (b"accept-encoding", b"gzip"),
                (b"available-dictionary", cd.sf_hash.encode()),
            ],
            request_target="/api/v1/items",
        )
        assert compressor is not None
        assert compressor.encoding == "gzip"
        assert dictionary is None

    def test_empty_dictionaries_tuple(self):
        """No dictionaries configured — standard path."""
        config = ServerConfig(compression_dictionaries=())
        compressor, dictionary = negotiate_compressor(
            config,
            [
                (b"accept-encoding", b"zstd"),
                (b"available-dictionary", b":abc=:"),
            ],
        )
        assert compressor is not None
        assert compressor.encoding == "zstd"
        assert dictionary is None
