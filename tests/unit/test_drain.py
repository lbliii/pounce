"""Tests for the shared drain-503 wire format and write helpers."""

from __future__ import annotations

import asyncio
import socket
from typing import cast

import pytest

import pounce._drain as drain_module
from pounce._drain import (
    DRAIN_503_RESPONSE,
    write_drain_503_async,
    write_drain_503_sync,
)


def _split_headers_body(raw: bytes) -> tuple[list[bytes], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    return head.split(b"\r\n"), body


class _RecordingWriter:
    """Minimal StreamWriter stand-in for shutdown failure-path proof."""

    def __init__(self, *, wait_closed_error: OSError | None = None) -> None:
        self.closed = False
        self.wait_closed_called = False
        self.wait_closed_error = wait_closed_error
        self.written = b""

    def write(self, data: bytes) -> None:
        self.written += data

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_called = True
        if self.wait_closed_error is not None:
            raise self.wait_closed_error


def test_drain_503_wire_format() -> None:
    """The canonical 503 carries status, Connection: close, Retry-After and a body."""
    lines, body = _split_headers_body(DRAIN_503_RESPONSE)
    assert lines[0] == b"HTTP/1.1 503 Service Unavailable"
    assert b"Connection: close" in lines
    assert any(line.lower().startswith(b"retry-after:") for line in lines)
    assert body == b"Server shutting down..."


def test_drain_503_content_length_matches_body() -> None:
    """Content-Length must equal the body length (no drift)."""
    lines, body = _split_headers_body(DRAIN_503_RESPONSE)
    declared = next(
        int(line.split(b":", 1)[1].strip())
        for line in lines
        if line.lower().startswith(b"content-length:")
    )
    assert declared == len(body) == 23


def test_write_drain_503_sync_emits_bytes() -> None:
    """The sync writer sends exactly the canonical response over a socketpair."""
    a, b = socket.socketpair()
    try:
        write_drain_503_sync(a)
        a.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            data = b.recv(4096)
            if not data:
                break
            chunks.append(data)
        assert b"".join(chunks) == DRAIN_503_RESPONSE
    finally:
        a.close()
        b.close()


def test_write_drain_503_sync_tolerates_closed_socket() -> None:
    """A peer that has already gone away must not raise."""
    a, b = socket.socketpair()
    b.close()
    a.close()
    # Writing to a closed socket would raise OSError if not suppressed.
    write_drain_503_sync(a)


@pytest.mark.asyncio
async def test_write_drain_503_async_emits_and_closes() -> None:
    """The async writer sends the canonical response and closes the connection."""
    server_sock, client_sock = socket.socketpair()
    server_sock.setblocking(False)

    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.connect_accepted_socket(lambda: protocol, server_sock)
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)

    await write_drain_503_async(writer)

    client_sock.setblocking(True)
    chunks = []
    while True:
        data = client_sock.recv(4096)
        if not data:
            break
        chunks.append(data)
    client_sock.close()
    assert b"".join(chunks) == DRAIN_503_RESPONSE


@pytest.mark.issue(297)
@pytest.mark.asyncio
async def test_write_drain_503_async_closes_after_drain_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reset while draining must not leave transport cleanup to loop teardown."""

    async def _failed_drain(writer: asyncio.StreamWriter, timeout: float) -> None:
        del writer, timeout
        raise OSError("client reset")

    monkeypatch.setattr(drain_module, "drain_with_timeout", _failed_drain)
    writer = _RecordingWriter()

    await write_drain_503_async(cast("asyncio.StreamWriter", writer))

    assert writer.written == DRAIN_503_RESPONSE
    assert writer.closed
    assert writer.wait_closed_called


@pytest.mark.issue(297)
@pytest.mark.asyncio
async def test_write_drain_503_async_tolerates_wait_closed_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer reset reported during close remains a bounded shutdown outcome."""

    async def _completed_drain(writer: asyncio.StreamWriter, timeout: float) -> None:
        del writer, timeout

    monkeypatch.setattr(drain_module, "drain_with_timeout", _completed_drain)
    writer = _RecordingWriter(wait_closed_error=OSError("peer already gone"))

    await write_drain_503_async(cast("asyncio.StreamWriter", writer))

    assert writer.closed
    assert writer.wait_closed_called
