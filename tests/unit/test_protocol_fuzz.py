"""Hypothesis property-based tests for H1 protocol and scope building.

Fuzzes receive_data, build_scope, and path encoding with random inputs.
"""

import hypothesis.strategies as st
from hypothesis import given

from pounce.asgi.bridge import build_scope
from pounce.config import ServerConfig
from pounce.protocols._base import RequestReceived
from pounce.protocols.h1 import H1Protocol

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_http_method = st.sampled_from([b"GET", b"POST", b"PUT", b"DELETE", b"HEAD", b"OPTIONS", b"PATCH"])
_ascii_path = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/.-_",
    min_size=1,
    max_size=100,
)
_path = _ascii_path.map(lambda s: s.encode("ascii"))

# Framing headers (Content-Length / Transfer-Encoding) have syntactic rules that
# h11 enforces: an empty or non-digit Content-Length, or a malformed
# Transfer-Encoding, is correctly rejected as a *bad* request. The
# arbitrary-header strategy must not emit these names, or it would generate
# requests that h11 legitimately refuses -- falsifying the
# "valid request does not raise" contract. We exclude them here and add a
# dedicated well-formed framing-header strategy to preserve coverage.
_FRAMING_HEADER_NAMES = frozenset({b"content-length", b"transfer-encoding"})
_header_name = (
    st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-",
        min_size=1,
        max_size=64,
    )
    .map(lambda s: s.encode("ascii"))
    .filter(lambda n: n.lower() not in _FRAMING_HEADER_NAMES)
)
_header_value = st.text(
    alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -.",
    min_size=0,
    max_size=200,
).map(lambda s: s.encode("ascii"))
_header = st.tuples(_header_name, _header_value)
_headers = st.lists(_header, max_size=20)

# A well-formed framing header -- or none. Content-Length is always a valid
# non-negative integer; Transfer-Encoding is the canonical "chunked". This keeps
# genuine coverage of framing headers without ever emitting a malformed value.
_framing_header = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=2**31).map(
        lambda n: (b"Content-Length", str(n).encode("ascii"))
    ),
    st.just((b"Transfer-Encoding", b"chunked")),
)


def _build_valid_request(
    method: bytes,
    path: bytes,
    headers: list[tuple[bytes, bytes]],
) -> bytes:
    """Build valid HTTP/1.1 request bytes."""
    has_host = any(h[0].lower() == b"host" for h in headers)
    if not has_host:
        headers = [(b"Host", b"localhost"), *list(headers)]
    # Ensure path starts with /
    target = path if path.startswith(b"/") else b"/" + path
    lines = [method + b" " + target + b" HTTP/1.1"]
    for name, value in headers:
        lines.append(name + b": " + value)
    lines.append(b"")
    lines.append(b"")
    return b"\r\n".join(lines)


# ---------------------------------------------------------------------------
# H1Protocol receive_data
# ---------------------------------------------------------------------------


class TestH1ProtocolFuzz:
    """Hypothesis fuzz tests for H1Protocol.receive_data()."""

    @given(
        method=_http_method,
        path=_path,
        headers=_headers,
        framing=_framing_header,
    )
    def test_valid_request_does_not_raise(
        self,
        method: bytes,
        path: bytes,
        headers: list,
        framing: tuple[bytes, bytes] | None,
    ) -> None:
        """Valid HTTP request bytes -- receive_data does not raise."""
        proto = H1Protocol()
        # Append a well-formed framing header (if any). receive_data emits
        # RequestReceived once the header block is complete, even when the body
        # promised by Content-Length / chunked encoding has not yet arrived.
        full_headers = [*headers, framing] if framing is not None else list(headers)
        raw = _build_valid_request(method, path, full_headers)
        events = proto.receive_data(raw)
        assert len(events) >= 1
        assert isinstance(events[0], RequestReceived)


# ---------------------------------------------------------------------------
# build_scope
# ---------------------------------------------------------------------------


class TestBuildScopeFuzz:
    """Hypothesis fuzz tests for build_scope()."""

    @given(
        method=_http_method,
        target=_path,
        headers=_headers,
    )
    def test_build_scope_produces_valid_dict(
        self,
        method: bytes,
        target: bytes,
        headers: list[tuple[bytes, bytes]],
    ) -> None:
        """build_scope produces valid scope dict from random headers."""
        has_host = any(h[0].lower() == b"host" for h in headers)
        if not has_host:
            headers = [(b"Host", b"localhost"), *list(headers)]
        request = RequestReceived(
            method=method,
            target=target,
            headers=tuple(headers),
            http_version="1.1",
        )
        config = ServerConfig()
        client = ("127.0.0.1", 12345)
        server = ("127.0.0.1", 8000)
        scope = build_scope(request, config, client, server)
        assert "type" in scope
        assert scope["type"] == "http"
        assert "method" in scope
        assert "path" in scope
        assert "headers" in scope


# ---------------------------------------------------------------------------
# Path encoding (path vs raw_path)
# ---------------------------------------------------------------------------


class TestPathEncodingFuzz:
    """Path encoding: path decoded, raw_path preserved."""

    @given(
        path_str=st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/.-_%",
            min_size=1,
            max_size=50,
        ),
    )
    def test_path_raw_path_roundtrip(self, path_str: str) -> None:
        """Percent-encoded path: receive_data parses, target preserved."""
        from urllib.parse import quote, unquote

        encoded = quote(path_str, safe="/")
        raw = f"GET /{encoded} HTTP/1.1\r\nHost: localhost\r\n\r\n\r\n".encode()
        proto = H1Protocol()
        events = proto.receive_data(raw)
        assert len(events) >= 1
        if isinstance(events[0], RequestReceived):
            req = events[0]
            # target preserves the raw path from the request
            decoded = unquote(req.target.decode("ascii", "replace"))
            assert decoded  # Non-empty
