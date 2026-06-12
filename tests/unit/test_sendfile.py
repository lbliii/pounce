"""Tests for zero-copy sendfile support and StaticFiles sendfile integration."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from unittest.mock import MagicMock

import pytest

from pounce._sendfile import can_use_sendfile, create_sendfile_callable
from pounce._static import StaticFiles, StaticMount

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_writer(*, ssl: bool = False, has_socket: bool = True, fd: int = 7) -> MagicMock:
    """Build a mock asyncio.StreamWriter with configurable extras."""
    writer = MagicMock(spec=asyncio.StreamWriter)
    extras: dict[str, object] = {}
    if ssl:
        extras["ssl_object"] = MagicMock()
    if has_socket:
        sock = MagicMock()
        sock.fileno.return_value = fd
        extras["socket"] = sock
    writer.get_extra_info = lambda key, default=None: extras.get(key, default)
    return writer


class _SentMessages:
    """Collects ASGI messages sent via the mock send callable."""

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def __call__(self, msg: dict) -> None:
        self.messages.append(msg)


# ---------------------------------------------------------------------------
# can_use_sendfile
# ---------------------------------------------------------------------------


class TestCanUseSendfile:
    def test_plain_connection(self):
        assert can_use_sendfile(_mock_writer()) is True

    def test_tls_returns_false(self):
        assert can_use_sendfile(_mock_writer(ssl=True)) is False

    def test_no_socket_returns_false(self):
        assert can_use_sendfile(_mock_writer(has_socket=False)) is False

    def test_tls_with_socket_returns_false(self):
        """TLS check takes precedence even when socket is available."""
        assert can_use_sendfile(_mock_writer(ssl=True, has_socket=True)) is False

    def test_no_os_sendfile(self):
        sf = getattr(os, "sendfile", None)
        try:
            if hasattr(os, "sendfile"):
                delattr(os, "sendfile")
            assert can_use_sendfile(_mock_writer()) is False
        finally:
            if sf is not None:
                os.sendfile = sf


# ---------------------------------------------------------------------------
# create_sendfile_callable
# ---------------------------------------------------------------------------


async def _stream_writer_pair() -> tuple[asyncio.StreamWriter, socket.socket]:
    """Build a real ``StreamWriter`` over a socketpair.

    Returns ``(writer, peer)`` where ``writer`` wraps one connected socket
    (the sending side, registered on the event loop) and ``peer`` is the
    raw receiving socket.  This exercises the real ``loop.sendfile`` path
    instead of mocking ``os.sendfile``.
    """
    loop = asyncio.get_running_loop()
    send_sock, peer = socket.socketpair()
    send_sock.setblocking(False)
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    transport, _ = await loop.create_connection(lambda: protocol, sock=send_sock)
    writer = asyncio.StreamWriter(transport, protocol, reader, loop)
    return writer, peer


def _recv_exact(sock: socket.socket, n: int, timeout: float = 2.0) -> bytes:
    """Read exactly ``n`` bytes from ``sock`` (or until it closes)."""
    sock.settimeout(timeout)
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
    return buf


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(OSError, ConnectionError):
        await writer.wait_closed()


class TestCreateSendfileCallable:
    @pytest.mark.asyncio
    async def test_transfers_full_file(self, tmp_path):
        """sendfile callable transfers the entire file when count == file size."""
        payload = b"Hello, sendfile!"
        test_file = tmp_path / "hello.txt"
        test_file.write_bytes(payload)

        writer, peer = await _stream_writer_pair()
        try:
            fn = create_sendfile_callable(writer)
            await fn(test_file, 0, len(payload))
            assert _recv_exact(peer, len(payload)) == payload
        finally:
            await _close_writer(writer)
            peer.close()

    @pytest.mark.asyncio
    async def test_transfers_with_offset(self, tmp_path):
        """An offset/count transfers only the requested slice of the file."""
        data = bytes(range(256)) * 4  # 1024 bytes
        test_file = tmp_path / "data.bin"
        test_file.write_bytes(data)

        writer, peer = await _stream_writer_pair()
        try:
            fn = create_sendfile_callable(writer)
            await fn(test_file, 50, 100)
            assert _recv_exact(peer, 100) == data[50:150]
        finally:
            await _close_writer(writer)
            peer.close()

    @pytest.mark.asyncio
    async def test_transfers_larger_than_buffer(self, tmp_path):
        """A transfer larger than the socket send buffer completes via back-pressure.

        The receiver drains concurrently so the kernel send buffer cannot
        wedge — the EAGAIN retry path inside ``loop.sendfile`` keeps the
        transfer flowing instead of crashing (issue #72).
        """
        payload = bytes(range(256)) * 8192  # 2 MiB — exceeds SO_SNDBUF
        test_file = tmp_path / "big.bin"
        test_file.write_bytes(payload)

        writer, peer = await _stream_writer_pair()
        loop = asyncio.get_running_loop()
        # Drain the peer in a thread so sendfile is not permanently blocked.
        recv_future = loop.run_in_executor(None, _recv_exact, peer, len(payload), 10.0)
        try:
            fn = create_sendfile_callable(writer)
            await fn(test_file, 0, len(payload))
            received = await recv_future
            assert received == payload
        finally:
            await _close_writer(writer)
            peer.close()

    @pytest.mark.asyncio
    async def test_zero_count_is_noop(self, tmp_path):
        """count <= 0 returns without touching the socket."""
        test_file = tmp_path / "empty.bin"
        test_file.write_bytes(b"")

        writer, peer = await _stream_writer_pair()
        try:
            fn = create_sendfile_callable(writer)
            await fn(test_file, 0, 0)  # must not raise
            peer.settimeout(0.2)
            with pytest.raises(TimeoutError):
                peer.recv(16)
        finally:
            await _close_writer(writer)
            peer.close()

    @pytest.mark.asyncio
    async def test_short_transfer_aborts_connection(self, tmp_path):
        """If the file yields fewer bytes than count, the connection is aborted.

        The framing was already committed for `count` bytes, so a short
        transfer would otherwise leave a truncated/desynced keep-alive
        response.  Guards the pounce.response.sendfile extension against a
        caller passing count > file size (and TOCTOU truncation).
        """
        test_file = tmp_path / "short.bin"
        test_file.write_bytes(b"only-ten!!")  # 10 bytes

        writer, peer = await _stream_writer_pair()
        try:
            fn = create_sendfile_callable(writer)
            await fn(test_file, 0, 100)  # ask for 100, only 10 exist
            # Transport was aborted, so the writer is now closing.
            assert writer.is_closing()
        finally:
            await _close_writer(writer)
            peer.close()

    @pytest.mark.asyncio
    async def test_closed_writer_is_noop(self, tmp_path):
        """A closing connection aborts cleanly instead of crashing."""
        test_file = tmp_path / "x.bin"
        test_file.write_bytes(b"abc")

        writer, peer = await _stream_writer_pair()
        fn = create_sendfile_callable(writer)
        await _close_writer(writer)
        peer.close()
        # Must not raise even though the transport is gone.
        await fn(test_file, 0, 3)

    @pytest.mark.asyncio
    async def test_client_disconnect_does_not_raise(self, tmp_path):
        """If the peer vanishes mid-transfer, sendfile aborts without raising."""
        payload = bytes(range(256)) * 8192  # 2 MiB
        test_file = tmp_path / "drop.bin"
        test_file.write_bytes(payload)

        writer, peer = await _stream_writer_pair()
        loop = asyncio.get_running_loop()
        try:
            fn = create_sendfile_callable(writer)
            task = asyncio.create_task(fn(test_file, 0, len(payload)))
            # Read a little in a thread (so the loop keeps running the sendfile
            # task), then slam the connection shut while bytes remain.
            await loop.run_in_executor(None, _recv_exact, peer, 4096, 2.0)
            peer.close()
            # Should swallow ConnectionError/BrokenPipeError rather than crash.
            await asyncio.wait_for(task, timeout=5.0)
        finally:
            await _close_writer(writer)


# ---------------------------------------------------------------------------
# StaticFiles sendfile integration
# ---------------------------------------------------------------------------


class TestStaticFilesSendfile:
    """Tests that StaticFiles emits sendfile intents when the scope advertises support."""

    # A body must be at least this large for StaticFiles to emit a sendfile
    # intent (matches ``_static._SENDFILE_MIN_SIZE``). Tests use a file well
    # above it so the intent path is exercised; small-file tests assert the
    # fallback path is taken instead (issue #127).
    LARGE_SIZE = 32 * 1024  # 32 KiB, comfortably above the 16 KiB gate

    @pytest.fixture
    def static_dir(self, tmp_path):
        (tmp_path / "hello.txt").write_text("Hello, World!")
        (tmp_path / "index.html").write_text("<h1>Index</h1>")
        # A file large enough to clear the sendfile minimum-size gate.
        (tmp_path / "big.bin").write_bytes(b"\xa5" * self.LARGE_SIZE)
        return tmp_path

    @pytest.fixture
    def handler(self, static_dir):
        return StaticFiles(mounts=[StaticMount(url_path="/static", directory=static_dir)])

    def _scope(self, path="/static/hello.txt", method="GET", *, sendfile_enabled=False):
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
            "root_path": "",
            "scheme": "http",
            "server": ("127.0.0.1", 8000),
        }
        if sendfile_enabled:
            scope["extensions"] = {"pounce.sendfile": {"version": 1}}
        return scope

    @pytest.mark.asyncio
    async def test_sendfile_intent_used_for_full_file(self, handler, static_dir):
        """When sendfile is advertised, StaticFiles emits a file-range intent.

        Uses a file above the sendfile minimum-size gate (issue #127) so the
        intent path — not the small-file fallback — is exercised.
        """
        sent = _SentMessages()
        scope = self._scope(path="/static/big.bin", sendfile_enabled=True)
        await handler(scope, None, sent)

        assert len(sent.messages) == 2
        assert sent.messages[0]["type"] == "http.response.start"
        assert sent.messages[0]["status"] == 200
        assert sent.messages[1] == {
            "type": "pounce.response.sendfile",
            "path": static_dir / "big.bin",
            "offset": 0,
            "count": self.LARGE_SIZE,
            "more_body": False,
        }

    @pytest.mark.asyncio
    async def test_small_file_skips_sendfile_intent(self, handler, static_dir):
        """A body below the minimum-size gate falls through to read()+write().

        Even with sendfile advertised, a tiny file (13 bytes here) must NOT
        emit ``pounce.response.sendfile``; the per-response transport detach
        cost is not justified below the threshold (issue #127). Byte and
        Content-Length accounting must stay correct on the fallback path.
        """
        sent = _SentMessages()
        scope = self._scope(path="/static/hello.txt", sendfile_enabled=True)
        await handler(scope, None, sent)

        assert sent.messages[0]["status"] == 200
        content_length = dict(sent.messages[0]["headers"]).get(b"content-length")
        assert content_length == b"13"
        assert all(m["type"] != "pounce.response.sendfile" for m in sent.messages)
        body_msgs = [m for m in sent.messages if m["type"] == "http.response.body"]
        total_body = b"".join(m["body"] for m in body_msgs)
        assert total_body == b"Hello, World!"

    @pytest.mark.asyncio
    async def test_fallback_without_sendfile(self, handler, static_dir):
        """Without sendfile support, file is sent via chunked ASGI send."""
        sent = _SentMessages()
        scope = self._scope()
        await handler(scope, None, sent)

        assert sent.messages[0]["status"] == 200
        # Body sent via ASGI (non-empty)
        body_msgs = [m for m in sent.messages if m["type"] == "http.response.body"]
        total_body = b"".join(m["body"] for m in body_msgs)
        assert total_body == b"Hello, World!"

    @pytest.mark.asyncio
    async def test_sendfile_intent_used_for_range_request(self, handler, static_dir):
        """Large range requests emit sendfile intents when support is advertised."""
        sent = _SentMessages()
        scope = self._scope(path="/static/big.bin", sendfile_enabled=True)
        # Request a range comfortably above the minimum-size gate.
        last = self.LARGE_SIZE - 1
        scope["headers"] = [(b"range", f"bytes=0-{last}".encode())]
        await handler(scope, None, sent)

        assert sent.messages[0]["status"] == 206
        assert sent.messages[1]["type"] == "pounce.response.sendfile"
        assert sent.messages[1]["offset"] == 0
        assert sent.messages[1]["count"] == self.LARGE_SIZE

    @pytest.mark.asyncio
    async def test_small_range_request_skips_sendfile_intent(self, handler, static_dir):
        """A tiny range falls through to read()+write() with correct bytes.

        The gate keys on the bytes actually transferred, so a 5-byte range of
        a large file skips sendfile and the partial body still matches.
        """
        sent = _SentMessages()
        scope = self._scope(path="/static/big.bin", sendfile_enabled=True)
        scope["headers"] = [(b"range", b"bytes=0-4")]
        await handler(scope, None, sent)

        assert sent.messages[0]["status"] == 206
        content_length = dict(sent.messages[0]["headers"]).get(b"content-length")
        assert content_length == b"5"
        assert all(m["type"] != "pounce.response.sendfile" for m in sent.messages)
        body_msgs = [m for m in sent.messages if m["type"] == "http.response.body"]
        total_body = b"".join(m["body"] for m in body_msgs)
        assert total_body == b"\xa5" * 5

    @pytest.mark.asyncio
    async def test_head_does_not_use_sendfile(self, handler):
        """HEAD requests never call sendfile — no body to transfer."""
        sent = _SentMessages()
        scope = self._scope(method="HEAD", sendfile_enabled=True)
        await handler(scope, None, sent)

        assert sent.messages[0]["status"] == 200
        assert all(message["type"] != "pounce.response.sendfile" for message in sent.messages)

    @pytest.mark.asyncio
    async def test_304_does_not_use_sendfile(self, handler, static_dir):
        """304 Not Modified skips sendfile entirely."""
        # First request to get the ETag
        sent1 = _SentMessages()
        await handler(self._scope(), None, sent1)
        etag = None
        for h_name, h_value in sent1.messages[0]["headers"]:
            if h_name == b"etag":
                etag = h_value
                break

        # Second request with If-None-Match
        sent2 = _SentMessages()
        scope = self._scope(sendfile_enabled=True)
        scope["headers"] = [(b"if-none-match", etag)]
        await handler(scope, None, sent2)

        assert sent2.messages[0]["status"] == 304
        assert all(message["type"] != "pounce.response.sendfile" for message in sent2.messages)
