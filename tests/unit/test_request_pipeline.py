"""Tests for pounce._request_pipeline — dictionary-aware compression negotiation."""

import json

import pytest

from pounce._compression import _HAS_ZSTD, CompressionDictionary
from pounce._request_pipeline import maybe_build_builtin_response, negotiate_compressor
from pounce.config import ServerConfig


def _make_dict(match: str = "/api/*") -> CompressionDictionary:
    from compression import zstd

    samples = [
        json.dumps({"id": i, "name": f"item_{i}", "status": "active"}).encode() for i in range(200)
    ]
    d = zstd.train_dict(samples, dict_size=8192)
    return CompressionDictionary(d.dict_content, match)


class TestNegotiateCompressorBasic:
    """Baseline negotiation (no dictionaries) returns (compressor, None)."""

    def test_compression_disabled(self):
        config = ServerConfig(compression=False)
        compressor, dictionary = negotiate_compressor(config, [(b"accept-encoding", b"gzip")])
        assert compressor is None
        assert dictionary is None

    def test_no_accept_encoding(self):
        config = ServerConfig(compression=True)
        compressor, dictionary = negotiate_compressor(config, [])
        assert compressor is None
        assert dictionary is None

    def test_gzip(self):
        config = ServerConfig(compression=True)
        compressor, dictionary = negotiate_compressor(config, [(b"accept-encoding", b"gzip")])
        assert compressor is not None
        assert compressor.encoding == "gzip"
        assert dictionary is None

    @pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
    def test_zstd(self):
        config = ServerConfig(compression=True)
        compressor, dictionary = negotiate_compressor(config, [(b"accept-encoding", b"zstd, gzip")])
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


class TestNegotiateCompressorFromMeta:
    """The meta-keyed entry point used by the sync worker must mirror
    :func:`negotiate_compressor` exactly, but without re-scanning headers."""

    def test_compression_disabled(self):
        from pounce._request_pipeline import negotiate_compressor_from_meta

        config = ServerConfig(compression=False)
        compressor, dictionary = negotiate_compressor_from_meta(config, b"gzip", None)
        assert compressor is None
        assert dictionary is None

    def test_no_accept_encoding(self):
        from pounce._request_pipeline import negotiate_compressor_from_meta

        config = ServerConfig(compression=True)
        compressor, dictionary = negotiate_compressor_from_meta(config, None, None)
        assert compressor is None
        assert dictionary is None

    def test_gzip(self):
        from pounce._request_pipeline import negotiate_compressor_from_meta

        config = ServerConfig(compression=True)
        compressor, dictionary = negotiate_compressor_from_meta(config, b"gzip", None)
        assert compressor is not None
        assert compressor.encoding == "gzip"
        assert dictionary is None

    def test_identity_only_no_compressor(self):
        from pounce._request_pipeline import negotiate_compressor_from_meta

        config = ServerConfig(compression=True)
        compressor, dictionary = negotiate_compressor_from_meta(config, b"identity", None)
        assert compressor is None
        assert dictionary is None

    @pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
    def test_matching_dictionary_dcz(self):
        from pounce._request_pipeline import negotiate_compressor_from_meta

        cd = _make_dict(match="/api/*")
        config = ServerConfig(compression_dictionaries=(cd,))
        compressor, dictionary = negotiate_compressor_from_meta(
            config,
            b"zstd, gzip",
            cd.sf_hash.encode(),
            request_target="/api/v1/items",
        )
        assert compressor is not None
        assert compressor.encoding == "dcz"
        assert dictionary is cd

    @pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
    def test_parity_with_header_scanning_entry_point(self):
        """from_meta must produce the same encoding as the header-scanning form."""
        from pounce._request_pipeline import (
            negotiate_compressor,
            negotiate_compressor_from_meta,
        )

        cd = _make_dict(match="/api/*")
        config = ServerConfig(compression_dictionaries=(cd,))
        headers = [
            (b"accept-encoding", b"zstd, gzip"),
            (b"available-dictionary", cd.sf_hash.encode()),
        ]
        scanned_c, scanned_d = negotiate_compressor(config, headers, request_target="/api/v1/items")
        meta_c, meta_d = negotiate_compressor_from_meta(
            config,
            b"zstd, gzip",
            cd.sf_hash.encode(),
            request_target="/api/v1/items",
        )
        assert scanned_c is not None
        assert meta_c is not None
        assert scanned_c.encoding == meta_c.encoding
        assert scanned_d is meta_d is cd


