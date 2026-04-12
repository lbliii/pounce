"""Tests for pounce._dictionary_endpoint — RFC 9842 dictionary serving."""

import json

import pytest

from pounce._compression import CompressionDictionary, _HAS_ZSTD
from pounce._dictionary_endpoint import (
    DICTIONARY_PATH_PREFIX,
    build_dictionary_response,
    use_as_dictionary_headers,
)


def _make_dict(match: str = "/api/*") -> CompressionDictionary:
    from compression import zstd

    samples = [
        json.dumps({"id": i, "name": f"item_{i}", "status": "active"}).encode()
        for i in range(200)
    ]
    d = zstd.train_dict(samples, dict_size=8192)
    return CompressionDictionary(d.dict_content, match)


@pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
class TestBuildDictionaryResponse:
    """build_dictionary_response serves dictionaries by sf-hash."""

    def test_matching_hash_returns_200(self):
        cd = _make_dict()
        path = f"{DICTIONARY_PATH_PREFIX}{cd.sf_hash}"
        resp = build_dictionary_response((cd,), path)
        assert resp is not None
        status, headers, body = resp
        assert status == 200
        assert body == cd.zstd_dict.dict_content

    def test_response_headers(self):
        cd = _make_dict()
        path = f"{DICTIONARY_PATH_PREFIX}{cd.sf_hash}"
        _, headers, body = build_dictionary_response((cd,), path)
        header_dict = dict(headers)
        assert header_dict[b"content-type"] == b"application/dictionary"
        assert header_dict[b"content-length"] == str(len(body)).encode()
        assert b"immutable" in header_dict[b"cache-control"]

    def test_wrong_hash_returns_404(self):
        cd = _make_dict()
        path = f"{DICTIONARY_PATH_PREFIX}:bm9tYXRjaA==:"
        resp = build_dictionary_response((cd,), path)
        assert resp is not None
        assert resp[0] == 404

    def test_empty_hash_returns_404(self):
        cd = _make_dict()
        resp = build_dictionary_response((cd,), DICTIONARY_PATH_PREFIX)
        assert resp is not None
        assert resp[0] == 404

    def test_non_dictionary_path_returns_none(self):
        cd = _make_dict()
        assert build_dictionary_response((cd,), "/api/items") is None

    def test_empty_dictionaries(self):
        assert build_dictionary_response((), f"{DICTIONARY_PATH_PREFIX}:abc:") is not None
        status = build_dictionary_response((), f"{DICTIONARY_PATH_PREFIX}:abc:")[0]
        assert status == 404

    def test_multiple_dictionaries(self):
        cd1 = _make_dict(match="/api/v1/*")
        cd2 = _make_dict(match="/api/v2/*")
        # cd1 and cd2 may have same content/hash since same training data
        path1 = f"{DICTIONARY_PATH_PREFIX}{cd1.sf_hash}"
        resp = build_dictionary_response((cd1, cd2), path1)
        assert resp is not None
        assert resp[0] == 200


@pytest.mark.skipif(not _HAS_ZSTD, reason="zstd not available")
class TestUseAsDictionaryHeaders:
    """use_as_dictionary_headers advertises dictionaries for matching paths."""

    def test_matching_path(self):
        cd = _make_dict(match="/api/*")
        headers = use_as_dictionary_headers((cd,), "/api/v1/items")
        assert len(headers) == 2
        # First header: Use-As-Dictionary
        assert headers[0][0] == b"use-as-dictionary"
        assert b"/api/*" in headers[0][1]
        # Second header: Link with rel="dictionary"
        assert headers[1][0] == b"link"
        assert b"dictionary" in headers[1][1]
        assert DICTIONARY_PATH_PREFIX.encode() in headers[1][1]

    def test_non_matching_path(self):
        cd = _make_dict(match="/api/*")
        headers = use_as_dictionary_headers((cd,), "/other/path")
        assert len(headers) == 0

    def test_empty_match_skipped(self):
        """Dictionaries with empty match pattern don't advertise."""
        cd = _make_dict(match="")
        headers = use_as_dictionary_headers((cd,), "/anything")
        assert len(headers) == 0

    def test_empty_dictionaries(self):
        headers = use_as_dictionary_headers((), "/api/v1")
        assert len(headers) == 0

    def test_multiple_matching_dictionaries(self):
        cd1 = _make_dict(match="/api/*")
        cd2 = _make_dict(match="/api/v1/*")
        headers = use_as_dictionary_headers((cd1, cd2), "/api/v1/items")
        # Both match — should produce 4 headers (2 per dictionary)
        assert len(headers) == 4

    def test_link_header_contains_dict_hash(self):
        cd = _make_dict(match="/api/*")
        headers = use_as_dictionary_headers((cd,), "/api/test")
        link_value = headers[1][1].decode()
        assert cd.sf_hash in link_value
