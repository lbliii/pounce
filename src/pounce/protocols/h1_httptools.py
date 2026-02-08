"""
HTTP/1.1 protocol handler — C-accelerated backend via httptools.

Drop-in replacement for ``H1Protocol`` that uses httptools (Node.js
http-parser bindings) for parsing.  Response serialization is hand-crafted
since httptools only parses inbound data.

Install via ``pip install pounce[fast]`` to enable.

All state is per-connection, per-request-cycle.  No shared mutable state.

"""

from __future__ import annotations

from pounce._errors import LimitError, ParseError
from pounce.protocols._base import (
    BodyReceived,
    ConnectionClosed,
    ProtocolEvent,
    RequestReceived,
)

try:
    import httptools  # type: ignore[import-untyped]

    _httptools_available = True
except ImportError:
    _httptools_available = False


def is_httptools_available() -> bool:
    """Return True if httptools is installed."""
    return _httptools_available


# ---------------------------------------------------------------------------
# HTTP/1.1 status phrases for response serialization
# ---------------------------------------------------------------------------

_STATUS_PHRASES: dict[int, bytes] = {
    100: b"Continue",
    101: b"Switching Protocols",
    103: b"Early Hints",
    200: b"OK",
    201: b"Created",
    204: b"No Content",
    301: b"Moved Permanently",
    302: b"Found",
    304: b"Not Modified",
    307: b"Temporary Redirect",
    308: b"Permanent Redirect",
    400: b"Bad Request",
    401: b"Unauthorized",
    403: b"Forbidden",
    404: b"Not Found",
    405: b"Method Not Allowed",
    408: b"Request Timeout",
    413: b"Content Too Large",
    429: b"Too Many Requests",
    431: b"Request Header Fields Too Large",
    500: b"Internal Server Error",
    502: b"Bad Gateway",
    503: b"Service Unavailable",
}