class TestBuiltinEndpointDispatch:
    """Built-in selection is shared without touching normal-request providers."""

    def test_regular_request_does_not_resolve_live_state(self) -> None:
        def unexpected() -> int:
            raise AssertionError("normal requests must not resolve live state")

        response = maybe_build_builtin_response(
            ServerConfig(),
            "GET",
            "/app",
            worker_id=3,
            active_connections=unexpected,
            draining=lambda: (_ for _ in ()).throw(AssertionError("unexpected drain read")),
        )

        assert response is None

    def test_health_uses_real_worker_and_lazy_state(self) -> None:
        response = maybe_build_builtin_response(
            ServerConfig(health_check_path="/healthz"),
            b"GET",
            "/healthz",
            worker_id=7,
            active_connections=lambda: 11,
            draining=lambda: True,
        )

        assert response is not None
        assert response.kind == "health"
        assert response.status == 503
        payload = json.loads(response.body)
        assert payload["worker_id"] == 7
        assert payload["active_connections"] == 11
        assert payload["status"] == "draining"

    def test_introspection_uses_real_worker_and_redacted_config(self) -> None:
        response = maybe_build_builtin_response(
            ServerConfig(introspection_enabled=True),
            "GET",
            "/_pounce/info",
            worker_id=9,
            active_connections=4,
        )

        assert response is not None
        assert response.kind == "introspection"
        payload = json.loads(response.body)
        assert payload["worker"] == {"worker_id": 9, "active_connections": 4}
        assert "config" in payload

    def test_builtin_endpoints_match_get_and_head_only(self) -> None:
        head = maybe_build_builtin_response(
            ServerConfig(health_check_path="/healthz"),
            "HEAD",
            "/healthz",
            worker_id=0,
            active_connections=0,
        )
        response = maybe_build_builtin_response(
            ServerConfig(health_check_path="/healthz", introspection_enabled=True),
            "POST",
            "/healthz",
            worker_id=0,
            active_connections=0,
        )

        assert head is not None
        assert response is None

    def test_head_matches_introspection_builtin(self) -> None:
        response = maybe_build_builtin_response(
            ServerConfig(introspection_enabled=True),
            b"HEAD",
            "/_pounce/info",
            worker_id=4,
            active_connections=2,
        )
        assert response is not None
        assert response.kind == "introspection"

    @pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
    def test_head_matches_dictionary_builtin(self) -> None:
        dictionary = _make_dict()
        response = maybe_build_builtin_response(
            ServerConfig(compression_dictionaries=(dictionary,)),
            b"HEAD",
            f"/.well-known/compression-dictionary/{dictionary.sf_hash}",
            worker_id=0,
            active_connections=0,
        )
        assert response is not None
        assert response.kind == "dictionary"

    @pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
    def test_dictionary_endpoint_serves_configured_bytes(self) -> None:
        dictionary = _make_dict()
        response = maybe_build_builtin_response(
            ServerConfig(compression_dictionaries=(dictionary,)),
            "GET",
            f"/.well-known/compression-dictionary/{dictionary.sf_hash}",
            worker_id=0,
            active_connections=0,
        )

        assert response is not None
        assert response.kind == "dictionary"
        assert response.status == 200
        assert response.body == dictionary.zstd_dict.dict_content
