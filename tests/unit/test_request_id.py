"""Tests for pounce._request_id — request ID generation and extraction."""

from pounce._request_id import extract_or_generate, generate_request_id


class TestGenerateRequestId:
    """generate_request_id() produces valid UUID4 hex strings."""

    def test_returns_32_char_hex(self):
        rid = generate_request_id()
        assert len(rid) == 32
        int(rid, 16)  # Valid hex — doesn't raise

    def test_unique_per_call(self):
        ids = {generate_request_id() for _ in range(100)}
        assert len(ids) == 100


class TestExtractOrGenerate:
    """extract_or_generate() honours trusted proxy headers or generates new."""

    def test_generates_when_not_trusted(self):
        headers = ((b"x-request-id", b"incoming-id"),)
        rid = extract_or_generate(headers, trusted=False)
        assert rid != "incoming-id"
        assert len(rid) == 32  # generated UUID4

    def test_extracts_from_trusted_proxy(self):
        headers = ((b"x-request-id", b"abc-123-def"),)
        rid = extract_or_generate(headers, trusted=True)
        assert rid == "abc-123-def"

    def test_generates_when_header_missing_and_trusted(self):
        headers = ((b"host", b"example.com"),)
        rid = extract_or_generate(headers, trusted=True)
        assert len(rid) == 32

    def test_generates_when_header_empty_and_trusted(self):
        headers = ((b"x-request-id", b""),)
        rid = extract_or_generate(headers, trusted=True)
        assert len(rid) == 32

    def test_generates_when_header_whitespace_only(self):
        headers = ((b"x-request-id", b"   "),)
        rid = extract_or_generate(headers, trusted=True)
        assert len(rid) == 32

    def test_case_insensitive_header_name(self):
        headers = ((b"X-Request-ID", b"MyId"),)
        rid = extract_or_generate(headers, trusted=True)
        assert rid == "MyId"

    def test_generates_when_no_headers(self):
        rid = extract_or_generate((), trusted=True)
        assert len(rid) == 32

    def test_generates_when_not_trusted_no_headers(self):
        rid = extract_or_generate((), trusted=False)
        assert len(rid) == 32
