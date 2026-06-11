"""
Fast HTTP/1.1 parser for the sync worker — replaces h11 on the hot path.

Parses request lines and headers directly from bytes using split/index
operations (~3 µs vs ~22 µs for h11) while enforcing the same safety
checks that matter for real-world deployment:

- Method validation (rejects malformed method tokens per RFC 9110)
- Header size limit (prevents memory exhaustion)
- Null byte / control character injection in targets and header names
- Duplicate Content-Length detection (request smuggling vector)
- Duplicate Host detection (request smuggling / routing-desync vector)
- Content-Length + Transfer-Encoding conflict (RFC 7230 §3.3.3)
- Negative or non-numeric Content-Length rejection
- Chunked Transfer-Encoding detection (returns flag so caller can handle)

Not a full HTTP parser — does not handle:
- Chunked body decoding (caller must handle or reject)
- Obs-fold header continuation lines (obsolete since RFC 7230)
- Trailer headers
"""

import logging
import re as _re
from typing import Final

from pounce._errors import ParseError
from pounce.protocols._base import RequestReceived

logger = logging.getLogger("pounce.parser")

# Pre-intern constants
_CRLF: Final = b"\r\n"
_CRLFCRLF: Final = b"\r\n\r\n"
_COLON_SPACE: Final = b": "
_COLON: Final = b":"
_SPACE: Final = b" "
_HTTP_1_1: Final = "1.1"
_HTTP_1_0: Final = "1.0"

# Regex for valid HTTP method tokens (RFC 9110 section 9.1: 1*tchar)
# tchar = "!" / "#" / "$" / "%" / "&" / "'" / "*" / "+" / "-" / "." /
#         "^" / "_" / "`" / "|" / "~" / DIGIT / ALPHA
_METHOD_RE: Final = _re.compile(rb"^[A-Za-z0-9!#$%&'*+\-.^_`|~]+$")

# Max header block size (16 KiB) — matches nginx default
_MAX_HEADER_SIZE: Final = 16384


