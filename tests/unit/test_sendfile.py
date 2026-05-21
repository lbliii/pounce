"""Tests for zero-copy sendfile support and StaticFiles sendfile integration."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest

from pounce._sendfile import _SENDFILE_CHUNK, can_use_sendfile, create_sendfile_callable
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


class TestCreateSendfileCallable:
    @pytest.mark.asyncio
    async def test_transfers_full_file(self, tmp_path):
        """sendfile callable reads the entire file when count == file size."""
        test_file = tmp_path / "hello.txt"
        test_file.write_bytes(b"Hello, sendfile!")

        writer = _mock_writer(fd=42)
        # Track os.sendfile calls
        calls: list[tuple] = []

        def fake_sendfile(out_fd, in_fd, offset, count):
            calls.append((out_fd, in_fd, offset, count))
            return count  # pretend all bytes sent

        with patch("os.sendfile", side_effect=fake_sendfile):
            fn = create_sendfile_callable(writer)
            await fn(test_file, 0, 15)

        assert len(calls) == 1
        assert calls[0][0] == 42  # socket fd
        assert calls[0][2] == 0  # offset
        assert calls[0][3] == 15  # count

    @pytest.mark.asyncio
    async def test_transfers_with_offset(self, tmp_path):
        test_file = tmp_path / "data.bin"
        test_file.write_bytes(b"x" * 200)

        writer = _mock_writer(fd=10)
        calls: list[tuple] = []

        def fake_sendfile(out_fd, in_fd, offset, count):
            calls.append((out_fd, in_fd, offset, count))
            return count

        with patch("os.sendfile", side_effect=fake_sendfile):
            fn = create_sendfile_callable(writer)
            await fn(test_file, 50, 100)

        assert calls[0][2] == 50  # offset
        assert calls[0][3] == 100  # count

    @pytest.mark.asyncio
    async def test_chunked_transfer(self, tmp_path):
        """Large transfers are split into _SENDFILE_CHUNK-sized pieces."""
        test_file = tmp_path / "big.bin"
        test_file.write_bytes(b"\x00" * 10)
        total = _SENDFILE_CHUNK * 2 + 500

        writer = _mock_writer()
        calls: list[tuple] = []

        def fake_sendfile(out_fd, in_fd, offset, count):
            calls.append((out_fd, in_fd, offset, count))
            return count

        with patch("os.sendfile", side_effect=fake_sendfile):
            fn = create_sendfile_callable(writer)
            await fn(test_file, 0, total)

        assert len(calls) == 3
        assert calls[0][3] == _SENDFILE_CHUNK
        assert calls[1][3] == _SENDFILE_CHUNK
        assert calls[2][3] == 500

    @pytest.mark.asyncio
    async def test_partial_sends(self, tmp_path):
        """Handles partial sends (os.sendfile returns less than requested)."""
        test_file = tmp_path / "partial.bin"
        test_file.write_bytes(b"\x00" * 10)

        writer = _mock_writer()
        call_count = 0

        def fake_sendfile(out_fd, in_fd, offset, count):
            nonlocal call_count
            call_count += 1
            return min(count, 100)  # only send 100 bytes at a time

        with patch("os.sendfile", side_effect=fake_sendfile):
            fn = create_sendfile_callable(writer)
            await fn(test_file, 0, 250)

        assert call_count == 3  # 100 + 100 + 50

    @pytest.mark.asyncio
    async def test_zero_return_breaks(self, tmp_path):
        """sendfile returns 0 when connection closes — loop must stop."""
        test_file = tmp_path / "eof.bin"
        test_file.write_bytes(b"\x00" * 10)

        writer = _mock_writer()
        call_count = 0

        def fake_sendfile(out_fd, in_fd, offset, count):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return 0
            return min(count, 500)

        with patch("os.sendfile", side_effect=fake_sendfile):
            fn = create_sendfile_callable(writer)
            await fn(test_file, 0, 2000)

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_fd_closed_on_error(self, tmp_path):
        """File descriptor is closed even if sendfile raises."""
        test_file = tmp_path / "err.bin"
        test_file.write_bytes(b"\x00" * 10)

        writer = _mock_writer()
        closed_fds: list[int] = []
        original_close = os.close

        def track_close(fd):
            closed_fds.append(fd)
            original_close(fd)

        def failing_sendfile(out_fd, in_fd, offset, count):
            raise OSError("disk error")

        with (
            patch("os.sendfile", side_effect=failing_sendfile),
            patch("os.close", side_effect=track_close),
        ):
            fn = create_sendfile_callable(writer)
            with pytest.raises(OSError, match="disk error"):
                await fn(test_file, 0, 100)

        assert len(closed_fds) == 1


# ---------------------------------------------------------------------------
# StaticFiles sendfile integration
# ---------------------------------------------------------------------------


class TestStaticFilesSendfile:
    """Tests that StaticFiles emits sendfile intents when the scope advertises support."""

    @pytest.fixture
    def static_dir(self, tmp_path):
        (tmp_path / "hello.txt").write_text("Hello, World!")
        (tmp_path / "index.html").write_text("<h1>Index</h1>")
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
        """When sendfile is advertised, StaticFiles emits a file-range intent."""
        sent = _SentMessages()
        scope = self._scope(sendfile_enabled=True)
        await handler(scope, None, sent)

        assert len(sent.messages) == 2
        assert sent.messages[0]["type"] == "http.response.start"
        assert sent.messages[0]["status"] == 200
        assert sent.messages[1] == {
            "type": "pounce.response.sendfile",
            "path": static_dir / "hello.txt",
            "offset": 0,
            "count": 13,
            "more_body": False,
        }

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
        """Range requests emit sendfile intents when support is advertised."""
        sent = _SentMessages()
        scope = self._scope(sendfile_enabled=True)
        scope["headers"] = [(b"range", b"bytes=0-4")]
        await handler(scope, None, sent)

        assert sent.messages[0]["status"] == 206
        assert sent.messages[1]["type"] == "pounce.response.sendfile"
        assert sent.messages[1]["offset"] == 0
        assert sent.messages[1]["count"] == 5

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
