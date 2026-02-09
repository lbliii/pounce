"""Tests for HTTP request smuggling prevention.

Verifies that h11 (via pounce's H1Protocol) correctly rejects ambiguous
framing that could enable request smuggling attacks.

RFC 9112 Section 6.1: If a message is received with both Transfer-Encoding
and Content-Length, the Transfer-Encoding overrides.  But a server SHOULD
reject such messages as malformed to prevent smuggling.

"""

import pytest

from pounce._errors import ParseError
from pounce.protocols._base import RequestReceived
from pounce.protocols.h1 import H1Protocol


class TestContentLengthTransferEncodingConflict:
    """Requests with both CL and TE must be rejected or handled safely."""

    def test_cl_and_te_chunked_uses_chunked_framing(self):
        """CL.TE: When both are present, h11 uses chunked framing (TE wins).

        Per RFC 9112 Section 6.1, Transfer-Encoding overrides Content-Length.
        h11 correctly uses chunked framing for body delivery. The CL header
        may be passed through as metadata, but pounce's ASGI bridge controls
        body delivery via receive(), so the CL value is never used for framing.
        """
        proto = H1Protocol()
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: 6\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"5\r\nhello\r\n0\r\n\r\n"
        )
        events = proto.receive_data(raw)
        # h11 accepts and uses chunked framing — body is "hello" (5 bytes),
        # not "5\r\nhe" (6 bytes that CL would imply if it were used)
        assert any(isinstance(e, RequestReceived) for e in events)
        from pounce.protocols._base import BodyReceived

        body_data = b"".join(e.data for e in events if isinstance(e, BodyReceived))
        assert body_data == b"hello"  # chunked framing, not CL framing

    def test_duplicate_content_length_different_values_rejected(self):
        """Duplicate Content-Length headers with conflicting values are rejected."""
        proto = H1Protocol()
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: 5\r\n"
            b"Content-Length: 10\r\n"
            b"\r\n"
            b"hello"
        )
        with pytest.raises(ParseError, match="[Cc]ontent-[Ll]ength"):
            proto.receive_data(raw)

    def test_duplicate_content_length_same_value_accepted(self):
        """Duplicate Content-Length with the same value is safely handled."""
        proto = H1Protocol()
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: 5\r\n"
            b"Content-Length: 5\r\n"
            b"\r\n"
            b"hello"
        )
        # h11 may accept (deduplicate) or reject — both are safe
        try:
            events = proto.receive_data(raw)
            # If accepted, it must be a valid RequestReceived
            assert any(isinstance(e, RequestReceived) for e in events)
        except ParseError:
            pass  # Rejection is also safe

    def test_te_chunked_without_cl_accepted(self):
        """Transfer-Encoding: chunked alone is valid and accepted."""
        proto = H1Protocol()
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
            b"5\r\nhello\r\n0\r\n\r\n"
        )
        events = proto.receive_data(raw)
        assert any(isinstance(e, RequestReceived) for e in events)

    def test_cl_without_te_accepted(self):
        """Content-Length alone is valid and accepted."""
        proto = H1Protocol()
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: 5\r\n"
            b"\r\n"
            b"hello"
        )
        events = proto.receive_data(raw)
        assert any(isinstance(e, RequestReceived) for e in events)

    def test_te_te_obfuscation_rejected(self):
        """TE.TE: Multiple Transfer-Encoding headers are rejected.

        Attackers may try:
            Transfer-Encoding: chunked
            Transfer-Encoding: identity
        """
        proto = H1Protocol()
        raw = (
            b"POST / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"Transfer-Encoding: identity\r\n"
            b"\r\n"
        )
        with pytest.raises(ParseError, match="[Tt]ransfer-[Ee]ncoding"):
            proto.receive_data(raw)

    def test_null_in_header_value_rejected(self):
        """Null bytes in header values are rejected."""
        proto = H1Protocol()
        raw = (
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Injected: value\x00evil\r\n"
            b"\r\n"
        )
        with pytest.raises(ParseError):
            proto.receive_data(raw)

    def test_cr_without_lf_rejected(self):
        """Bare CR without LF in headers is rejected."""
        proto = H1Protocol()
        raw = (
            b"GET / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Bad: value\rinjection\r\n"
            b"\r\n"
        )
        with pytest.raises(ParseError):
            proto.receive_data(raw)
