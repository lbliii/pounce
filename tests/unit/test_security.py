"""Tests for HTTP security — smuggling prevention, CRLF injection, limits.

Verifies that h11 (via pounce's H1Protocol) correctly rejects ambiguous
framing that could enable request smuggling attacks.

RFC 9112 Section 6.1: If a message is received with both Transfer-Encoding
and Content-Length, the Transfer-Encoding overrides.  But a server SHOULD
reject such messages as malformed to prevent smuggling.

"""

import pytest

from pounce._errors import ParseError
from pounce._fast_h1 import ParseError as FastParseError
from pounce._fast_h1 import parse_request
from pounce._headers import strip_crlf
from pounce._proxy import apply_proxy_headers
from pounce._request_id import extract_or_generate
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
        with pytest.raises(ParseError, match=r"[Cc]ontent-[Ll]ength"):
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
        raw = b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Length: 5\r\n\r\nhello"
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
        with pytest.raises(ParseError, match=r"[Tt]ransfer-[Ee]ncoding"):
            proto.receive_data(raw)

    def test_null_in_header_value_rejected(self):
        """Null bytes in header values are rejected."""
        proto = H1Protocol()
        raw = b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Injected: value\x00evil\r\n\r\n"
        with pytest.raises(ParseError):
            proto.receive_data(raw)

    def test_cr_without_lf_rejected(self):
        """Bare CR without LF in headers is rejected."""
        proto = H1Protocol()
        raw = b"GET / HTTP/1.1\r\nHost: localhost\r\nX-Bad: value\rinjection\r\n\r\n"
        with pytest.raises(ParseError):
            proto.receive_data(raw)


class TestCRLFInjection:
    """CRLF injection prevention in proxy headers and request IDs."""

    def test_strip_crlf_removes_cr_lf(self):
        assert strip_crlf("good\r\nX-Injected: evil") == "goodX-Injected: evil"

    def test_strip_crlf_passthrough_clean(self):
        assert strip_crlf("clean-value") == "clean-value"

    def test_request_id_strips_crlf_from_trusted_header(self):
        """Trusted X-Request-ID with CRLF must be sanitized."""
        headers = ((b"x-request-id", b"evil\r\nX-Injected: yes"),)
        result = extract_or_generate(headers, trusted=True)
        assert "\r" not in result
        assert "\n" not in result
        assert result == "evilX-Injected: yes"

    def test_forwarded_for_strips_crlf(self):
        """X-Forwarded-For with CRLF must be sanitized."""
        scope = {
            "client": ("127.0.0.1", 12345),
            "headers": [
                [b"x-forwarded-for", b"1.2.3.4\r\nX-Injected: yes"],
            ],
        }
        result = apply_proxy_headers(scope, trusted_hosts=frozenset({"127.0.0.1"}))
        client_ip = result["client"][0]
        assert "\r" not in client_ip
        assert "\n" not in client_ip

    def test_forwarded_host_strips_crlf(self):
        """X-Forwarded-Host with CRLF must be sanitized."""
        scope = {
            "client": ("127.0.0.1", 12345),
            "server": ("localhost", 8000),
            "headers": [
                [b"x-forwarded-host", b"evil.com\r\nX-Injected: yes"],
            ],
        }
        result = apply_proxy_headers(scope, trusted_hosts=frozenset({"127.0.0.1"}))
        host = result["server"][0]
        assert "\r" not in host
        assert "\n" not in host


class TestMaxHeadersEnforcement:
    """Fast H1 parser must reject requests exceeding max_headers."""

    def _make_request(self, num_headers: int) -> bytes:
        headers = "".join(f"X-H-{i}: value{i}\r\n" for i in range(num_headers))
        return f"GET / HTTP/1.1\r\nHost: localhost\r\n{headers}\r\n".encode()

    def test_within_limit_accepted(self):
        raw = self._make_request(10)
        buf = memoryview(raw)
        result, _, _, _ = parse_request(buf, len(raw), max_headers=100)
        assert result is not None

    def test_exceeding_limit_rejected(self):
        raw = self._make_request(101)
        buf = memoryview(raw)
        with pytest.raises(FastParseError, match="Too many headers"):
            parse_request(buf, len(raw), max_headers=100)

    def test_exact_limit_accepted(self):
        """Exactly max_headers headers should be accepted (including Host)."""
        raw = self._make_request(99)  # 99 + Host = 100
        buf = memoryview(raw)
        result, _, _, _ = parse_request(buf, len(raw), max_headers=100)
        assert result is not None
