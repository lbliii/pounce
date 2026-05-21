"""Zero-copy sendfile support for static file serving.

Provides an async callable that uses os.sendfile() to transfer file
data directly from the filesystem to a socket, bypassing Python memory.

Constraints:
- Only works on non-TLS connections (SSL wraps the socket, preventing sendfile)
- Unix-like systems only (Linux, macOS, FreeBSD)
- Falls back gracefully when unavailable

"""

import asyncio
import os
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Type for the sendfile extension callable
type SendfileCallable = Callable[[Path, int, int], Coroutine[Any, Any, None]]

# sendfile chunk size — avoid holding the GIL too long per call
_SENDFILE_CHUNK = 1_048_576  # 1 MB


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


def create_sendfile_callable(writer: asyncio.StreamWriter) -> SendfileCallable:
    """Create an async sendfile callable bound to this writer's socket.

    The returned callable transfers file data to the socket using
    os.sendfile() in a thread executor (non-blocking).

    Args:
        writer: The asyncio StreamWriter for the connection.

    Returns:
        Async callable: (path, offset, count) -> None

    """
    sock = writer.get_extra_info("socket")
    loop = asyncio.get_running_loop()
    sock_fd = sock.fileno()

    async def sendfile(path: Path, offset: int, count: int) -> None:
        """Transfer file bytes to the socket using zero-copy sendfile.

        Drains the writer first to ensure any buffered data (e.g. HTTP
        response headers) is flushed to the socket before sendfile writes
        file bytes directly to the fd.

        Args:
            path: Filesystem path to the file.
            offset: Byte offset to start reading from.
            count: Number of bytes to transfer.

        """
        # Flush any buffered data (response headers) to the socket
        # before writing file bytes directly to the fd.
        await writer.drain()

        fd = os.open(str(path), os.O_RDONLY)
        try:
            remaining = count
            current_offset = offset
            while remaining > 0:
                chunk = min(remaining, _SENDFILE_CHUNK)
                sent = await loop.run_in_executor(
                    None, os.sendfile, sock_fd, fd, current_offset, chunk
                )
                if sent == 0:
                    break
                current_offset += sent
                remaining -= sent
        finally:
            os.close(fd)

    return sendfile
