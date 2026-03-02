"""
HTTP/3 worker — runs QUIC/UDP datagram endpoint for HTTP/3.

Uses asyncio.create_datagram_endpoint with a pre-bound UDP socket.
Shares app, config, lifecycle with the TCP Worker model but serves
HTTP/3 over QUIC instead of HTTP/1.1 and HTTP/2 over TCP.

Requires the ``h3`` optional dependency (``pip install pounce[h3]``).

"""

import asyncio
import logging
import socket
import threading

from pounce._types import ASGIApp
from pounce.config import ServerConfig
from pounce.protocols.h3 import is_h3_available


class H3Worker:
    """Single-threaded async worker that serves HTTP/3 over QUIC/UDP.

    Uses create_datagram_endpoint with a pre-bound UDP socket.
    aioquic's QuicServer dispatches datagrams to per-connection
    H3ServerProtocol instances.

    Args:
        config: Server configuration.
        app: The ASGI application.
        sock: A bound UDP socket.
        worker_id: Numeric identifier for log differentiation.
        shutdown_event: Optional external threading.Event for shutdown.
        ssl_certfile: Path to TLS certificate (required for QUIC).
        ssl_keyfile: Path to TLS private key (required for QUIC).

    """

    __slots__ = (
        "_app",
        "_config",
        "_ext_shutdown",
        "_async_shutdown",
        "_logger",
        "_loop",
        "_sock",
        "_worker_id",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        sock: socket.socket,
        *,
        worker_id: int = 0,
        shutdown_event: threading.Event | None = None,
        ssl_certfile: str,
        ssl_keyfile: str,
    ) -> None:
        self._config = config
        self._app = app
        self._sock = sock
        self._worker_id = worker_id
        self._ext_shutdown = shutdown_event
        self._async_shutdown: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._logger = logging.getLogger(f"pounce.h3_worker.{worker_id}")

    def run(self) -> None:
        """Start the H3 worker's event loop (blocking)."""
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        """Run the QUIC datagram endpoint until shutdown."""
        if not is_h3_available():
            self._logger.error("aioquic not installed; HTTP/3 disabled")
            return

        from aioquic.asyncio.server import QuicServer
        from aioquic.quic.configuration import QuicConfiguration

        from pounce._h3_handler import create_h3_protocol_factory

        self._loop = asyncio.get_running_loop()
        self._async_shutdown = asyncio.Event()

        # Bridge external shutdown event
        bridge_task: asyncio.Task[None] | None = None
        if self._ext_shutdown is not None:
            bridge_task = asyncio.create_task(
                self._bridge_shutdown(self._ext_shutdown),
            )

        configuration = QuicConfiguration(
            is_client=False,
            alpn_protocols=["h3"],
            max_idle_timeout=self._config.http3_idle_timeout,
        )
        configuration.load_cert_chain(
            self._config.ssl_certfile or "",
            self._config.ssl_keyfile or "",
        )

        server_addr = self._sock.getsockname()
        server = (str(server_addr[0]), int(server_addr[1]))

        protocol_factory = create_h3_protocol_factory(
            self._app,
            self._config,
            self._logger,
            server,
        )

        transport, quic_server = await self._loop.create_datagram_endpoint(
            lambda: QuicServer(
                configuration=configuration,
                create_protocol=protocol_factory,
            ),
            sock=self._sock,
        )

        self._logger.debug(
            "H3 worker %d started on %s:%d",
            self._worker_id,
            server[0],
            server[1],
        )

        try:
            await self._async_shutdown.wait()
        finally:
            if bridge_task is not None:
                bridge_task.cancel()
            quic_server.close()
            transport.close()
            self._logger.info("H3 worker %d stopped", self._worker_id)

    async def _bridge_shutdown(self, ext_event: threading.Event) -> None:
        """Poll external shutdown event and set async shutdown."""
        while not ext_event.is_set():
            await asyncio.sleep(0.25)
        if self._async_shutdown is not None:
            self._loop.call_soon(self._async_shutdown.set)

    def shutdown(self) -> None:
        """Signal the worker to stop."""
        if self._ext_shutdown is not None:
            self._ext_shutdown.set()
        elif self._async_shutdown is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._async_shutdown.set)