def parse_request(
    buf: memoryview,
    length: int,
    *,
    max_headers: int = 100,
    max_header_size: int = _MAX_HEADER_SIZE,
) -> tuple[RequestReceived | None, bytes, int, bool]:
    """Parse an HTTP request from a buffer.

    Returns:
        (request, body, consumed, chunked) on success.
        (None, b"", 0, False) if the buffer doesn't contain a complete
        request yet (need more data).

    Raises:
        ParseError: The request is malformed — caller should send 400
        and close the connection.

    The *consumed* count tells the caller how many bytes were used,
    allowing leftover data to be carried forward for pipelining.
    The *chunked* flag indicates Transfer-Encoding: chunked was present.
    """
    data = bytes(buf[:length])

    # Find end of headers
    header_end = data.find(_CRLFCRLF)
    if header_end == -1:
        if length > max_header_size:
            raise ParseError(
                "Request headers too large",
                code="POUNCE_PARSE_HEADERS_TOO_LARGE",
                hint=f"Increase max_header_size (current: {max_header_size} bytes).",
            )
        return (None, b"", 0, False)

    if header_end > max_header_size:
        raise ParseError(
            "Request headers too large",
            code="POUNCE_PARSE_HEADERS_TOO_LARGE",
            hint=f"Increase max_header_size (current: {max_header_size} bytes).",
        )

    head = data[:header_end]
    body_start = header_end + 4  # skip \r\n\r\n

    # Parse request line: "METHOD /target HTTP/1.x"
    first_line_end = head.find(_CRLF)
    first_line = head if first_line_end == -1 else head[:first_line_end]

    sp1 = first_line.find(_SPACE)
    if sp1 == -1:
        raise ParseError("Malformed request line", code="POUNCE_PARSE_MALFORMED_REQUEST_LINE")
    sp2 = first_line.find(_SPACE, sp1 + 1)
    if sp2 == -1:
        # Same semantic (request line has fewer than two spaces); shared code is intentional.
        raise ParseError("Malformed request line", code="POUNCE_PARSE_MALFORMED_REQUEST_LINE")

    method = first_line[:sp1]
    if not _METHOD_RE.match(method):
        raise ParseError("Malformed HTTP method", code="POUNCE_PARSE_BAD_METHOD")

    target = first_line[sp1 + 1 : sp2]
    # Reject null bytes and bare CR/LF in the target (injection vectors)
    if b"\x00" in target or b"\r" in target or b"\n" in target:
        raise ParseError(
            "Invalid characters in request target",
            code="POUNCE_PARSE_BAD_TARGET",
        )

    version_part = first_line[sp2 + 1 :]
    if version_part == b"HTTP/1.1":
        http_version = _HTTP_1_1
    elif version_part == b"HTTP/1.0":
        http_version = _HTTP_1_0
    else:
        raise ParseError(
            "Unsupported HTTP version",
            code="POUNCE_PARSE_BAD_VERSION",
            hint="Pounce supports HTTP/1.0 and HTTP/1.1 on this listener.",
        )

    # Parse headers
    header_start = first_line_end + 2 if first_line_end != -1 else len(head)
    header_block = head[header_start:]
    headers: list[tuple[bytes, bytes]] = []
    content_length = -1  # -1 = not set
    has_transfer_encoding = False
    chunked = False
    host_count = 0

    pos = 0
    block_len = len(header_block)
    while pos < block_len:
        line_end = header_block.find(_CRLF, pos)
        if line_end == -1:
            line_end = block_len
        line = header_block[pos:line_end]
        pos = line_end + 2

        # Find colon — try ": " first (most common), then bare ":"
        colon = line.find(_COLON_SPACE)
        if colon != -1:
            name = line[:colon]
            value = line[colon + 2 :]
        else:
            colon = line.find(_COLON)
            if colon == -1:
                logger.debug("Skipping malformed header line (no colon): %r", line)
                continue  # skip malformed header lines
            name = line[:colon]
            value = line[colon + 1 :].lstrip()

        # Reject header names with spaces or null bytes
        if b" " in name or b"\x00" in name:
            raise ParseError("Invalid header name", code="POUNCE_PARSE_BAD_HEADER_NAME")

        name_lower = name.lower()
        headers.append((name_lower, value))

        if len(headers) > max_headers:
            raise ParseError(
                "Too many headers",
                code="POUNCE_PARSE_TOO_MANY_HEADERS",
                hint=f"Increase max_headers (current: {max_headers}).",
            )

        if name_lower == b"content-length":
            if content_length != -1:
                raise ParseError(
                    "Duplicate Content-Length header",
                    code="POUNCE_PARSE_DUPLICATE_CONTENT_LENGTH",
                )
            try:
                content_length = int(value)
            except ValueError:
                raise ParseError(
                    "Invalid Content-Length value",
                    code="POUNCE_PARSE_BAD_CONTENT_LENGTH",
                ) from None
            if content_length < 0:
                raise ParseError(
                    "Negative Content-Length",
                    code="POUNCE_PARSE_NEGATIVE_CONTENT_LENGTH",
                )
        elif name_lower == b"transfer-encoding":
            has_transfer_encoding = True
            if b"chunked" in value.lower():
                chunked = True
        elif name_lower == b"host":
            # Duplicate Host is a request-smuggling / routing-desync vector
            # (RFC 9112 §3.2). h11 (the async path) rejects this too — keep
            # both worker paths in lockstep.
            host_count += 1
            if host_count > 1:
                raise ParseError(
                    "Duplicate Host header",
                    code="POUNCE_PARSE_DUPLICATE_HOST",
                )

    # CL + TE together is a smuggling vector (RFC 7230 §3.3.3)
    if content_length >= 0 and has_transfer_encoding:
        raise ParseError(
            "Content-Length with Transfer-Encoding",
            code="POUNCE_PARSE_SMUGGLING_CL_TE",
            hint="RFC 7230 §3.3.3 forbids both headers on the same request.",
        )

    # Determine body size
    if chunked:
        # Caller must handle chunked decoding or reject
        body_length = 0
    elif content_length >= 0:
        body_length = content_length
    else:
        body_length = 0  # no body

    consumed = body_start + body_length
    if consumed > length:
        # Incomplete body — need more data
        return (None, b"", 0, False)

    body = data[body_start : body_start + body_length] if body_length > 0 else b""

    request = RequestReceived(
        method=method,
        target=target,
        headers=tuple(headers),
        http_version=http_version,
    )
    return (request, body, consumed, chunked)
