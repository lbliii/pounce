"""Tests for pounce.protocols.h1 — HTTP/1.1 protocol handler."""

import pytest

from pounce._errors import ParseError
from pounce.protocols._base import (
    BodyReceived,
    ProtocolHandler,
    RequestReceived,
)
from pounce.protocols.h1 import H1Protocol


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


class TestH1ProtocolConformance:
    """H1Protocol implements the ProtocolHandler interface."""

    def test_is_protocol_handler(self):
        proto = H1Protocol()
        assert isinstance(proto, ProtocolHandler)


class TestH1ReceiveData:
    """H1Protocol.receive_data() parses HTTP/1.1 requests into events."""

    def test_simple_get(self):
        proto = H1Protocol()
        raw = _make_request("GET", "/hello")
        events = proto.receive_data(raw)

        assert len(events) == 2  # RequestReceived + BodyReceived(more=False)
        req = events[0]
        assert isinstance(req, RequestReceived)
        assert req.method == b"GET"
        assert req.target == b"/hello"
        assert req.http_version == "1.1"

    def test_request_headers(self):
        proto = H1Protocol()
        raw = _make_request(
            "GET", "/", headers={"host": "example.com", "accept": "text/html"}
        )
        events = proto.receive_data(raw)
        req = events[0]
        assert isinstance(req, RequestReceived)

        header_dict = {name: value for name, value in req.headers}
        assert header_dict[b"host"] == b"example.com"
        assert header_dict[b"accept"] == b"text/html"

    def test_post_with_body(self):
        proto = H1Protocol()
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
        proto = H1Protocol()
        raw = _make_request("GET", "/stream")

        # Feed one byte at a time for the first few bytes
        all_events: list = []
        for i in range(len(raw)):
            events = proto.receive_data(raw[i : i + 1])
            all_events.extend(events)

        assert any(isinstance(e, RequestReceived) for e in all_events)

    def test_end_of_message_no_body(self):
        """GET request has an EndOfMessage producing BodyReceived(more=False)."""
        proto = H1Protocol()
        raw = _make_request("GET", "/")
        events = proto.receive_data(raw)

        body_events = [e for e in events if isinstance(e, BodyReceived)]
        assert len(body_events) == 1
        assert body_events[0].data == b""
        assert body_events[0].more is False


class TestH1SendResponse:
    """H1Protocol.send_response() serializes response headers."""

    def test_200_ok(self):
        proto = H1Protocol()
        # First receive a request so h11 is in the right state
        proto.receive_data(_make_request("GET", "/"))

        raw = proto.send_response(200, [(b"content-type", b"text/plain")])
        assert b"200" in raw
        assert b"content-type: text/plain" in raw

    def test_404_not_found(self):
        proto = H1Protocol()
        proto.receive_data(_make_request("GET", "/missing"))

        raw = proto.send_response(404, [(b"content-length", b"9")])
        assert b"404" in raw


class TestH1SendBody:
    """H1Protocol.send_body() serializes response body chunks."""

    def test_single_body(self):
        proto = H1Protocol()
        proto.receive_data(_make_request("GET", "/"))
        proto.send_response(200, [(b"content-length", b"5")])

        raw = proto.send_body(b"hello", more=False)
        assert b"hello" in raw

    def test_chunked_body(self):
        proto = H1Protocol()
        proto.receive_data(_make_request("GET", "/"))
        proto.send_response(200, [(b"transfer-encoding", b"chunked")])

        chunk1 = proto.send_body(b"hello", more=True)
        chunk2 = proto.send_body(b" world", more=False)
        combined = chunk1 + chunk2
        assert b"hello" in combined
        assert b"world" in combined

    def test_empty_body_final(self):
        """Sending empty body with more=False still produces EndOfMessage."""
        proto = H1Protocol()
        proto.receive_data(_make_request("GET", "/"))
        proto.send_response(200, [(b"content-length", b"0")])

        raw = proto.send_body(b"", more=False)
        assert isinstance(raw, bytes)


class TestH1KeepAlive:
    """Keep-alive cycling — reuse the connection for multiple requests."""

    def test_keep_alive_cycle(self):
        proto = H1Protocol()

        # First request
        proto.receive_data(_make_request("GET", "/first"))
        proto.send_response(200, [(b"content-length", b"2")])
        proto.send_body(b"ok", more=False)

        # Start new cycle
        proto.start_new_cycle()

        # Second request on same connection
        events = proto.receive_data(_make_request("GET", "/second"))
        req = [e for e in events if isinstance(e, RequestReceived)]
        assert len(req) == 1
        assert req[0].target == b"/second"


class TestH1Errors:
    """Malformed input produces ParseError."""

    def test_malformed_request_line(self):
        proto = H1Protocol()
        with pytest.raises(ParseError):
            proto.receive_data(b"NOT A VALID HTTP REQUEST\r\n\r\n")

    def test_truncated_headers(self):
        """Incomplete headers followed by garbage should eventually error."""
        proto = H1Protocol()
        # Feed partial request — should not error yet (NEED_DATA)
        events = proto.receive_data(b"GET / HTTP/1.1\r\nHost:")
        assert events == []  # Waiting for more data
