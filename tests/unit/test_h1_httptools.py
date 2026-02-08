"""Tests for pounce.protocols.h1_httptools — httptools-backed HTTP/1.1 handler.

Runs the core parsing and serialization tests against the httptools
backend to verify interface compatibility with H1Protocol (h11).

"""

import pytest

from pounce._errors import ParseError
from pounce.protocols._base import (
    BodyReceived,
    RequestReceived,
)

# Skip entire module if httptools is not installed
httptools = pytest.importorskip("httptools")

from pounce.protocols.h1_httptools import H1HttpToolsProtocol, is_httptools_available  # noqa: E402


def _make_request(
    method: str = "GET",
    path: str = "/",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    http_version: str = "1.1",
) -> bytes:
    """Build raw HTTP/1.1 request bytes for testing."""
    hdrs = headers or {}
    if "host" not in hdrs:
        hdrs["host"] = "localhost"
    if body is not None and "content-length" not in hdrs:
        hdrs["content-length"] = str(len(body))

    lines = [f"{method} {path} HTTP/{http_version}"]
    for name, value in hdrs.items():
        lines.append(f"{name}: {value}")
    lines.append("")
    lines.append("")
    raw = "\r\n".join(lines).encode("ascii")
    if body is not None:
        raw += body
    return raw


class TestHttpToolsAvailability:
    """httptools detection works."""

    def test_is_available(self):
        assert is_httptools_available() is True


class TestHttpToolsReceiveData:
    """H1HttpToolsProtocol.receive_data() parses requests into events."""

    def test_simple_get(self):
        proto = H1HttpToolsProtocol()
        raw = _make_request("GET", "/hello")
        events = proto.receive_data(raw)

        assert len(events) == 2  # RequestReceived + BodyReceived(more=False)
        req = events[0]
        assert isinstance(req, RequestReceived)
        assert req.method == b"GET"
        assert req.target == b"/hello"

    def test_request_headers(self):
        proto = H1HttpToolsProtocol()
        raw = _make_request(
            "GET", "/", headers={"host": "example.com", "accept": "text/html"}
        )
        events = proto.receive_data(raw)
        req = events[0]
        assert isinstance(req, RequestReceived)

        header_dict = {name.lower(): value for name, value in req.headers}
        assert header_dict[b"host"] == b"example.com"
        assert header_dict[b"accept"] == b"text/html"

    def test_post_with_body(self):
        proto = H1HttpToolsProtocol()
        body = b'{"key": "value"}'
        raw = _make_request(
            "POST",
            "/api/data",
            headers={"content-type": "application/json"},
            body=body,
        )
        events = proto.receive_data(raw)

        assert isinstance(events[0], RequestReceived)
        assert events[0].method == b"POST"

        # Body events
        body_events = [e for e in events if isinstance(e, BodyReceived)]
        received_body = b"".join(e.data for e in body_events)
        assert received_body == body

        # Last body event should have more=False
        assert body_events[-1].more is False

    def test_incremental_data(self):
        """Data arrives in multiple chunks."""
        proto = H1HttpToolsProtocol()
        raw = _make_request("GET", "/stream")

        all_events: list = []
        for i in range(len(raw)):
            events = proto.receive_data(raw[i : i + 1])
            all_events.extend(events)

        assert any(isinstance(e, RequestReceived) for e in all_events)

    def test_end_of_message_no_body(self):
        """GET request has BodyReceived(more=False) as terminal marker."""
        proto = H1HttpToolsProtocol()
        raw = _make_request("GET", "/")
        events = proto.receive_data(raw)

        body_events = [e for e in events if isinstance(e, BodyReceived)]
        assert len(body_events) == 1
        assert body_events[0].data == b""
        assert body_events[0].more is False

    def test_large_post_body(self):
        """Large body is delivered correctly."""
        proto = H1HttpToolsProtocol()
        body = b"X" * 65536
        raw = _make_request("POST", "/upload", body=body)
        events = proto.receive_data(raw)

        body_events = [e for e in events if isinstance(e, BodyReceived)]
        received_body = b"".join(e.data for e in body_events)
        assert received_body == body


class TestHttpToolsSendResponse:
    """H1HttpToolsProtocol.send_response() serializes response headers."""

    def test_200_ok(self):
        proto = H1HttpToolsProtocol()
        # Process a request first
        proto.receive_data(_make_request("GET", "/"))

        raw = proto.send_response(200, [(b"content-type", b"text/plain")])
        assert b"HTTP/1.1 200 OK" in raw
        assert b"content-type: text/plain" in raw

    def test_404_not_found(self):
        proto = H1HttpToolsProtocol()
        proto.receive_data(_make_request("GET", "/missing"))

        raw = proto.send_response(404, [(b"content-length", b"9")])
        assert b"404" in raw
        assert b"Not Found" in raw

    def test_headers_crlf_terminated(self):
        """Each header line ends with CRLF, blank line terminates."""
        proto = H1HttpToolsProtocol()
        proto.receive_data(_make_request("GET", "/"))

        raw = proto.send_response(200, [(b"x-custom", b"value")])
        assert raw.endswith(b"\r\n\r\n")


class TestHttpToolsSendBody:
    """H1HttpToolsProtocol.send_body() returns body bytes."""

    def test_single_body(self):
        proto = H1HttpToolsProtocol()
        proto.receive_data(_make_request("GET", "/"))

        raw = proto.send_body(b"hello", more=False)
        assert raw == b"hello"

    def test_empty_body(self):
        proto = H1HttpToolsProtocol()
        proto.receive_data(_make_request("GET", "/"))

        raw = proto.send_body(b"", more=False)
        assert raw == b""


class TestHttpToolsKeepAlive:
    """Keep-alive cycling works."""

    def test_keep_alive_cycle(self):
        proto = H1HttpToolsProtocol()

        # First request
        proto.receive_data(_make_request("GET", "/first"))
        proto.send_response(200, [(b"content-length", b"2")])
        proto.send_body(b"ok", more=False)

        # Start new cycle
        proto.start_new_cycle()

        # Second request
        events = proto.receive_data(_make_request("GET", "/second"))
        req = [e for e in events if isinstance(e, RequestReceived)]
        assert len(req) == 1
        assert req[0].target == b"/second"


class TestHttpToolsErrors:
    """Malformed input produces ParseError."""

    def test_malformed_request(self):
        proto = H1HttpToolsProtocol()
        with pytest.raises(ParseError):
            proto.receive_data(b"NOT A VALID HTTP REQUEST\r\n\r\n")
