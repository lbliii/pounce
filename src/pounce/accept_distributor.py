"""
AcceptDistributor — single-thread accept feeding a shared worker queue.

Eliminates thundering herd on macOS/Windows where SO_REUSEPORT is
unavailable. One thread accepts connections and enqueues them into a
single shared queue from which all SyncWorkers pull.

"""

import logging
import queue
import socket
import ssl
import threading

logger = logging.getLogger("pounce.accept_distributor")


def is_shared_socket(sockets: list[socket.socket]) -> bool:
    """True if all workers share the same socket (no SO_REUSEPORT)."""
    if len(sockets) < 2:
        return False
    first_id = id(sockets[0])
    return all(id(s) == first_id for s in sockets)


class AcceptDistributor:
    """Single thread that accepts connections and feeds a shared queue.

    Used when SO_REUSEPORT is unavailable (macOS, Windows). Avoids
    thundering herd where all workers block on accept() on the same fd.

    All SyncWorkers pull from the same shared queue so the first idle
    worker handles the next connection — no head-of-line blocking.

    """

    __slots__ = (
        "_conn_queue",
        "_ext_shutdown",
        "_logger",
        "_sock",
        "_ssl_context",
    )

    def __init__(
        self,
        sock: socket.socket,
        conn_queue: queue.Queue[tuple[socket.socket, object]],
        *,
        shutdown_event: threading.Event | None = None,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._sock = sock
        self._conn_queue = conn_queue
        self._ext_shutdown = shutdown_event
        self._ssl_context = ssl_context
        self._logger = logging.getLogger("pounce.accept_distributor")

    def run(self) -> None:
        """Accept connections and enqueue for workers until shutdown."""
        self._sock.setblocking(True)
        accept_poll_interval = 0.25

        while not (self._ext_shutdown and self._ext_shutdown.is_set()):
            self._sock.settimeout(accept_poll_interval)
            try:
                conn, addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._ext_shutdown and self._ext_shutdown.is_set():
                    break
                raise

            conn.setblocking(True)
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if self._ssl_context:
                try:
                    conn = self._ssl_context.wrap_socket(conn, server_side=True)
                except ssl.SSLError:
                    conn.close()
                    continue

            self._conn_queue.put((conn, addr))
