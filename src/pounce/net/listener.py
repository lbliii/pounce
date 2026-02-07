"""
Network listener — creates and configures server sockets.

Binds to the configured address, sets socket options (SO_REUSEADDR,
SO_REUSEPORT if available), and returns ready-to-accept sockets.

Phase 2 multi-worker strategy:
- SO_REUSEPORT available (Linux): each worker gets its own independently
  bound socket — the kernel distributes connections across them.
- SO_REUSEPORT unavailable (macOS, Windows): one socket is created and
  shared by all workers — all workers accept from the same fd.

The worker receives a socket and does not know which strategy was used.

"""

import logging
import socket
import sys

from pounce.config import ServerConfig

logger = logging.getLogger("pounce.net")

def create_listener(config: ServerConfig) -> socket.socket:
    """Create and bind a single server socket from configuration.

    Args:
        config: Server configuration with host, port, and backlog settings.

    Returns:
        A bound, listening, non-blocking socket.

    Raises:
        OSError: If the address is already in use or permission is denied.

    """
    return _bind_socket(config)

def create_listeners(config: ServerConfig, count: int) -> list[socket.socket]:
    """Create server sockets for *count* workers.

    On platforms with ``SO_REUSEPORT`` each worker gets its own
    independently bound socket so the kernel distributes connections.
    When ``SO_REUSEPORT`` is unavailable, a single socket is created and
    the same object is returned for every worker (shared-fd strategy).

    Args:
        config: Server configuration.
        count: Number of worker sockets needed.

    Returns:
        A list of *count* sockets.  Callers must not close a shared socket
        until all workers have stopped.

    """
    if count < 1:
        msg = f"count must be >= 1 (got {count})"
        raise ValueError(msg)

    if count == 1:
        return [_bind_socket(config)]

    if has_so_reuseport():
        # Each worker binds independently — kernel distributes connections
        sockets: list[socket.socket] = []
        try:
            for _ in range(count):
                sockets.append(_bind_socket(config))
        except Exception:
            # Clean up any sockets that were successfully created
            for s in sockets:
                s.close()
            raise
        logger.info(
            "Created %d independent sockets with SO_REUSEPORT on %s:%d",
            count,
            config.host,
            config.port,
        )
        return sockets

    # Shared-socket fallback — one socket, all workers accept from it
    shared = _bind_socket(config)
    logger.info(
        "Created shared socket on %s:%d for %d workers (no SO_REUSEPORT)",
        config.host,
        config.port,
        count,
    )
    return [shared] * count

def has_so_reuseport() -> bool:
    """Check if SO_REUSEPORT is available on this platform."""
    return hasattr(socket, "SO_REUSEPORT") and sys.platform != "win32"

def _bind_socket(config: ServerConfig) -> socket.socket:
    """Create, configure, bind, and listen on a single TCP socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        # Allow immediate reuse of the address after restart
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # SO_REUSEPORT allows multiple sockets to bind to the same port
        if has_so_reuseport():
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
