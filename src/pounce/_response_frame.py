"""Pre-built HTTP response framing for the fused sync path.

Bypasses H1Protocol/h11 for the hot path — serializes response directly
to avoid protocol state machine overhead. Date header cached per-second
(RFC 7231 allows 1s resolution).
"""

from email.utils import formatdate


def serialize_raw_response(
    status: int,
    headers: tuple[tuple[bytes, bytes], ...],
    body: bytes,
    *,
    server_header: str = "pounce",
    date_header: bytes | None = None,
) -> bytes:
    """Serialize a full HTTP/1.1 response without using h11.

    Args:
        status: HTTP status code.
        headers: Response headers as (name, value) byte pairs.
        body: Response body bytes.
        server_header: Value for Server header.
        date_header: Pre-formatted Date header (e.g. b"date: Wed, 11 Mar 2025 12:00:00 GMT\\r\\n").
            If None, omitted (client may cache less effectively).

    Returns:
        Complete HTTP/1.1 response bytes ready for socket.sendall().
    """
    head, _ = serialize_raw_response_parts(
        status, headers, body, server_header=server_header, date_header=date_header
    )
    return head + body


def serialize_raw_response_parts(
    status: int,
    headers: tuple[tuple[bytes, bytes], ...],
    body: bytes,
    *,
    server_header: str = "pounce",
    date_header: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Serialize HTTP/1.1 response as (head, body) for scatter-gather send.

    Use with socket.sendmsg([head, body]) to avoid concatenation.
    """
    parts: list[bytes] = []
    reason = _STATUS_REASONS.get(status, b"OK")
    parts.append(b"HTTP/1.1 " + str(status).encode() + b" " + reason + b"\r\n")
    parts.append(b"server: " + server_header.encode() + b"\r\n")
    if date_header is not None:
        parts.append(date_header)
    for name, value in headers:
        parts.append(name + b": " + value + b"\r\n")
    parts.append(b"\r\n")
    head = b"".join(parts)
    return (head, body)


def get_date_header_bytes() -> bytes:
    """RFC 7231 Date header value, e.g. b'date: Wed, 11 Mar 2025 12:00:00 GMT\\r\\n'."""
    return b"date: " + formatdate(usegmt=True).encode() + b"\r\n"


# Common status reason phrases — avoid dict lookup for 200
_STATUS_REASONS: dict[int, bytes] = {
    200: b"OK",
    201: b"Created",
    204: b"No Content",
    301: b"Moved Permanently",
    302: b"Found",
    303: b"See Other",
    304: b"Not Modified",
    400: b"Bad Request",
    401: b"Unauthorized",
    403: b"Forbidden",
    404: b"Not Found",
    405: b"Method Not Allowed",
    413: b"Content Too Large",
    422: b"Unprocessable Entity",
    500: b"Internal Server Error",
    501: b"Not Implemented",
    502: b"Bad Gateway",
    503: b"Service Unavailable",
}
