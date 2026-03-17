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

import contextlib
import errno
import logging
import os
import socket
import sys

from pounce.config import ServerConfig

logger = logging.getLogger("pounce.net")


def create_listener(config: ServerConfig) -> socket.socket:
    """Create and bind a single server socket from configuration.

    If ``config.uds`` is set, creates a Unix domain socket.
    Otherwise creates a TCP socket bound to ``config.host:config.port``.

    Args:
        config: Server configuration with host, port, and backlog settings.

    Returns:
        A bound, listening, non-blocking socket.

    Raises:
        OSError: If the address is already in use or permission is denied.

    """
    if config.uds is not None:
        return _bind_unix_socket(config)
    return _bind_socket(config)


def create_listeners(
    config: ServerConfig,
    count: int,
    *,
    shared: bool = False,
) -> list[socket.socket]:
    """Create server sockets for *count* workers.

    When *shared* is True (recommended for thread workers), a single socket
    is created and returned for every worker — all threads call ``accept()``
    on the same fd and the kernel distributes connections naturally.

    When *shared* is False (required for process workers), each worker gets
    its own independently bound ``SO_REUSEPORT`` socket on platforms that
    support it.  On platforms without ``SO_REUSEPORT`` the shared strategy
    is used as a fallback regardless of this flag.

    Args:
        config: Server configuration.
        count: Number of worker sockets needed.
        shared: If True, all workers share a single socket fd.  Use this
            for thread-based workers to avoid macOS SO_REUSEPORT
            distribution issues.

    Returns:
        A list of *count* sockets.  Callers must not close a shared socket
        until all workers have stopped.

    """
    if count < 1:
        msg = f"count must be >= 1 (got {count})"
        raise ValueError(msg)

    if config.uds is not None:
        # Unix sockets are shared — all workers accept from the same fd
        shared_sock = _bind_unix_socket(config)
        return [shared_sock] * count

    if count == 1:
        return [_bind_socket(config, use_reuseport=False)]

    # Thread workers share one socket — kernel accept queue distributes
    # connections naturally without SO_REUSEPORT quirks.
    if shared:
        shared_sock = _bind_socket(config)
        logger.info(
            "Created shared socket on %s:%d for %d workers",
            config.host,
            config.port,
            count,
        )
        return [shared_sock] * count

    if has_so_reuseport():
        # Each worker binds independently — kernel distributes connections
        sockets: list[socket.socket] = []
        try:
            sockets.extend(
                _bind_socket(config, _log_listen=False, use_reuseport=True) for _ in range(count)
            )
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
    shared_sock = _bind_socket(config)
    logger.info(
        "Created shared socket on %s:%d for %d workers (no SO_REUSEPORT)",
        config.host,
        config.port,
        count,
    )
    return [shared_sock] * count


def create_udp_listener(config: ServerConfig) -> socket.socket:
    """Create and bind a single UDP socket for HTTP/3 (QUIC).

    Binds to config.host:config.port. When config.port is 0 (ephemeral),
    the OS assigns a port; callers must pass the resolved TCP port (from
    the bound TCP socket) so HTTP/3 shares the advertised address.

    UDP has no listen() or backlog.

    Args:
        config: Server configuration with host and port.

    Returns:
        A bound, non-blocking UDP socket.

    """
    return _bind_udp_socket(config)


def create_udp_listeners(config: ServerConfig, count: int) -> list[socket.socket]:
    """Create UDP sockets for *count* HTTP/3 workers.

    Mirrors create_listeners: SO_REUSEPORT for independent sockets,
    shared socket when unavailable.

    Args:
        config: Server configuration.
        count: Number of worker sockets needed.

    Returns:
        A list of *count* UDP sockets.

    """
    if count < 1:
        msg = f"count must be >= 1 (got {count})"
        raise ValueError(msg)

    if count == 1:
        return [_bind_udp_socket(config, use_reuseport=False)]

    if has_so_reuseport():
        sockets: list[socket.socket] = []
        try:
            sockets.extend(
                _bind_udp_socket(config, _log_bind=False, use_reuseport=True) for _ in range(count)
            )
        except Exception:
            for s in sockets:
                s.close()
            raise
        logger.info(
            "Created %d independent UDP sockets with SO_REUSEPORT on %s:%d",
            count,
            config.host,
            config.port,
        )
        return sockets

    shared = _bind_udp_socket(config)
    logger.info(
        "Created shared UDP socket on %s:%d for %d workers (no SO_REUSEPORT)",
        config.host,
        config.port,
        count,
    )
    return [shared] * count


def has_so_reuseport() -> bool:
    """Check if SO_REUSEPORT is available on this platform."""
    return hasattr(socket, "SO_REUSEPORT") and sys.platform != "win32"


