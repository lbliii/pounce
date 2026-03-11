"""Tests for pounce._response_frame — pre-built HTTP response serialization."""

from pounce._response_frame import (
    get_date_header_bytes,
    serialize_raw_response,
    serialize_raw_response_parts,
)


def test_serialize_raw_response_basic() -> None:
    raw = serialize_raw_response(
        200,
        ((b"content-type", b"application/json"), (b"content-length", b"2")),
        b"{}",
        date_header=None,
    )
    assert raw.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"server: pounce\r\n" in raw
    assert b"content-type: application/json\r\n" in raw
    assert raw.endswith(b"\r\n\r\n{}")


def test_serialize_raw_response_with_date() -> None:
    date_hdr = get_date_header_bytes()
    assert date_hdr.startswith(b"date: ")
    assert date_hdr.endswith(b"\r\n")
    raw = serialize_raw_response(
        200,
        ((b"content-type", b"text/html"),),
        b"<html></html>",
        date_header=date_hdr,
    )
    assert date_hdr in raw


def test_serialize_raw_response_custom_server() -> None:
    raw = serialize_raw_response(
        200,
        (),
        b"",
        server_header="custom",
        date_header=None,
    )
    assert b"server: custom\r\n" in raw


def test_serialize_raw_response_status_reasons() -> None:
    for status, reason in [(404, b"Not Found"), (500, b"Internal Server Error")]:
        raw = serialize_raw_response(status, (), b"", date_header=None)
        assert reason in raw


def test_serialize_raw_response_parts() -> None:
    """Parts (head, body) match concatenated serialize_raw_response."""
    headers = ((b"content-type", b"application/json"), (b"content-length", b"2"))
    body = b"{}"
    head, body_out = serialize_raw_response_parts(200, headers, body, date_header=None)
    assert head.startswith(b"HTTP/1.1 200 OK\r\n")
    assert b"content-type: application/json\r\n" in head
    assert head.endswith(b"\r\n\r\n")
    assert body_out == body
    full = serialize_raw_response(200, headers, body, date_header=None)
    assert head + body_out == full
