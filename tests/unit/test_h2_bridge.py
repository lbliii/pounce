"""Tests for pounce.asgi.h2_bridge — HTTP/2 scope, receive, and send.

Covers build_h2_scope (existing), create_h2_receive, create_h2_send
(stream multiplexing, flow control, compression, error handling).
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

from pounce._compression import GzipCompressor
from pounce.asgi.bridge import SendState
from pounce.asgi.h2_bridge import build_h2_scope, create_h2_receive, create_h2_send
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CLIENT = ("127.0.0.1", 5000)
_SERVER = ("0.0.0.0", 8000)


def _request(
    *,
    method: bytes = b"GET",
    target: bytes = b"/",
    headers: tuple[tuple[bytes, bytes], ...] = (),
) -> RequestReceived:
    base_headers = ((b"host", b"localhost"),)
    return RequestReceived(
        method=method,
        target=target,
        headers=base_headers + headers,
        http_version="2",
    )


class _MockH2Connection:
    """Fake H2Connection that records send calls and simulates flow control."""

    def __init__(self, *, flow_control_window: int = 65535) -> None:
        self.sent_headers: list[tuple[int, int, list[tuple[bytes, bytes]]]] = []
        self.sent_data: list[tuple[int, bytes, bool]] = []
        self._flow_window = flow_control_window
        self._pending_data = bytearray()

    def send_response_headers(
        self, stream_id: int, status: int, headers: list[tuple[bytes, bytes]]
    ) -> None:
        self.sent_headers.append((stream_id, status, headers))

    def send_data(self, stream_id: int, data: bytes, end_stream: bool = False) -> None:
        self.sent_data.append((stream_id, data, end_stream))
        self._flow_window -= len(data)

    def local_flow_control_window(self, stream_id: int) -> int:
        return self._flow_window

    @property
    def max_outbound_frame_size(self) -> int:
        return 16384

    def data_to_send(self) -> bytes:
        out = bytes(self._pending_data)
        self._pending_data.clear()
        return out


class _MockWriter:
    """Fake asyncio.StreamWriter."""

    def __init__(self) -> None:
        self.written = bytearray()
        self.transport = MagicMock()
        self.transport.get_write_buffer_size.return_value = 0

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        pass


# ---------------------------------------------------------------------------
# build_h2_scope — existing tests
# ---------------------------------------------------------------------------


class TestBuildH2Scope:
    """build_h2_scope() produces a valid ASGI HTTP scope for H2."""

    def test_http_version_is_2(self) -> None:
        scope = build_h2_scope(_request(), ServerConfig(), _CLIENT, _SERVER)
        assert scope["http_version"] == "2"

    def test_scheme_https_when_tls(self) -> None:
        config = ServerConfig(ssl_certfile="cert.pem", ssl_keyfile="key.pem")
        scope = build_h2_scope(_request(), config, _CLIENT, _SERVER)
        assert scope["scheme"] == "https"

    def test_scheme_http_when_no_tls(self) -> None:
        scope = build_h2_scope(_request(), ServerConfig(), _CLIENT, _SERVER)
        assert scope["scheme"] == "http"

    def test_applies_proxy_headers_from_trusted_peer(self) -> None:
        """Proxy headers are applied for H2 just like H1."""
        request = _request(
            headers=(
                (b"x-forwarded-for", b"203.0.113.50"),
                (b"x-forwarded-proto", b"https"),
            ),
        )
        config = ServerConfig(trusted_hosts=("10.0.0.1",))
        scope = build_h2_scope(request, config, ("10.0.0.1", 5000), _SERVER)
        assert scope["client"] == ("203.0.113.50", 5000)
        assert scope["scheme"] == "https"

    def test_strips_proxy_headers_from_untrusted_peer(self) -> None:
        """Untrusted peers get X-Forwarded-* stripped."""
        request = _request(
            headers=((b"x-forwarded-for", b"evil"),),
        )
        config = ServerConfig(trusted_hosts=("10.0.0.1",))
        scope = build_h2_scope(request, config, ("192.168.1.1", 5000), _SERVER)
        assert scope["client"] == ("192.168.1.1", 5000)
        header_names = [h[0] for h in scope["headers"]]
        assert b"x-forwarded-for" not in header_names

    def test_no_trusted_hosts_strips_forwarded(self) -> None:
        """When trusted_hosts is empty, forwarded headers are stripped."""
        request = _request(
            headers=((b"x-forwarded-for", b"1.2.3.4"),),
        )
        scope = build_h2_scope(request, ServerConfig(), ("10.0.0.1", 5000), _SERVER)
        header_names = [h[0] for h in scope["headers"]]
        assert b"x-forwarded-for" not in header_names

    def test_path_and_query_string(self) -> None:
        request = _request(target=b"/api/users?page=2")
        scope = build_h2_scope(request, ServerConfig(), _CLIENT, _SERVER)
        assert scope["path"] == "/api/users"
        assert scope["query_string"] == b"page=2"

    def test_method_preserved(self) -> None:
        request = _request(method=b"POST")
        scope = build_h2_scope(request, ServerConfig(), _CLIENT, _SERVER)
        assert scope["method"] == "POST"

    def test_root_path(self) -> None:
        config = ServerConfig(root_path="/prefix")
        scope = build_h2_scope(_request(), config, _CLIENT, _SERVER)
        assert scope["root_path"] == "/prefix"

    def test_headers_lowered(self) -> None:
        request = _request(headers=((b"Content-Type", b"text/html"),))
        scope = build_h2_scope(request, ServerConfig(), _CLIENT, _SERVER)
        header_names = [h[0] for h in scope["headers"]]
        assert b"content-type" in header_names

    def test_state_injected(self) -> None:
        state = {"tenant_registry": object()}
        scope = build_h2_scope(_request(), ServerConfig(), _CLIENT, _SERVER, state=state)
        assert scope["state"] is state


# ---------------------------------------------------------------------------
# create_h2_receive
# ---------------------------------------------------------------------------


class TestCreateH2Receive:
    """Tests for HTTP/2 ASGI receive callable."""

    async def test_receive_returns_queued_message(self) -> None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        receive = create_h2_receive(q)
        msg = {"type": "http.request", "body": b"data", "more_body": False}
        await q.put(msg)
        result = await receive()
        assert result == msg

    async def test_receive_multiple_body_chunks(self) -> None:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        receive = create_h2_receive(q)
        await q.put({"type": "http.request", "body": b"a", "more_body": True})
        await q.put({"type": "http.request", "body": b"b", "more_body": False})
        msg1 = await receive()
        msg2 = await receive()
        assert msg1["body"] == b"a"
        assert msg2["more_body"] is False


# ---------------------------------------------------------------------------
# create_h2_send — new tests
# ---------------------------------------------------------------------------


class TestCreateH2Send:
    """Tests for HTTP/2 ASGI send callable."""

    async def test_response_start_sends_headers(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(h2, stream_id=1, writer=writer, state=state)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )

        assert len(h2.sent_headers) == 1
        sid, status, _headers = h2.sent_headers[0]
        assert sid == 1
        assert status == 200
        assert state.status == 200
        assert state.response_started is True

    async def test_response_body_sends_data(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(h2, stream_id=1, writer=writer, state=state)

        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": b"hello",
                "more_body": False,
            }
        )

        assert len(h2.sent_data) == 1
        sid, data, end = h2.sent_data[0]
        assert sid == 1
        assert data == b"hello"
        assert end is True
        assert state.bytes_sent == 5

    async def test_streaming_body(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(h2, stream_id=1, writer=writer, state=state)

        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        await send({"type": "http.response.body", "body": b"b", "more_body": False})

        assert len(h2.sent_data) == 2
        assert h2.sent_data[0][2] is False  # not end_stream
        assert h2.sent_data[1][2] is True  # end_stream

    async def test_103_early_hints(self) -> None:
        """103 doesn't mark response_started."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(h2, stream_id=1, writer=writer, state=state)

        await send(
            {
                "type": "http.response.start",
                "status": 103,
                "headers": [(b"link", b"</css>; rel=preload")],
            }
        )

        assert len(h2.sent_headers) == 1
        assert state.response_started is False

    async def test_body_before_start_raises(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(h2, stream_id=1, writer=writer, state=state)

        with __import__("pytest").raises(RuntimeError, match="before http.response.start"):
            await send({"type": "http.response.body", "body": b"oops"})

    async def test_body_after_complete_raises(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(h2, stream_id=1, writer=writer, state=state)

        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"done", "more_body": False})

        with __import__("pytest").raises(RuntimeError, match="after response is complete"):
            await send({"type": "http.response.body", "body": b"extra"})

    async def test_request_id_injected(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(h2, stream_id=1, writer=writer, state=state, request_id="req-42")

        await send({"type": "http.response.start", "status": 200, "headers": []})

        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict.get(b"x-request-id") == b"req-42"

    async def test_compression_applied(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        compressor = MagicMock()
        compressor.encoding = "gzip"
        compressor.compress.return_value = b"compressed"
        compressor.flush.return_value = b"-end"

        send = create_h2_send(h2, stream_id=1, writer=writer, state=state, compressor=compressor)

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

        _, data, _ = h2.sent_data[0]
        assert data == b"compressed-end"

        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict.get(b"content-encoding") == b"gzip"

    async def test_compression_skipped_for_already_encoded_response(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        compressor = MagicMock()
        compressor.encoding = "gzip"

        send = create_h2_send(h2, stream_id=1, writer=writer, state=state, compressor=compressor)

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

        _, data, _ = h2.sent_data[0]
        assert data == b"hello"
        compressor.compress.assert_not_called()
        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict[b"content-encoding"] == b"br"
        assert header_dict[b"content-length"] == b"5"

    async def test_compression_disabled_for_304(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        compressor = MagicMock()
        compressor.encoding = "gzip"

        send = create_h2_send(h2, stream_id=1, writer=writer, state=state, compressor=compressor)

        await send({"type": "http.response.start", "status": 304, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

        compressor.compress.assert_not_called()

    async def test_compression_disabled_for_head(self) -> None:
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        compressor = MagicMock()
        compressor.encoding = "gzip"

        send = create_h2_send(
            h2,
            stream_id=1,
            writer=writer,
            state=state,
            compressor=compressor,
            request_method=b"HEAD",
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"100")],
            }
        )
        await send({"type": "http.response.body", "body": b"", "more_body": False})

        compressor.compress.assert_not_called()

    async def test_head_drops_app_body(self) -> None:
        """HEAD must not send app body bytes even when Content-Length is set."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()

        send = create_h2_send(
            h2,
            stream_id=1,
            writer=writer,
            state=state,
            request_method=b"HEAD",
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", b"100")],
            }
        )
        await send({"type": "http.response.body", "body": b"x" * 100, "more_body": False})

        assert len(h2.sent_data) == 1
        _, data, end_stream = h2.sent_data[0]
        assert data == b""
        assert end_stream is True
        assert state.bytes_sent == 0

    async def test_below_threshold_single_shot_not_compressed(self) -> None:
        """Sub-threshold single-shot body is sent uncompressed (issue #123)."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        compressor = GzipCompressor()

        send = create_h2_send(
            h2,
            stream_id=1,
            writer=writer,
            state=state,
            compressor=compressor,
            compression_min_size=500,
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"tiny", "more_body": False})

        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert b"content-encoding" not in header_dict
        _, data, _ = h2.sent_data[0]
        assert data == b"tiny"

    async def test_above_threshold_single_shot_compressed(self) -> None:
        """At/above-threshold single-shot body is compressed (issue #123)."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        compressor = GzipCompressor()

        send = create_h2_send(
            h2,
            stream_id=1,
            writer=writer,
            state=state,
            compressor=compressor,
            compression_min_size=500,
        )

        body = b"x" * 600
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})

        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict.get(b"content-encoding") == b"gzip"

    async def test_app_content_length_below_threshold_not_compressed(self) -> None:
        """App-supplied Content-Length below threshold disables compression."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        compressor = GzipCompressor()

        send = create_h2_send(
            h2,
            stream_id=1,
            writer=writer,
            state=state,
            compressor=compressor,
            compression_min_size=500,
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", b"4")],
            }
        )
        await send({"type": "http.response.body", "body": b"tiny", "more_body": False})

        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert b"content-encoding" not in header_dict

    async def test_streaming_unknown_size_compressed(self) -> None:
        """Streaming responses of unknown size still compress (issue #123)."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        compressor = GzipCompressor()

        send = create_h2_send(
            h2,
            stream_id=1,
            writer=writer,
            state=state,
            compressor=compressor,
            compression_min_size=500,
        )

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"a", "more_body": True})
        await send({"type": "http.response.body", "body": b"b", "more_body": False})

        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict.get(b"content-encoding") == b"gzip"

    async def test_empty_body_end_stream(self) -> None:
        """Empty body with end_stream sends zero-length DATA frame."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(h2, stream_id=1, writer=writer, state=state)

        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

        # Should have sent zero-length data with end_stream
        found_end = any(end for _, _, end in h2.sent_data)
        assert found_end

    async def test_alt_svc_header_injected_when_h3_enabled(self) -> None:
        """Alt-Svc header added when HTTP/3 is enabled."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        config = ServerConfig(
            http3_enabled=True,
            ssl_certfile="cert.pem",
            ssl_keyfile="key.pem",
        )
        send = create_h2_send(
            h2,
            stream_id=1,
            writer=writer,
            state=state,
            config=config,
            server=("0.0.0.0", 8443),
        )

        await send({"type": "http.response.start", "status": 200, "headers": []})

        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert b"alt-svc" in header_dict
        assert b"h3=" in header_dict[b"alt-svc"]

    async def test_no_alt_svc_when_h3_disabled(self) -> None:
        """No Alt-Svc header when HTTP/3 is not enabled."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(
            h2,
            stream_id=1,
            writer=writer,
            state=state,
            config=ServerConfig(),
            server=_SERVER,
        )

        await send({"type": "http.response.start", "status": 200, "headers": []})

        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert b"alt-svc" not in header_dict

    async def test_multiple_streams_independent(self) -> None:
        """Two send callables on different streams don't interfere."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state1 = SendState()
        state2 = SendState()
        send1 = create_h2_send(h2, stream_id=1, writer=writer, state=state1)
        send2 = create_h2_send(h2, stream_id=3, writer=writer, state=state2)

        await send1({"type": "http.response.start", "status": 200, "headers": []})
        await send2({"type": "http.response.start", "status": 404, "headers": []})
        await send1({"type": "http.response.body", "body": b"ok", "more_body": False})
        await send2({"type": "http.response.body", "body": b"nope", "more_body": False})

        assert state1.status == 200
        assert state2.status == 404
        assert state1.bytes_sent == 2
        assert state2.bytes_sent == 4

        # Verify correct stream IDs
        stream_1_data = [(s, d, e) for s, d, e in h2.sent_data if s == 1]
        stream_3_data = [(s, d, e) for s, d, e in h2.sent_data if s == 3]
        assert len(stream_1_data) == 1
        assert len(stream_3_data) == 1
        assert stream_1_data[0][1] == b"ok"
        assert stream_3_data[0][1] == b"nope"

    async def test_string_headers_encoded(self) -> None:
        """String header names/values are encoded to bytes."""
        h2 = _MockH2Connection()
        writer = _MockWriter()
        state = SendState()
        send = create_h2_send(h2, stream_id=1, writer=writer, state=state)

        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [("x-custom", "value")],
            }
        )

        _, _, headers = h2.sent_headers[0]
        header_dict = dict(headers)
        assert header_dict.get(b"x-custom") == b"value"


class TestPriorityGating:
    """create_h2_send respects a PriorityScheduler when one is passed.

    RFC 9218: higher-urgency streams preempt lower-urgency ones, and
    incremental streams interleave via mark_wrote() in the scheduler.
    """

    async def test_higher_urgency_writes_before_lower(self) -> None:
        from pounce._priority import PriorityScheduler, StreamPriority

        h2 = _MockH2Connection()
        writer = _MockWriter()
        scheduler = PriorityScheduler()

        scheduler.set_priority(1, StreamPriority(urgency=5))
        scheduler.set_priority(3, StreamPriority(urgency=1))

        state1 = SendState()
        state3 = SendState()
        send1 = create_h2_send(h2, 1, writer, state1, scheduler=scheduler)
        send3 = create_h2_send(h2, 3, writer, state3, scheduler=scheduler)

        # start both responses
        await send1(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send3(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )

        # Pre-schedule both streams so they compete from the start.
        # Without this, task1 would `await_turn(1)` alone-at-head and race
        # through its synchronous write before task3 ever ran.
        scheduler.schedule(1)
        scheduler.schedule(3)

        async def write_body(send_fn, body: bytes) -> None:
            await send_fn({"type": "http.response.body", "body": body})

        task1 = asyncio.create_task(write_body(send1, b"low-prio"))
        task3 = asyncio.create_task(write_body(send3, b"high-prio"))

        await asyncio.gather(task1, task3)

        # Find first non-empty DATA frame
        data_frames = [(sid, data) for sid, data, _ in h2.sent_data if data]
        assert data_frames  # sanity
        first_stream = data_frames[0][0]
        assert first_stream == 3, (
            f"expected urgency-1 stream 3 to write first, got stream {first_stream}"
        )
