"""
Network listener — creates and configures the server socket.

Binds to the configured address, sets socket options (SO_REUSEADDR,
SO_REUSEPORT if available), and returns a ready-to-accept socket.

The socket is non-blocking for use with asyncio's event loop.

"""

from __future__ import annotations

import logging
import socket
import sys

from pounce.config import ServerConfig

logger = logging.getLogger("pounce.net")


def create_listener(config: ServerConfig) -> socket.socket:
    """Create and bind a server socket from configuration.

    Args:
        config: Server configuration with host, port, and backlog settings.

    Returns:
        A bound, listening, non-blocking socket.

    Raises:
        OSError: If the address is already in use or permission is denied.

    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        # Allow immediate reuse of the address after restart
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # SO_REUSEPORT allows multiple workers to bind to the same port
        # Available on Linux 3.9+ and macOS/BSD
        if _has_so_reuseport():
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        sock.bind((config.host, config.port))
        sock.listen(config.backlog)
        sock.setblocking(False)

        actual_addr = sock.getsockname()
        logger.info(
            "Listening on %s:%d (backlog=%d)",
            actual_addr[0],
            actual_addr[1],
            config.backlog,
        )

        return sock

    except OSError as exc:
        sock.close()
        if exc.errno == 98 or "already in use" in str(exc).lower():  # EADDRINUSE
            raise OSError(
                f"Address {config.host}:{config.port} is already in use. "
                "Is another server running?"
            ) from exc
        if exc.errno == 13:  # EACCES
            raise OSError(
                f"Permission denied binding to {config.host}:{config.port}. "
                "Try a port > 1024 or run with elevated permissions."
            ) from exc
        raise


def _has_so_reuseport() -> bool:
    """Check if SO_REUSEPORT is available on this platform."""
    return hasattr(socket, "SO_REUSEPORT") and sys.platform != "win32"
