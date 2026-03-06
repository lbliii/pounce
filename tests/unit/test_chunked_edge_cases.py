"""Unit tests for chunked transfer-encoding edge cases.

Verifies H1Protocol (via h11) handles malformed or invalid chunked bodies
without crashing. Documents h11 behavior for truncated/malformed chunks.
"""

import pytest

from pounce._errors import ParseError
from pounce.protocols._base import RequestReceived
from pounce.protocols.h1 import H1Protocol


class TestInvalidChunkedEncoding:
    """Malformed chunked bodies — no crash, ParseError or valid events."""

    def test_malformed_chunk_size_non_hex_rejected(self):
        """Chunk size with non-hex characters is rejected by h11."""
        proto = H1Protocol()
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"X\r\n"  # Invalid hex chunk size
        )
        with pytest.raises(ParseError):
            proto.receive_data(raw)

    def test_truncated_chunk_incomplete_returns_partial_or_waits(self):
        """Truncated chunk '5\\r\\nhe' — h11 may return partial or need more data."""
        proto = H1Protocol()
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"5\r\nhe"  # Incomplete: declared 5 bytes, only 2 sent
        )
        # h11 may return RequestReceived + partial BodyReceived, or need more data
        events = proto.receive_data(raw)
        assert len(events) >= 1
        assert any(isinstance(e, RequestReceived) for e in events)
        # No crash — either we get events or we'd need more data in real usage

    def test_empty_chunk_without_trailing_crlf(self):
        """Chunk terminator '0\\r\\n' without trailing \\r\\n — incomplete."""
        proto = H1Protocol()
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"0\r\n"  # Missing \r\n after final chunk
        )
        events = proto.receive_data(raw)
        # h11 may return RequestReceived; body may be incomplete
        assert len(events) >= 1
        assert any(isinstance(e, RequestReceived) for e in events)