def _bind_unix_socket(config: ServerConfig) -> socket.socket:
    """Create, bind, and listen on a Unix domain socket.

    Removes any stale socket file before binding.  The socket file
    should be cleaned up on shutdown via ``cleanup_unix_socket()``.

    """
    path = config.uds
    assert path is not None

    # Remove stale socket file if it exists
    with contextlib.suppress(FileNotFoundError):
        os.unlink(path)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(path)
        sock.listen(config.backlog)
        sock.setblocking(False)

        # Make the socket file accessible to the web server (e.g. nginx)
        os.chmod(path, 0o666)

        logger.info("Listening on unix:%s (backlog=%d)", path, config.backlog)
        return sock

    except OSError:
        sock.close()
        raise


def cleanup_unix_socket(config: ServerConfig) -> None:
    """Remove the Unix domain socket file on shutdown.

    Safe to call even if no UDS is configured (no-op).

    """
    if config.uds is not None:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(config.uds)
            logger.info("Removed socket file %s", config.uds)


def _bind_socket(
    config: ServerConfig,
    *,
    _log_listen: bool = True,
    use_reuseport: bool = False,
) -> socket.socket:
    """Create, configure, bind, and listen on a single TCP socket.

    Uses ``getaddrinfo`` to resolve the host, supporting both IPv4 and
    IPv6 addresses.  When binding to an IPv6 address, enables dual-stack
    (``IPV6_V6ONLY=False``) where possible so both IPv4 and IPv6 clients
    can connect.

    When ``use_reuseport`` is False (default for single-worker dev), a
    second instance binding to the same address will fail with EADDRINUSE,
    ensuring single-instance semantics for development.
    """
    # Resolve host to get the correct address family
    infos = socket.getaddrinfo(
        config.host,
        config.port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
        flags=socket.AI_PASSIVE,
    )
    if not infos:
        msg = f"Could not resolve address {config.host}:{config.port}"
        raise OSError(msg)

    # Prefer IPv6 for dual-stack, fall back to IPv4
    af, socktype, proto, _canonname, sockaddr = infos[0]
    for info in infos:
        if info[0] == socket.AF_INET6:
            af, socktype, proto, _canonname, sockaddr = info
            break

    sock = socket.socket(af, socktype, proto)

    try:
        # Allow immediate reuse of the address after restart
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # Enable dual-stack on IPv6 sockets where supported
        if af == socket.AF_INET6:
            with contextlib.suppress(AttributeError, OSError):
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)

        # SO_REUSEPORT: only enable for multi-worker (kernel distribution).
        # Single-worker dev keeps it off so duplicate instances fail fast.
        if use_reuseport and has_so_reuseport():
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        sock.bind(sockaddr)
        sock.listen(config.backlog)
        sock.setblocking(False)

        if _log_listen:
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
        if exc.errno == errno.EADDRINUSE or "already in use" in str(exc).lower():
            raise OSError(
                f"Address {config.host}:{config.port} is already in use. Is another server running?"
            ) from exc
        if exc.errno == errno.EACCES:
            raise OSError(
                f"Permission denied binding to {config.host}:{config.port}. "
                "Try a port > 1024 or run with elevated permissions."
            ) from exc
        raise


def _bind_udp_socket(
    config: ServerConfig,
    *,
    _log_bind: bool = True,
    use_reuseport: bool = False,
) -> socket.socket:
    """Create, configure, and bind a single UDP socket for HTTP/3.

    UDP has no listen() or backlog. Uses same address resolution as TCP.

    """
    infos = socket.getaddrinfo(
        config.host,
        config.port,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_DGRAM,
        flags=socket.AI_PASSIVE,
    )
    if not infos:
        msg = f"Could not resolve address {config.host}:{config.port}"
        raise OSError(msg)

    af, socktype, proto, _canonname, sockaddr = infos[0]
    for info in infos:
        if info[0] == socket.AF_INET6:
            af, socktype, proto, _canonname, sockaddr = info
            break

    sock = socket.socket(af, socktype, proto)

    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if af == socket.AF_INET6:
            with contextlib.suppress(AttributeError, OSError):
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        if use_reuseport and has_so_reuseport():
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        sock.bind(sockaddr)
        sock.setblocking(False)

        if _log_bind:
            actual_addr = sock.getsockname()
            logger.info("UDP socket bound on %s:%d (HTTP/3)", actual_addr[0], actual_addr[1])

        return sock

    except OSError as exc:
        sock.close()
        if exc.errno == errno.EADDRINUSE or "already in use" in str(exc).lower():
            raise OSError(
                f"Address {config.host}:{config.port} is already in use. Is another server running?"
            ) from exc
        if exc.errno == errno.EACCES:
            raise OSError(
                f"Permission denied binding to {config.host}:{config.port}. "
                "Try a port > 1024 or run with elevated permissions."
            ) from exc
        raise
