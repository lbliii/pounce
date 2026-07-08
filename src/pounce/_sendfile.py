"""Zero-copy sendfile support for static file serving.

Provides an async callable that uses ``loop.sendfile()`` to transfer file
data directly from the filesystem to a socket, bypassing Python memory.

Constraints:
- Only works on non-TLS connections (SSL wraps the socket, preventing sendfile)
- Unix-like systems only (Linux, macOS, FreeBSD)
- Falls back gracefully when unavailable

Back-pressure: asyncio sets transport sockets non-blocking, so a raw
``os.sendfile`` loop raises ``BlockingIOError`` (EAGAIN) the moment the
kernel send buffer fills — a normal flow-control signal that crashes a
hand-rolled loop.  ``loop.sendfile(transport, ...)`` is the transport-aware
primitive: it detaches the live transport from the selector, runs native
sendfile with proper EAGAIN / ``add_writer`` retry handling, then restores
the transport.  See https://github.com/lbliii/pounce/issues/72.

"""

import asyncio
import logging
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pounce._timeouts import drain_with_timeout, wait_for_write

logger = logging.getLogger("pounce.sendfile")

# Type for the sendfile extension callable
type SendfileCallable = Callable[[Path, int, int], Coroutine[Any, Any, None]]


@dataclass(frozen=True, slots=True)
class SendfileRegion:
    """Protocol-owned file body marker.

    h11's passthrough send path only needs ``len(data)`` for body accounting.
    The ASGI bridge recognizes this marker and transfers the referenced file
    range with ``os.sendfile`` after writing h11's surrounding framing bytes.
    """

    path: Path
    offset: int
    count: int

    def __len__(self) -> int:
        return self.count


def can_use_sendfile(writer: asyncio.StreamWriter) -> bool:
    """Check if sendfile can be used on this connection.

    Returns False for TLS connections and when the raw socket is unavailable.
    """
    # TLS connections: SSL wraps the socket, sendfile can't bypass it
    if writer.get_extra_info("ssl_object") is not None:
        return False

    # Need access to the raw socket fd
    sock = writer.get_extra_info("socket")
    if sock is None:
        return False

    # Check platform support
    return hasattr(os, "sendfile")


def create_sendfile_callable(
    writer: asyncio.StreamWriter,
    *,
    write_timeout: float = 30.0,
) -> SendfileCallable:
    """Create an async sendfile callable bound to this writer's transport.

    The returned callable transfers file data to the socket using
    ``loop.sendfile()``, which handles non-blocking-socket back-pressure
    (EAGAIN) via the selector instead of crashing in an executor thread.

    Args:
        writer: The asyncio StreamWriter for the connection.

    Returns:
        Async callable: (path, offset, count) -> None

    """
    loop = asyncio.get_running_loop()
    # The connection fd is OWNED by this transport (registered on the
    # selector).  loop.sendfile is the correct primitive for that case;
    # loop.sock_sendfile would double-register the fd and corrupt the
    # transport's I/O state.
    transport = writer.transport

    async def sendfile(path: Path, offset: int, count: int) -> None:
        """Transfer file bytes to the socket using zero-copy sendfile.

        Drains the writer first to ensure any buffered data (e.g. HTTP
        response headers / chunk framing) is flushed to the socket before
        the file body is transferred.

        Args:
            path: Filesystem path to the file.
            offset: Byte offset to start reading from.
            count: Number of bytes to transfer.

        """
        if count <= 0:
            return
        if writer.is_closing() or transport is None or transport.is_closing():
            return

        # Flush any buffered framing bytes (response headers / chunk prefix)
        # before the file body so ordering on the wire is correct.  A drain
        # failure means the client already vanished — abort cleanly.
        try:
            await drain_with_timeout(writer, write_timeout)
        except ConnectionError:
            # BrokenPipeError / ConnectionResetError both subclass ConnectionError.
            return
        if writer.is_closing() or transport.is_closing():
            return

        # loop.sendfile needs a real binary file OBJECT — it calls .fileno()
        # and os.fstat to confirm a regular file.  buffering=0 avoids an
        # extra BufferedReader layer; the context manager closes it.
        with open(path, "rb", buffering=0) as f:
            try:
                # Native path: detaches the transport from the selector, runs
                # os.sendfile with proper EAGAIN / add_writer retry handling,
                # then restores the transport.  fallback=True degrades to a
                # read+send loop (e.g. partial-file edge cases) instead of
                # raising.  Returns the number of bytes actually transferred.
                sent = await wait_for_write(
                    loop.sendfile(
                        transport,
                        f,
                        offset=offset,
                        count=count,
                        fallback=True,
                    ),
                    writer,
                    write_timeout,
                )
            except ConnectionError:
                # Client vanished mid-transfer (BrokenPipeError /
                # ConnectionResetError) — benign for a server.
                return
            except RuntimeError:
                # loop.sendfile raises RuntimeError if the transport is
                # closing / no longer supports sendfile.  Treat a closing
                # connection as a clean abort; re-raise anything else.
                if writer.is_closing() or transport.is_closing():
                    return
                raise

        if sent < count:
            # loop.sendfile stops at EOF without raising, so a file shorter
            # than offset+count (truncated mid-flight, or a caller passing
            # count > available bytes via the pounce.response.sendfile
            # extension) transfers fewer bytes than promised.  The h11 framing
            # (Content-Length / chunk size) was already committed for `count`
            # bytes, so the response is unsalvageable on a keep-alive
            # connection.  Abort so the client fails fast instead of hanging
            # on the missing bytes.
            logger.warning(
                "sendfile transferred %d of %d bytes for %s — aborting "
                "connection to avoid a truncated/desynced response",
                sent,
                count,
                path,
            )
            transport.abort()

    return sendfile
