"""Extended Hypothesis property-based tests for pounce internals.

Covers: _fast_h1, _compression, _headers, protocols.ws, and config validation.
Complements the existing test_protocol_fuzz.py with ~20 additional @given tests.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import string

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from pounce._compression import negotiate_encoding
from pounce._fast_h1 import ParseError, parse_request
from pounce._headers import get_header, is_websocket_upgrade
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived
from pounce.protocols.ws import build_101_response, build_ws_accept_key

# ---------------------------------------------------------------------------
# Shared strategies
# ---------------------------------------------------------------------------

_HTTP_METHODS = st.sampled_from(
    [b"GET", b"HEAD", b"POST", b"PUT", b"DELETE", b"PATCH", b"OPTIONS", b"TRACE", b"CONNECT"]
)

_ASCII_PATH = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/.-_~%+?=&",
    min_size=1,
    max_size=100,
).map(lambda s: s.encode("ascii"))

_HEADER_NAME = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
    min_size=1,
    max_size=40,
).map(lambda s: s.encode("ascii"))

_HEADER_VALUE = st.text(
    alphabet=string.printable.replace("\r", "").replace("\n", "").replace("\x00", ""),
    min_size=0,
    max_size=100,
).map(lambda s: s.encode("ascii", errors="replace"))

_HEADER_PAIR = st.tuples(_HEADER_NAME, _HEADER_VALUE)
_HEADER_LIST = st.lists(_HEADER_PAIR, max_size=15)

_HTTP_VERSION = st.sampled_from([b"HTTP/1.1", b"HTTP/1.0"])


def _make_raw_request(
    method: bytes,
    path: bytes,
    headers: list[tuple[bytes, bytes]],
    version: bytes = b"HTTP/1.1",
    body: bytes = b"",
) -> bytes:
    """Assemble a valid HTTP/1.1 request from components."""
    target = path if path.startswith(b"/") else b"/" + path
    lines = [method + b" " + target + b" " + version]
    has_host = any(n.lower() == b"host" for n, _ in headers)
    if not has_host:
        lines.append(b"Host: localhost")
    for name, value in headers:
        lines.append(name + b": " + value)
    if body:
        lines.append(b"Content-Length: " + str(len(body)).encode())
    lines.append(b"")
    lines.append(b"")
    raw = b"\r\n".join(lines)
    return raw + body


# ===========================================================================
# 1. Fast H1 parser fuzzing
# ===========================================================================


class TestFastH1ParserFuzz:
    """Property-based tests for pounce._fast_h1.parse_request."""

    @given(method=_HTTP_METHODS, path=_ASCII_PATH, headers=_HEADER_LIST, version=_HTTP_VERSION)
    @settings(max_examples=200)
    def test_valid_request_parses_without_crash(
        self,
        method: bytes,
        path: bytes,
        headers: list[tuple[bytes, bytes]],
        version: bytes,
    ) -> None:
        """Well-formed requests never crash; they return a RequestReceived or None."""
        # Filter out headers that would trigger intentional ParseError
        safe_headers = [
            (n, v)
            for n, v in headers
            if n.lower() not in (b"content-length", b"transfer-encoding")
            and b" " not in n
            and b"\x00" not in n
        ]
        raw = _make_raw_request(method, path, safe_headers, version)
        buf = memoryview(bytearray(raw))
        result = parse_request(buf, len(raw))
        req, _body, consumed, _chunked = result
        if req is not None:
            assert isinstance(req, RequestReceived)
            assert req.method == method
            assert consumed > 0

    @given(data=st.binary(min_size=0, max_size=500))
    @settings(max_examples=300)
    def test_arbitrary_bytes_never_crash(self, data: bytes) -> None:
        """Random bytes must raise ParseError or return incomplete, never crash."""
        buf = memoryview(bytearray(data))
        try:
            req, _body, consumed, _chunked = parse_request(buf, len(data))
            # If no exception, result must be None (incomplete) or a valid request
            if req is not None:
                assert isinstance(req, RequestReceived)
                assert consumed > 0
        except ParseError:
            pass  # expected for malformed input

    @given(
        method=_HTTP_METHODS,
        path=_ASCII_PATH,
        cl_value=st.text(alphabet="0123456789abcdefxX.-+ ", min_size=1, max_size=20),
    )
    @settings(max_examples=200)
    def test_content_length_values(self, method: bytes, path: bytes, cl_value: str) -> None:
        """Random Content-Length values: parser must not crash."""
        raw = (
            method + b" /" + path + b" HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: " + cl_value.encode("ascii", errors="replace") + b"\r\n"
            b"\r\n"
        )
        buf = memoryview(bytearray(raw))
        with contextlib.suppress(ParseError):
            parse_request(buf, len(raw))

    @given(
        method=_HTTP_METHODS,
        cl1=st.integers(min_value=0, max_value=100),
        cl2=st.integers(min_value=0, max_value=100),
    )
    def test_duplicate_content_length_rejected(self, method: bytes, cl1: int, cl2: int) -> None:
        """Duplicate Content-Length headers must raise ParseError."""
        raw = (
            method + b" / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: " + str(cl1).encode() + b"\r\n"
            b"Content-Length: " + str(cl2).encode() + b"\r\n"
            b"\r\n"
        )
        buf = memoryview(bytearray(raw))
        with pytest.raises(ParseError, match="Duplicate Content-Length"):
            parse_request(buf, len(raw))

    @given(
        method=_HTTP_METHODS,
        cl=st.integers(min_value=0, max_value=50),
        te=st.sampled_from([b"chunked", b"gzip, chunked", b"chunked, gzip"]),
    )
    def test_content_length_with_transfer_encoding_rejected(
        self, method: bytes, cl: int, te: bytes
    ) -> None:
        """Content-Length + Transfer-Encoding together must raise ParseError (RFC 7230)."""
        raw = (
            method + b" / HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Length: " + str(cl).encode() + b"\r\n"
            b"Transfer-Encoding: " + te + b"\r\n"
            b"\r\n"
        )
        buf = memoryview(bytearray(raw))
        with pytest.raises(ParseError, match="Content-Length with Transfer-Encoding"):
            parse_request(buf, len(raw))

    @given(method=_HTTP_METHODS, path=_ASCII_PATH)
    def test_truncated_request_returns_none(self, method: bytes, path: bytes) -> None:
        """A request without the terminating CRLFCRLF returns None (need more data)."""
        raw = method + b" /" + path + b" HTTP/1.1\r\nHost: localhost\r\n"
        buf = memoryview(bytearray(raw))
        req, _body, consumed, _chunked = parse_request(buf, len(raw))
        assert req is None
        assert consumed == 0

    @given(method=_HTTP_METHODS, path=_ASCII_PATH)
    def test_null_bytes_in_target_rejected(self, method: bytes, path: bytes) -> None:
        """Null bytes in the request target must raise ParseError."""
        target = b"/" + path[:10] + b"\x00" + path[10:]
        raw = method + b" " + target + b" HTTP/1.1\r\nHost: localhost\r\n\r\n"
        buf = memoryview(bytearray(raw))
        with pytest.raises(ParseError, match="Invalid characters"):
            parse_request(buf, len(raw))


# ===========================================================================
# 2. Compression negotiation fuzzing
# ===========================================================================


class TestCompressionNegotiationFuzz:
    """Property-based tests for pounce._compression.negotiate_encoding."""

    @given(header=st.text(min_size=0, max_size=300))
    @settings(max_examples=300)
    def test_negotiate_always_returns_valid_or_none(self, header: str) -> None:
        """negotiate_encoding must return None or a recognized encoding string."""
        result = negotiate_encoding(header)
        assert result is None or result in {"zstd", "gzip"}

    @given(header=st.binary(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_negotiate_bytes_input_never_crashes(self, header: bytes) -> None:
        """Random bytes as Accept-Encoding must not crash."""
        result = negotiate_encoding(header)
        assert result is None or result in {"zstd", "gzip"}

    @given(
        encodings=st.lists(
            st.sampled_from(["gzip", "zstd", "br", "deflate", "identity", "*"]),
            min_size=1,
            max_size=5,
        ),
        q_values=st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            min_size=1,
            max_size=5,
        ),
    )
    def test_qvalue_parsing_with_random_floats(
        self, encodings: list[str], q_values: list[float]
    ) -> None:
        """Random q-values produce valid results."""
        parts = []
        for i, enc in enumerate(encodings):
            q = q_values[i % len(q_values)]
            parts.append(f"{enc};q={q:.3f}")
        header = ", ".join(parts)
        result = negotiate_encoding(header)
        assert result is None or result in {"zstd", "gzip"}

    @given(
        malformed_q=st.text(alphabet="0123456789.abcxyz!@#$%", min_size=0, max_size=10),
    )
    def test_malformed_qvalue_never_crashes(self, malformed_q: str) -> None:
        """Malformed q-values (non-numeric, extra dots) must not crash."""
        header = f"gzip;q={malformed_q}, zstd;q={malformed_q}"
        result = negotiate_encoding(header)
        assert result is None or result in {"zstd", "gzip"}

    @given(
        extra_encodings=st.lists(
            st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10),
            min_size=0,
            max_size=5,
        ),
    )
    def test_wildcard_with_random_encodings(self, extra_encodings: list[str]) -> None:
        """Wildcard (*) handling with random encoding names."""
        parts = [f"{e};q=0.5" for e in extra_encodings]
        parts.append("*;q=0.1")
        header = ", ".join(parts)
        result = negotiate_encoding(header)
        # With wildcard, should match our priority encoding
        assert result is None or result in {"zstd", "gzip"}


# ===========================================================================
# 3. Header utilities fuzzing
# ===========================================================================


class TestHeaderUtilitiesFuzz:
    """Property-based tests for pounce._headers."""

    @given(
        headers=st.lists(
            st.tuples(
                st.sampled_from(
                    [
                        b"Connection",
                        b"connection",
                        b"CONNECTION",
                        b"Upgrade",
                        b"upgrade",
                        b"UPGRADE",
                    ]
                ),
                st.sampled_from(
                    [
                        b"upgrade",
                        b"Upgrade",
                        b"UPGRADE",
                        b"websocket",
                        b"WebSocket",
                        b"WEBSOCKET",
                        b"keep-alive",
                        b"close",
                        b"random",
                    ]
                ),
            ),
            min_size=0,
            max_size=6,
        ),
    )
    def test_is_websocket_upgrade_never_crashes(self, headers: list[tuple[bytes, bytes]]) -> None:
        """is_websocket_upgrade returns a bool, never crashes, for random header combos."""
        request = RequestReceived(
            method=b"GET", target=b"/ws", headers=tuple(headers), http_version="1.1"
        )
        result = is_websocket_upgrade(request)
        assert isinstance(result, bool)

    def test_is_websocket_upgrade_detects_valid(self) -> None:
        """Confirm true positive: proper upgrade headers yield True."""
        request = RequestReceived(
            method=b"GET",
            target=b"/ws",
            headers=(
                (b"Connection", b"Upgrade"),
                (b"Upgrade", b"websocket"),
            ),
            http_version="1.1",
        )
        assert is_websocket_upgrade(request) is True

    @given(
        headers=_HEADER_LIST,
        lookup=_HEADER_NAME,
    )
    def test_get_header_case_insensitive(
        self, headers: list[tuple[bytes, bytes]], lookup: bytes
    ) -> None:
        """get_header returns bytes or None, always matches case-insensitively."""
        result = get_header(headers, lookup)
        if result is not None:
            assert isinstance(result, bytes)
            # Verify there's actually a matching header (case-insensitive)
            assert any(n.lower() == lookup.lower() for n, _ in headers)

    @given(
        name=_HEADER_NAME,
        value=_HEADER_VALUE,
    )
    def test_get_header_finds_inserted_header(self, name: bytes, value: bytes) -> None:
        """A header we explicitly insert is always found by get_header."""
        headers = [(b"x-other", b"foo"), (name, value), (b"x-another", b"bar")]
        result = get_header(headers, name)
        assert result is not None
        # Should find the first match
        assert result == value or name.lower() == b"x-other" or name.lower() == b"x-another"

    @given(
        lookup=_HEADER_NAME,
    )
    def test_get_header_missing_returns_none(self, lookup: bytes) -> None:
        """get_header on an empty list always returns None."""
        assert get_header([], lookup) is None


# ===========================================================================
# 4. WebSocket handshake fuzzing
# ===========================================================================


class TestWebSocketHandshakeFuzz:
    """Property-based tests for pounce.protocols.ws handshake functions."""

    @given(key=st.binary(min_size=1, max_size=200))
    @settings(max_examples=200)
    def test_build_ws_accept_key_returns_valid_base64(self, key: bytes) -> None:
        """build_ws_accept_key always returns valid base64 bytes."""
        result = build_ws_accept_key(key)
        assert isinstance(result, bytes)
        # Must be valid base64 — decoding should not raise
        decoded = base64.b64decode(result)
        # SHA-1 digest is 20 bytes
        assert len(decoded) == 20

    @given(key=st.binary(min_size=1, max_size=200))
    def test_build_ws_accept_key_matches_rfc6455(self, key: bytes) -> None:
        """Accept key matches the RFC 6455 algorithm: SHA1(key + magic GUID)."""
        magic = b"258EAFA5-E914-47DA-95CA-5AB5353BE70A"
        expected = base64.b64encode(hashlib.sha1(key.strip() + magic).digest())  # noqa: S324
        assert build_ws_accept_key(key) == expected

    @given(
        key=st.binary(min_size=1, max_size=100),
        subprotocol=st.one_of(st.none(), st.text(alphabet=string.ascii_letters, max_size=30)),
    )
    def test_build_101_response_is_valid_http(self, key: bytes, subprotocol: str | None) -> None:
        """build_101_response always produces bytes starting with HTTP/1.1 101."""
        result = build_101_response(key, subprotocol=subprotocol)
        assert isinstance(result, bytes)
        assert result.startswith(b"HTTP/1.1 101 Switching Protocols\r\n")
        assert b"Upgrade: websocket\r\n" in result
        assert b"Connection: Upgrade\r\n" in result
        assert b"Sec-WebSocket-Accept: " in result
        # Must end with double CRLF
        assert result.endswith(b"\r\n\r\n")
        if subprotocol:
            assert b"Sec-WebSocket-Protocol: " in result

    @given(
        key=st.binary(min_size=1, max_size=50),
        extensions=st.one_of(
            st.none(),
            st.text(alphabet=string.ascii_letters + "-", max_size=40),
        ),
    )
    def test_build_101_response_extensions(self, key: bytes, extensions: str | None) -> None:
        """Extensions header is included only when extensions is not None."""
        result = build_101_response(key, extensions=extensions)
        if extensions:
            assert b"Sec-WebSocket-Extensions: " in result
        else:
            assert b"Sec-WebSocket-Extensions: " not in result


# ===========================================================================
# 5. Config validation fuzzing
# ===========================================================================


class TestServerConfigFuzz:
    """Property-based tests for pounce.config.ServerConfig validation."""

    @given(port=st.integers(min_value=0, max_value=65535))
    def test_valid_ports_accepted(self, port: int) -> None:
        """Ports 0-65535 should create a valid ServerConfig."""
        config = ServerConfig(port=port)
        assert config.port == port

    @given(
        port=st.one_of(
            st.integers(max_value=-1),
            st.integers(min_value=65536, max_value=1_000_000),
        )
    )
    def test_invalid_ports_rejected(self, port: int) -> None:
        """Ports outside 0-65535 must raise ValueError."""
        with pytest.raises(ValueError, match="port must be 0-65535"):
            ServerConfig(port=port)

    @given(workers=st.integers(min_value=0, max_value=128))
    def test_valid_workers_accepted(self, workers: int) -> None:
        """Non-negative worker counts should be accepted."""
        config = ServerConfig(workers=workers)
        assert config.workers == workers

    @given(workers=st.integers(max_value=-1))
    def test_negative_workers_rejected(self, workers: int) -> None:
        """Negative worker counts must raise ValueError."""
        with pytest.raises(ValueError, match="workers must be >= 0"):
            ServerConfig(workers=workers)

    @given(
        timeout=st.floats(min_value=0.001, max_value=3600.0, allow_nan=False, allow_infinity=False),
    )
    def test_valid_timeouts_accepted(self, timeout: float) -> None:
        """Positive timeouts should create a valid config."""
        config = ServerConfig(
            keep_alive_timeout=timeout,
            request_timeout=timeout,
            header_timeout=timeout,
            shutdown_timeout=timeout,
        )
        assert config.keep_alive_timeout == timeout

    @given(
        timeout=st.one_of(
            st.just(0.0),
            st.floats(max_value=-0.001, allow_nan=False, allow_infinity=False),
        ),
    )
    def test_non_positive_timeouts_rejected(self, timeout: float) -> None:
        """Zero or negative timeouts must raise ValueError."""
        with pytest.raises(ValueError, match="keep_alive_timeout must be > 0"):
            ServerConfig(keep_alive_timeout=timeout)
