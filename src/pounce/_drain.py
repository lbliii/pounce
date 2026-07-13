"""
Shared drain-503 wire format and write helpers.

Single source of truth for the HTTP 503 response that every worker mode
emits to *new* connections while the server is draining (SIGTERM / SIGHUP).
Keeping the bytes in one place means the async ``Worker``, the blocking
``SyncWorker`` and the ``AcceptDistributor`` answer byte-identically:

    HTTP/1.1 503 Service Unavailable
    Connection: close
    Retry-After: 1
    Content-Type: text/plain; charset=utf-8
    Content-Length: 23

    Server shutting down...

The body is short and actionable; ``Retry-After`` tells well-behaved
clients (and load balancers) to come back, while ``Connection: close``
guarantees the socket is torn down after the single response.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from pounce._errors import RequestTimeoutError
from pounce._health import build_health_response
from pounce._timeouts import drain_with_timeout

if TYPE_CHECKING:
    import socket

    from pounce.config import ServerConfig

# Drain-503 body. Length is folded into the header below so the
# Content-Length header can never drift out of sync with the payload.
_DRAIN_503_BODY: bytes = b"Server shutting down..."
_DRAIN_REQUEST_INSPECTION_TIMEOUT_S: float = 1.0

DRAIN_503_RESPONSE: bytes = (
    b"HTTP/1.1 503 Service Unavailable\r\n"
    b"Connection: close\r\n"
    b"Retry-After: 1\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Length: " + str(len(_DRAIN_503_BODY)).encode("ascii") + b"\r\n"
    b"\r\n" + _DRAIN_503_BODY
)


def _draining_health_method(request_head: bytes, health_check_path: str | None) -> bytes | None:
    """Return GET/HEAD when *request_head* targets the configured health path."""
    if health_check_path is None or b"\r\n\r\n" not in request_head:
        return None

    request_line = request_head.split(b"\r\n", 1)[0]
    parts = request_line.split(b" ", 2)
    if len(parts) != 3:
        return None
    method, target, version = parts
    if method not in (b"GET", b"HEAD") or version not in (b"HTTP/1.0", b"HTTP/1.1"):
        return None

    path = target.partition(b"?")[0]
    try:
        expected_path = health_check_path.encode("ascii")
    except UnicodeEncodeError:
        return None
    return method if path == expected_path else None


def _draining_health_response(
    method: bytes,
    *,
    worker_id: int,
    active_connections: int,
) -> bytes:
    """Serialize the configured readiness response for the drain boundary."""
    status, headers, body = build_health_response(
        worker_id=worker_id,
        active_connections=active_connections,
        draining=True,
    )
    response_body = b"" if method == b"HEAD" else body
    wire_headers = [
        *headers,
        (b"connection", b"close"),
        (b"retry-after", b"1"),
    ]
    status_line = f"HTTP/1.1 {status} Service Unavailable\r\n".encode("ascii")
    header_block = b"".join(name.title() + b": " + value + b"\r\n" for name, value in wire_headers)
    return status_line + header_block + b"\r\n" + response_body


def _inspection_timeout(config: ServerConfig, timeout: float | None) -> float:
    requested = _DRAIN_REQUEST_INSPECTION_TIMEOUT_S if timeout is None else max(timeout, 0.0)
    return min(
        requested,
        config.header_timeout,
        config.shutdown_timeout,
    )


def _read_request_head_sync(
    conn: socket.socket,
    config: ServerConfig,
    *,
    timeout: float | None,
) -> bytes:
    """Read one bounded HTTP/1 header block from a draining socket."""
    if config.health_check_path is None:
        return b""
    read_timeout = _inspection_timeout(config, timeout)
    if read_timeout <= 0:
        return b""

    deadline = time.monotonic() + read_timeout
    limit = config.max_header_size + 4
    request_head = bytearray()
    try:
        while len(request_head) < limit:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            conn.settimeout(remaining)
            chunk = conn.recv(min(4096, limit - len(request_head)))
            if not chunk:
                break
            request_head.extend(chunk)
            if b"\r\n\r\n" in request_head:
                break
    except (OSError, TimeoutError):  # fmt: skip
        pass
    return bytes(request_head)


async def _read_request_head_async(
    reader: asyncio.StreamReader,
    config: ServerConfig,
    *,
    timeout: float | None,
) -> bytes:
    """Read one bounded HTTP/1 header block from a draining stream."""
    if config.health_check_path is None:
        return b""
    read_timeout = _inspection_timeout(config, timeout)
    if read_timeout <= 0:
        return b""

    limit = config.max_header_size + 4
    request_head = bytearray()
    try:
        async with asyncio.timeout(read_timeout):
            while len(request_head) < limit:
                chunk = await reader.read(min(4096, limit - len(request_head)))
                if not chunk:
                    break
                request_head.extend(chunk)
                if b"\r\n\r\n" in request_head:
                    break
    except (OSError, TimeoutError):  # fmt: skip
        pass
    return bytes(request_head)


def _select_drain_response(
    request_head: bytes,
    config: ServerConfig,
    *,
    worker_id: int,
    active_connections: int,
) -> bytes:
    method = _draining_health_method(request_head, config.health_check_path)
    if method is None:
        return DRAIN_503_RESPONSE
    return _draining_health_response(
        method,
        worker_id=worker_id,
        active_connections=active_connections,
    )


def write_drain_503_sync(conn: socket.socket) -> None:
    """Send the drain 503 on a blocking socket; the caller still owns the close.

    Tolerates a client that has already gone away — a dropped connection
    during shutdown is expected, not exceptional.
    """
    # silent: client may have closed mid-drain; a 503 we cannot deliver is benign
    with contextlib.suppress(OSError):
        conn.sendall(DRAIN_503_RESPONSE)


def write_drain_response_sync(
    conn: socket.socket,
    config: ServerConfig,
    *,
    worker_id: int,
    active_connections: int,
    timeout: float | None = None,
) -> None:
    """Inspect one bounded request and write the matching drain response."""
    request_head = _read_request_head_sync(conn, config, timeout=timeout)
    response = _select_drain_response(
        request_head,
        config,
        worker_id=worker_id,
        active_connections=active_connections,
    )
    with contextlib.suppress(OSError):
        conn.sendall(response)


async def write_drain_503_async(
    writer: asyncio.StreamWriter,
    *,
    timeout: float = 30.0,
) -> None:
    """Send the drain 503 on an asyncio writer and close it.

    Tolerates a client that has already gone away — a dropped connection
    during shutdown is expected, not exceptional.
    """
    # silent: client may have closed mid-drain; a 503 we cannot deliver is benign
    with contextlib.suppress(OSError, RequestTimeoutError):
        writer.write(DRAIN_503_RESPONSE)
        await drain_with_timeout(writer, timeout)

    # Closing is unconditional: a failed drain must not leave the transport for
    # event-loop finalization, where free-threaded shutdown can race its cleanup.
    writer.close()
    # A peer reset may surface again while the transport finishes closing.
    with contextlib.suppress(OSError, RequestTimeoutError):
        await writer.wait_closed()


async def write_drain_response_async(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    config: ServerConfig,
    *,
    worker_id: int,
    active_connections: int,
    timeout: float | None = None,
) -> None:
    """Inspect one bounded request, write its drain response, and close."""
    request_head = await _read_request_head_async(reader, config, timeout=timeout)
    response = _select_drain_response(
        request_head,
        config,
        worker_id=worker_id,
        active_connections=active_connections,
    )
    with contextlib.suppress(OSError, RequestTimeoutError):
        writer.write(response)
        await drain_with_timeout(writer, config.write_timeout)

    writer.close()
    with contextlib.suppress(OSError, RequestTimeoutError):
        await writer.wait_closed()
