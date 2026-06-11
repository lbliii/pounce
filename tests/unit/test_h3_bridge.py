"""Tests for pounce.asgi.h3_bridge — HTTP/3 scope building, receive, and send.

Sprint 3 coverage: scope building from pseudo-headers, receive queue,
send callable (headers, body, compression, error handling).
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from pounce.asgi.bridge import SendState
from pounce.asgi.h3_bridge import (
    H3PseudoHeaderError,
    build_h3_scope,
    create_h3_receive,
    create_h3_send,
)
from pounce.config import ServerConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLIENT = ("203.0.113.50", 12345)
_SERVER = ("0.0.0.0", 443)


def _h3_headers(
    method: str = "GET",
    path: str = "/",
    scheme: str = "https",
    authority: str = "example.com",
    extra: list[tuple[bytes, bytes]] | None = None,
) -> list[tuple[bytes, bytes]]:
    """Build HTTP/3 pseudo-headers + regular headers."""
    headers: list[tuple[bytes, bytes]] = [
        (b":method", method.encode()),
        (b":path", path.encode()),
        (b":scheme", scheme.encode()),
        (b":authority", authority.encode()),
    ]
    if extra:
        headers.extend(extra)
    return headers


class _MockH3Connection:
    """Fake zoomies H3Connection that records calls."""

    def __init__(self) -> None:
        self.sent_headers: list[tuple[int, list[tuple[bytes, bytes]]]] = []
        self.sent_data: list[tuple[int, bytes, bool]] = []

    def send_headers(self, *, stream_id: int, headers: list[tuple[bytes, bytes]]) -> None:
        self.sent_headers.append((stream_id, headers))

    def send_data(self, *, stream_id: int, data: bytes, end_stream: bool = False) -> None:
        self.sent_data.append((stream_id, data, end_stream))


# ---------------------------------------------------------------------------
# build_h3_scope
# ---------------------------------------------------------------------------


class TestBuildH3Scope:
    """Tests for HTTP/3 ASGI scope construction."""

    def test_basic_get(self) -> None:
        scope = build_h3_scope(_h3_headers(), ServerConfig(), _CLIENT, _SERVER)
        assert scope["type"] == "http"
        assert scope["http_version"] == "3"
        assert scope["method"] == "GET"
        assert scope["path"] == "/"
        assert scope["scheme"] == "https"
        assert scope["client"] == _CLIENT
        assert scope["server"] == _SERVER

    def test_method_extraction(self) -> None:
        scope = build_h3_scope(_h3_headers(method="POST"), ServerConfig(), _CLIENT, _SERVER)
        assert scope["method"] == "POST"

    def test_path_with_query_string(self) -> None:
        scope = build_h3_scope(
            _h3_headers(path="/api/users?page=2&limit=10"),
            ServerConfig(),
            _CLIENT,
            _SERVER,
        )
        assert scope["path"] == "/api/users"
        assert scope["query_string"] == b"page=2&limit=10"
        assert scope["raw_path"] == b"/api/users"

    def test_path_without_query_string(self) -> None:
        scope = build_h3_scope(_h3_headers(path="/hello"), ServerConfig(), _CLIENT, _SERVER)
        assert scope["path"] == "/hello"
        assert scope["query_string"] == b""
        assert scope["raw_path"] == b"/hello"

    def test_percent_encoded_path_decoded(self) -> None:
        """Path is unquoted; raw_path preserves original percent-encoded bytes."""
        scope = build_h3_scope(_h3_headers(path="/hello%20world"), ServerConfig(), _CLIENT, _SERVER)
        assert scope["path"] == "/hello world"
        # ASGI spec: raw_path is the original undecoded bytes
        assert scope["raw_path"] == b"/hello%20world"

    def test_percent_encoded_path_with_query_preserves_raw(self) -> None:
        """With query string, raw_path preserves percent-encoding."""
        scope = build_h3_scope(
            _h3_headers(path="/hello%20world?q=1"), ServerConfig(), _CLIENT, _SERVER
        )
        assert scope["path"] == "/hello world"
        assert scope["raw_path"] == b"/hello%20world"
        assert scope["query_string"] == b"q=1"

    def test_scheme_always_from_pseudo_header(self) -> None:
        """HTTP/3 scheme comes from :scheme pseudo-header."""
        scope = build_h3_scope(_h3_headers(scheme="https"), ServerConfig(), _CLIENT, _SERVER)
        assert scope["scheme"] == "https"

    def test_regular_headers_preserved(self) -> None:
        """Non-pseudo headers pass through to scope headers."""
        extra = [
            (b"content-type", b"application/json"),
            (b"x-custom", b"value"),
        ]
        scope = build_h3_scope(_h3_headers(extra=extra), ServerConfig(), _CLIENT, _SERVER)
        header_names = [h[0] for h in scope["headers"]]
        assert b"content-type" in header_names
        assert b"x-custom" in header_names

    def test_pseudo_headers_not_in_scope_headers(self) -> None:
        """Pseudo-headers (:method, :path, etc.) are parsed, not forwarded."""
        scope = build_h3_scope(_h3_headers(), ServerConfig(), _CLIENT, _SERVER)
        header_names = [h[0] for h in scope["headers"]]
        assert b":method" not in header_names
        assert b":path" not in header_names
        assert b":scheme" not in header_names
        assert b":authority" not in header_names

    def test_missing_required_pseudo_header_rejected(self) -> None:
        headers = [
            (b":method", b"GET"),
            (b":path", b"/"),
            (b":authority", b"example.com"),
        ]
        with pytest.raises(H3PseudoHeaderError, match="missing required"):
            build_h3_scope(headers, ServerConfig(), _CLIENT, _SERVER)

    def test_duplicate_pseudo_header_rejected(self) -> None:
        headers = _h3_headers()
        headers.append((b":path", b"/other"))
        with pytest.raises(H3PseudoHeaderError, match="duplicate"):
            build_h3_scope(headers, ServerConfig(), _CLIENT, _SERVER)

    def test_authority_host_conflict_rejected(self) -> None:
        headers = _h3_headers(extra=[(b"host", b"other.example")])
        with pytest.raises(H3PseudoHeaderError, match="host does not match"):
            build_h3_scope(headers, ServerConfig(), _CLIENT, _SERVER)

    def test_stream_id_in_extensions(self) -> None:
        scope = build_h3_scope(_h3_headers(), ServerConfig(), _CLIENT, _SERVER, stream_id=4)
        assert scope["extensions"]["pounce.h3.stream_id"] == 4

    def test_0rtt_flag_in_extensions(self) -> None:
        scope = build_h3_scope(_h3_headers(), ServerConfig(), _CLIENT, _SERVER, is_0rtt=True)
        assert scope["extensions"]["pounce.h3.is_0rtt"] is True

    def test_does_not_advertise_response_push(self) -> None:
        """H3 must not advertise http.response.push: server push is unimplemented.

        Extension honesty (src/pounce/asgi/AGENTS.md): advertise an extension only
        when the runtime can execute it. This matches the H2 bridge, which does not
        advertise push either.
        """
        scope = build_h3_scope(_h3_headers(), ServerConfig(), _CLIENT, _SERVER)
        assert "http.response.push" not in scope["extensions"]

    def test_root_path_from_config(self) -> None:
        config = ServerConfig(root_path="/prefix")
        scope = build_h3_scope(_h3_headers(), config, _CLIENT, _SERVER)
        assert scope["root_path"] == "/prefix"

    def test_asgi_version(self) -> None:
        scope = build_h3_scope(_h3_headers(), ServerConfig(), _CLIENT, _SERVER)
        assert scope["asgi"]["version"] == "3.0"

    def test_proxy_headers_trusted(self) -> None:
        """X-Forwarded-For applied when client is trusted."""
        extra = [(b"x-forwarded-for", b"10.0.0.99")]
        config = ServerConfig(trusted_hosts=("203.0.113.50",))
        scope = build_h3_scope(_h3_headers(extra=extra), config, _CLIENT, _SERVER)
        assert scope["client"] == ("10.0.0.99", 12345)

    def test_proxy_headers_untrusted(self) -> None:
        """X-Forwarded-For stripped when client is untrusted."""
        extra = [(b"x-forwarded-for", b"evil")]
        config = ServerConfig(trusted_hosts=("10.0.0.1",))
        scope = build_h3_scope(_h3_headers(extra=extra), config, _CLIENT, _SERVER)
        assert scope["client"] == _CLIENT
        header_names = [h[0] for h in scope["headers"]]
        assert b"x-forwarded-for" not in header_names

    def test_header_names_lowered(self) -> None:
        """Regular header names are lowered in scope."""
        extra = [(b"Content-Type", b"text/html")]
        scope = build_h3_scope(_h3_headers(extra=extra), ServerConfig(), _CLIENT, _SERVER)
        header_names = [h[0] for h in scope["headers"]]
        assert b"content-type" in header_names
        assert b"Content-Type" not in header_names

    def test_state_injected(self) -> None:
        state = {"tenant_registry": object()}
        scope = build_h3_scope(_h3_headers(), ServerConfig(), _CLIENT, _SERVER, state=state)
        assert scope["state"] is state


# ---------------------------------------------------------------------------
# create_h3_receive
# ---------------------------------------------------------------------------


class TestCreateH3Receive:
    """Tests for HTTP/3 ASGI receive callable."""

    async def test_receive_returns_queued_message(self) -> None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        receive = create_h3_receive(q)
        msg = {"type": "http.request", "body": b"hello", "more_body": False}
        await q.put(msg)
        result = await receive()
        assert result == msg

    async def test_receive_waits_for_message(self) -> None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        receive = create_h3_receive(q)

        async def put_later() -> None:
            await asyncio.sleep(0.01)
            await q.put({"type": "http.request", "body": b"", "more_body": False})

        task = asyncio.create_task(put_later())
        result = await receive()
        assert result["type"] == "http.request"
        await task

    async def test_receive_multiple_messages(self) -> None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        receive = create_h3_receive(q)
        await q.put({"type": "http.request", "body": b"part1", "more_body": True})
        await q.put({"type": "http.request", "body": b"part2", "more_body": False})
        msg1 = await receive()
        msg2 = await receive()
        assert msg1["body"] == b"part1"
        assert msg1["more_body"] is True
        assert msg2["body"] == b"part2"
        assert msg2["more_body"] is False


# ---------------------------------------------------------------------------
# create_h3_send
# ---------------------------------------------------------------------------


class TestCreateH3Send:
    """Tests for HTTP/3 ASGI send callable."""

    async def test_response_start_sends_headers(self) -> None:
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=0, transmit=transmit, state=state)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )

        assert len(h3.sent_headers) == 1
        stream_id, headers = h3.sent_headers[0]
        assert stream_id == 0
        status_header = dict(headers).get(b":status")
        assert status_header == b"200"
        assert state.status == 200
        assert state.response_started is True
        transmit.assert_called_once()

    async def test_response_body_sends_data(self) -> None:
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=4, transmit=transmit, state=state)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"hello",
                "more_body": False,
            }
        )

        assert len(h3.sent_data) == 1
        sid, data, end = h3.sent_data[0]
        assert sid == 4
        assert data == b"hello"
        assert end is True
        assert state.bytes_sent == 5

    async def test_streaming_body(self) -> None:
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=0, transmit=transmit, state=state)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"chunk1",
                "more_body": True,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"chunk2",
                "more_body": False,
            }
        )

        assert len(h3.sent_data) == 2
        assert h3.sent_data[0] == (0, b"chunk1", False)
        assert h3.sent_data[1] == (0, b"chunk2", True)
        assert state.bytes_sent == 12

    async def test_103_early_hints(self) -> None:
        """103 Early Hints sends headers but doesn't mark response_started."""
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=0, transmit=transmit, state=state)

        await send(
            {
                "type": "http.response.start",
                "status": 103,
                "headers": [(b"link", b'</style.css>; rel="preload"')],
            }
        )

        assert len(h3.sent_headers) == 1
        assert state.response_started is False
        assert state.status == 0

    async def test_request_id_injected(self) -> None:
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=0, transmit=transmit, state=state, request_id="abc-123")

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [],
            }
        )

        _, headers = h3.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict.get(b"x-request-id") == b"abc-123"

    async def test_body_before_start_raises(self) -> None:
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=0, transmit=transmit, state=state)

        with __import__("pytest").raises(RuntimeError, match="before http.response.start"):
            await send(
                {
                    "type": "http.response.body",
                    "body": b"oops",
                }
            )

    async def test_body_after_complete_raises(self) -> None:
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=0, transmit=transmit, state=state)

        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done", "more_body": False})

        with __import__("pytest").raises(RuntimeError, match="after response is complete"):
            await send({"type": "http.response.body", "body": b"extra"})

    async def test_compression_applied(self) -> None:
        """Compressor compresses body data."""
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()

        compressor = MagicMock()
        compressor.encoding = "gzip"
        compressor.compress.return_value = b"compressed"
        compressor.flush.return_value = b"-end"

        send = create_h3_send(
            h3, stream_id=0, transmit=transmit, state=state, compressor=compressor
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"hello",
                "more_body": False,
            }
        )

        # Body should be compressed
        _, data, _ = h3.sent_data[0]
        assert data == b"compressed-end"
        compressor.compress.assert_called_once_with(b"hello")
        compressor.flush.assert_called_once()

        # Headers should include content-encoding
        _, headers = h3.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict.get(b"content-encoding") == b"gzip"

    async def test_compression_skipped_for_already_encoded_response(self) -> None:
        """Responses with Content-Encoding are not double-compressed."""
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()

        compressor = MagicMock()
        compressor.encoding = "gzip"

        send = create_h3_send(
            h3, stream_id=0, transmit=transmit, state=state, compressor=compressor
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-encoding", b"br"),
                    (b"content-length", b"5"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"hello", "more_body": False})

        _, data, _ = h3.sent_data[0]
        assert data == b"hello"
        compressor.compress.assert_not_called()
        _, headers = h3.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict[b"content-encoding"] == b"br"
        assert header_dict[b"content-length"] == b"5"

    async def test_compression_disabled_for_204(self) -> None:
        """Compressor disabled for 204 No Content."""
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()

        compressor = MagicMock()
        compressor.encoding = "gzip"

        send = create_h3_send(
            h3, stream_id=0, transmit=transmit, state=state, compressor=compressor
        )

        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"",
                "more_body": False,
            }
        )

        # Compressor should not have been called
        compressor.compress.assert_not_called()

    async def test_compression_disabled_for_head(self) -> None:
        """Compressor disabled for HEAD requests."""
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()

        compressor = MagicMock()
        compressor.encoding = "gzip"

        send = create_h3_send(
            h3,
            stream_id=0,
            transmit=transmit,
            state=state,
            compressor=compressor,
            request_method="HEAD",
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"100")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"",
                "more_body": False,
            }
        )

        compressor.compress.assert_not_called()

    async def test_compression_disabled_for_sse(self) -> None:
        """Compressor disabled for text/event-stream (SSE)."""
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()

        compressor = MagicMock()
        compressor.encoding = "gzip"

        send = create_h3_send(
            h3, stream_id=0, transmit=transmit, state=state, compressor=compressor
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b"data: hello\n\n",
                "more_body": False,
            }
        )

        compressor.compress.assert_not_called()

    async def test_streaming_compression_sync_flush(self) -> None:
        """Streaming chunks use sync_flush, final chunk uses flush."""
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()

        compressor = MagicMock()
        compressor.encoding = "gzip"
        compressor.compress.return_value = b"c"
        compressor.sync_flush.return_value = b"-sf"
        compressor.flush.return_value = b"-f"

        send = create_h3_send(
            h3, stream_id=0, transmit=transmit, state=state, compressor=compressor
        )

        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        await send({"type": "http.response.body", "body": b"b", "more_body": False})

        # First chunk: compress + sync_flush
        assert h3.sent_data[0] == (0, b"c-sf", False)
        # Final chunk: compress + flush
        assert h3.sent_data[1] == (0, b"c-f", True)

    async def test_empty_body_end_stream(self) -> None:
        """Empty body with more_body=False sends end_stream."""
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=0, transmit=transmit, state=state)

        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

        assert len(h3.sent_data) == 1
        _, data, end = h3.sent_data[0]
        assert data == b""
        assert end is True

    async def test_string_headers_encoded(self) -> None:
        """String header names/values are encoded to bytes."""
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=0, transmit=transmit, state=state)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [("x-custom", "value")],
            }
        )

        _, headers = h3.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict.get(b"x-custom") == b"value"

    async def test_unhandled_message_type_raises(self) -> None:
        """Unknown ASGI message types must fail loud (no silent no-op).

        Mirrors the H1 bridge, which raises on unexpected message types rather
        than dropping them silently (e.g. a never-implemented push attempt).
        """
        h3 = _MockH3Connection()
        transmit = MagicMock()
        state = SendState()
        send = create_h3_send(h3, stream_id=0, transmit=transmit, state=state)

        with pytest.raises(RuntimeError, match="Unexpected ASGI message type"):
            await send({"type": "http.response.push", "path": "/asset.js", "headers": []})