class H1HttpToolsProtocol:
    """HTTP/1.1 protocol handler backed by httptools (C-accelerated).

    Implements the same ``ProtocolHandler`` contract as ``H1Protocol``.
    Uses httptools for parsing (much faster than h11) and hand-crafts
    response serialization in pure Python.

    Args:
        max_incomplete_event_size: Unused — kept for interface compatibility.

    """

    __slots__ = (
        "_parser",
        "_events",
        "_current_url",
        "_current_headers",
        "_body_chunks",
        "_keep_alive",
        "_request_complete",
        "_response_started",
        "_chunked",
        "_max_size",
        "_header_bytes",
    )

    def __init__(self, *, max_incomplete_event_size: int | None = None) -> None:
        if not _httptools_available:
            raise RuntimeError(
                "httptools is not installed. Install with: pip install pounce[fast]"
            )
        self._events: list[ProtocolEvent] = []
        self._current_url: bytes = b""
        self._current_headers: list[tuple[bytes, bytes]] = []
        self._body_chunks: list[bytes] = []
        self._keep_alive: bool = True
        self._request_complete: bool = False
        self._response_started: bool = False
        self._chunked: bool = False
        self._max_size: int | None = max_incomplete_event_size
        self._header_bytes: int = 0
        self._parser = httptools.HttpRequestParser(self)  # type: ignore[attr-defined]

    # -- httptools callbacks ------------------------------------------------

    def on_url(self, url: bytes) -> None:
        """Called when the URL is parsed."""
        self._current_url = url
        if self._max_size is not None:
            self._header_bytes += len(url)
            if self._header_bytes > self._max_size:
                raise LimitError(
                    f"Request head exceeds {self._max_size} bytes",
                    status_code=431,
                )

    def on_header(self, name: bytes, value: bytes) -> None:
        """Called for each header pair."""
        self._current_headers.append((name, value))
        if self._max_size is not None:
            self._header_bytes += len(name) + len(value) + 4  # ": " + "\r\n"
            if self._header_bytes > self._max_size:
                raise LimitError(
                    f"Request head exceeds {self._max_size} bytes",
                    status_code=431,
                )

    def on_headers_complete(self) -> None:
        """Called when all headers have been parsed."""
        method = self._parser.get_method()
        http_version = self._parser.get_http_version()
        self._keep_alive = self._parser.should_keep_alive()

        self._events.append(
            RequestReceived(
                method=method,
                target=self._current_url,
                headers=tuple(self._current_headers),
                http_version=http_version,
            )
        )
        # Reset for next request
        self._current_url = b""
        self._current_headers = []
        self._header_bytes = 0

    def on_body(self, body: bytes) -> None:
        """Called for each chunk of body data."""
        self._events.append(BodyReceived(data=body, more=True))

    def on_message_complete(self) -> None:
        """Called when the full request has been parsed."""
        self._events.append(BodyReceived(data=b"", more=False))
        self._request_complete = True

    # -- ProtocolHandler interface -----------------------------------------

    def receive_data(self, data: bytes) -> list[ProtocolEvent]:
        """Feed raw bytes from the socket, return parsed protocol events.

        Args:
            data: Raw bytes received from the network.

        Returns:
            List of protocol events parsed from the input.

        Raises:
            ParseError: If httptools encounters malformed HTTP.

        """
        self._events = []
        try:
            self._parser.feed_data(data)
        except LimitError:
            raise  # Propagate size limit errors as-is
        except httptools.HttpParserError as exc:  # type: ignore[attr-defined]
            raise ParseError(str(exc)) from exc
        except httptools.HttpParserCallbackError as exc:  # type: ignore[attr-defined]
            # Callback errors wrap the original exception; unwrap LimitError
            if isinstance(exc.__cause__, LimitError):
                raise exc.__cause__ from exc
            raise ParseError(str(exc)) from exc

        result = self._events
        self._events = []
        return result

    def send_response(
        self, status: int, headers: list[tuple[bytes, bytes]]
    ) -> bytes:
        """Serialize a response status line and headers into bytes.

        httptools only parses — response serialization is hand-crafted
        for maximum speed (no library overhead, no intermediate objects).

        Detects ``Transfer-Encoding: chunked`` in the response headers
        so that ``send_body`` produces correct chunked framing.

        Args:
            status: HTTP status code.
            headers: Response headers as (name, value) byte pairs.

        Returns:
            Serialized HTTP/1.1 response head bytes.

        """
        self._response_started = True
        self._chunked = any(
            name.lower() == b"transfer-encoding" and b"chunked" in value.lower()
            for name, value in headers
        )
        phrase = _STATUS_PHRASES.get(status, b"Unknown")

        # Pre-compute size for efficient allocation
        parts: list[bytes] = [
            b"HTTP/1.1 ",
            str(status).encode("ascii"),
            b" ",
            phrase,
            b"\r\n",
        ]
        for name, value in headers:
            parts.extend((name, b": ", value, b"\r\n"))
        parts.append(b"\r\n")

        return b"".join(parts)

    def send_body(self, data: bytes, more: bool = False) -> bytes:
        """Serialize a response body chunk into bytes.

        When chunked transfer encoding is active (detected from response
        headers), produces proper chunked framing with size lines and
        a zero-length terminator.

        Args:
            data: Body bytes to send.
            more: True if more body chunks will follow.

        Returns:
            Serialized bytes to write to the socket.

        """
        if not self._chunked:
            return data

        parts: list[bytes] = []
        if data:
            parts.append(b"%x\r\n" % len(data))
            parts.append(data)
            parts.append(b"\r\n")
        if not more:
            parts.append(b"0\r\n\r\n")
        return b"".join(parts)

    def start_new_cycle(self) -> None:
        """Prepare for the next request on keep-alive connections."""
        self._request_complete = False
        self._response_started = False
        self._chunked = False
        self._header_bytes = 0
        # httptools parser is reset internally per-message

    # -- Introspection -----------------------------------------------------

    @property
    def keep_alive(self) -> bool:
        """True if the client wants to keep the connection alive."""
        return self._keep_alive
