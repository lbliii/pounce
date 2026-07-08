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

import contextlib
from typing import TYPE_CHECKING

from pounce._errors import RequestTimeoutError
from pounce._timeouts import drain_with_timeout

if TYPE_CHECKING:
    import asyncio
    import socket

# Drain-503 body. Length is folded into the header below so the
# Content-Length header can never drift out of sync with the payload.
_DRAIN_503_BODY: bytes = b"Server shutting down..."

DRAIN_503_RESPONSE: bytes = (
    b"HTTP/1.1 503 Service Unavailable\r\n"
    b"Connection: close\r\n"
    b"Retry-After: 1\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Length: " + str(len(_DRAIN_503_BODY)).encode("ascii") + b"\r\n"
    b"\r\n" + _DRAIN_503_BODY
)


def write_drain_503_sync(conn: socket.socket) -> None:
    """Send the drain 503 on a blocking socket; the caller still owns the close.

    Tolerates a client that has already gone away — a dropped connection
    during shutdown is expected, not exceptional.
    """
    # silent: client may have closed mid-drain; a 503 we cannot deliver is benign
    with contextlib.suppress(OSError):
        conn.sendall(DRAIN_503_RESPONSE)


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
        writer.close()
        await writer.wait_closed()
