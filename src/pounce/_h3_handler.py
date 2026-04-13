"""
HTTP/3 connection handler — manages QUIC/UDP streams via zoomies.

HTTP/3 uses QUIC (UDP) transport. Zoomies is sans-I/O: pounce owns the
UDP loop and maintains a connection map. Each QUIC connection has an
H3Connection; each HTTP/3 stream maps to one ASGI invocation.

Requires the ``h3`` optional dependency (``pip install pounce[h3]``).

"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pounce._compression import Compressor, create_compressor, negotiate_encoding
from pounce._headers import get_header as _get_header_from_list
from pounce._health import build_health_response
from pounce._request_id import extract_or_generate
from pounce._timing import ServerTiming, elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.bridge import SendState
from pounce.asgi.h3_bridge import build_h3_scope, create_h3_receive, create_h3_send
from pounce.config import ServerConfig
from pounce.logging import access_log
from pounce.protocols.h3 import is_h3_available


class _PounceZeroRttPolicy:
    """0-RTT policy that accepts early data at the TLS level.

    Application-layer safety (425 Too Early for unsafe methods) is enforced
    separately in the H3 event handler.
    """

    def allow_0rtt(self, ticket_data: bytes, obfuscated_age: int) -> bool:
        return True


def _make_zero_rtt_policy() -> _PounceZeroRttPolicy:
    """Create a 0-RTT policy for use with QuicConfiguration."""
    return _PounceZeroRttPolicy()


@dataclass
class _ZoomiesConnection:
    """Per-client QUIC + H3 connection state."""

    quic: Any  # zoomies.core.QuicConnection
    h3: Any  # zoomies.h3.H3Connection
    last_activity: float = field(default_factory=time.monotonic)
    last_addr: tuple[str, int] = ("", 0)
    stream_tasks: dict[int, tuple[asyncio.Task[None], asyncio.Queue[dict[str, Any]]]] = field(
        default_factory=dict
    )
    stream_body_bytes: dict[int, int] = field(default_factory=dict)
    stream_body_ended: set[int] = field(default_factory=set)  # Streams that received terminal body


def _create_zoomies_datagram_protocol(
    app: ASGIApp,
    config: ServerConfig,
    logger: logging.Logger,
    server: tuple[str, int],
    quic_config: Any,  # zoomies.core.configuration.QuicConfiguration
) -> type:
    """Factory that returns a ZoomiesDatagramProtocol class bound to app/config/logger/server."""

    from zoomies.core import QuicConnection
    from zoomies.events import (
        ConnectionClosed,
        ConnectionIdIssued,
        ConnectionIdRetired,
        StopSendingReceived,
        StreamDataReceived,
        StreamReset,
        ZeroRttAccepted,
        ZeroRttRejected,
    )
    from zoomies.h3 import H3Connection
    from zoomies.packet import pull_destination_cid_for_routing

    class ZoomiesDatagramProtocol(asyncio.DatagramProtocol):
        """HTTP/3 datagram protocol using zoomies sans-I/O."""

        def __init__(self) -> None:
            self._app = app
            self._config = config
            self._logger = logger
            self._server = server
            self._quic_config = quic_config
            self._connections: dict[tuple[str, int], _ZoomiesConnection] = {}
            self._cid_to_conn: dict[bytes, _ZoomiesConnection] = {}
            self._transport: asyncio.DatagramTransport | None = None

        def connection_made(self, transport: asyncio.BaseTransport) -> None:
            assert isinstance(transport, asyncio.DatagramTransport)
            self._transport = transport

        def _prune_idle_connections(self) -> None:
            """Remove connections idle longer than http3_idle_timeout."""
            now = time.monotonic()
            timeout = self._config.http3_idle_timeout
            stale = [
                addr
                for addr, conn in self._connections.items()
                if now - conn.last_activity > timeout
            ]
            for addr in stale:
                conn = self._connections.pop(addr, None)
                if conn is not None:
                    for cid in conn.quic.our_cids:
                        self._cid_to_conn.pop(cid, None)

        def _remove_connection(self, conn: _ZoomiesConnection) -> None:
            """Remove connection from both addr and CID maps."""
            self._connections.pop(conn.last_addr, None)
            for cid in conn.quic.our_cids:
                self._cid_to_conn.pop(cid, None)

        def _route_connection(
            self, data: bytes, addr: tuple[str, int]
        ) -> _ZoomiesConnection | None:
            """Route packet to connection by CID or addr. Returns conn or None for new."""
            known_cids = tuple(c for c in self._cid_to_conn if c)
            dest_cid = pull_destination_cid_for_routing(data, known_cids=known_cids)
            if dest_cid:
                conn = self._cid_to_conn.get(dest_cid)
                if conn is not None:
                    if conn.last_addr != addr:
                        self._connections.pop(conn.last_addr, None)
                        conn.last_addr = addr
                        self._connections[addr] = conn
                    return conn
            return self._connections.get(addr)

        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            if self._transport is None:
                return

            self._prune_idle_connections()

            conn = self._route_connection(data, addr)
            if conn is None:
                # Enforce connection limit — silently drop to avoid amplification
                if len(self._connections) >= self._config.http3_max_connections:
                    self._logger.warning(
                        "H3 connection limit reached (%d), dropping packet from %s:%d",
                        self._config.http3_max_connections,
                        addr[0],
                        addr[1],
                    )
                    return

                quic = QuicConnection(self._quic_config)
                conn = _ZoomiesConnection(
                    quic=quic,
                    h3=H3Connection(
                        sender=quic,
                        qpack_max_table_capacity=config.http3_qpack_max_table_capacity,
                    ),
                    last_addr=addr,
                )
                self._connections[addr] = conn

            conn.last_activity = time.monotonic()
            events = conn.quic.datagram_received(data, addr)

            for cid in conn.quic.our_cids:
                if cid and cid not in self._cid_to_conn:
                    self._cid_to_conn[cid] = conn

            for event in events:
                if isinstance(event, ConnectionClosed):
                    self._cancel_all_streams(conn)
                    self._remove_connection(conn)
                    return
                if isinstance(event, ConnectionIdIssued):
                    self._cid_to_conn[event.connection_id] = conn
                elif isinstance(event, ConnectionIdRetired):
                    self._cid_to_conn.pop(event.connection_id, None)
                elif isinstance(event, (StreamReset, StopSendingReceived)):
                    self._cancel_stream(conn, event.stream_id)
                elif isinstance(event, ZeroRttAccepted):
                    self._logger.debug("0-RTT accepted for %s:%d", addr[0], addr[1])
                elif isinstance(event, ZeroRttRejected):
                    self._logger.debug("0-RTT rejected for %s:%d", addr[0], addr[1])
                elif isinstance(event, StreamDataReceived):
                    for h3_event in conn.h3.handle_event(event):
                        self._handle_h3_event(conn, h3_event, conn.last_addr)

            for dg in conn.quic.send_datagrams():
                self._transport.sendto(dg, conn.last_addr)

        def _handle_h3_event(
            self,
            conn: _ZoomiesConnection,
            event: Any,
            addr: tuple[str, int],
        ) -> None:
            from zoomies.events import H3DataReceived, H3HeadersReceived

            if isinstance(event, H3HeadersReceived):
                self._handle_headers(conn, event, addr)
            elif isinstance(event, H3DataReceived):
                self._handle_data(conn, event, addr)

        def _handle_headers(
            self,
            conn: _ZoomiesConnection,
            event: Any,
            addr: tuple[str, int],
        ) -> None:
            stream_id = event.stream_id
            if stream_id in conn.stream_tasks:
                return

            client = addr
            is_0rtt = event.is_0rtt

            scope = build_h3_scope(
                list(event.headers),
                self._config,
                client,
                self._server,
                stream_id=stream_id,
                is_0rtt=is_0rtt,
            )

            method = scope["method"]

            if is_0rtt and method in {"POST", "PUT", "DELETE", "PATCH"}:
                conn.h3.send_headers(
                    stream_id=stream_id,
                    headers=[(b":status", b"425")],
                )
                self._flush(conn, addr)
                return

            body_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            task = asyncio.create_task(
                self._run_stream(conn, stream_id, scope, body_queue, addr),
            )
            conn.stream_tasks[stream_id] = (task, body_queue)

            if event.end_stream:
                body_queue.put_nowait(
                    {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
                )

        def _handle_data(
            self,
            conn: _ZoomiesConnection,
            event: Any,
            addr: tuple[str, int],
        ) -> None:
            stream_id = event.stream_id
            pair = conn.stream_tasks.get(stream_id)
            if pair is None:
                return
            # Ignore further data after body was truncated or ended
            if stream_id in conn.stream_body_ended:
                return

            _, body_queue = pair
            conn.stream_body_bytes[stream_id] = conn.stream_body_bytes.get(stream_id, 0) + len(
                event.data
            )

            if conn.stream_body_bytes[stream_id] > self._config.max_request_size:
                self._logger.warning(
                    "H3 stream %d body exceeds max_request_size (%d bytes)",
                    stream_id,
                    self._config.max_request_size,
                )
                conn.stream_body_ended.add(stream_id)
                body_queue.put_nowait(
                    {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
                )
            else:
                body_queue.put_nowait(
                    {
                        "type": "http.request",
                        "body": event.data,
                        "more_body": not event.end_stream,
                    }
                )

        def _flush(self, conn: _ZoomiesConnection, addr: tuple[str, int]) -> None:
            """Send queued datagrams for this connection."""
            if self._transport is None:
                return
            for dg in conn.quic.send_datagrams():
                self._transport.sendto(dg, addr)

        def _cancel_stream(self, conn: _ZoomiesConnection, stream_id: int) -> None:
            """Cancel a stream task and clean up its state."""
            pair = conn.stream_tasks.pop(stream_id, None)
            if pair is not None:
                task, _ = pair
                task.cancel()
            conn.stream_body_bytes.pop(stream_id, None)
            conn.stream_body_ended.discard(stream_id)

        def _cancel_all_streams(self, conn: _ZoomiesConnection) -> None:
            """Cancel all stream tasks for a connection."""
            for stream_id in list(conn.stream_tasks):
                self._cancel_stream(conn, stream_id)

        def close_all_connections(self) -> None:
            """Gracefully close all active QUIC connections.

            Sends CONNECTION_CLOSE to each peer before the transport shuts down.
            Called by H3Worker during server shutdown.
            """
            for conn in list(self._connections.values()):
                self._cancel_all_streams(conn)
                try:
                    conn.quic.close(error_code=0, reason="Server shutting down")
                    if self._transport is not None:
                        for dg in conn.quic.send_datagrams():
                            self._transport.sendto(dg, conn.last_addr)
                except (OSError, ConnectionError):  # fmt: skip
                    pass
            self._connections.clear()
            self._cid_to_conn.clear()

        def _make_transmit(
            self, conn: _ZoomiesConnection, addr: tuple[str, int]
        ) -> Callable[[], None]:
            """Create transmit callback for create_h3_send."""

            def transmit() -> None:
                self._flush(conn, addr)

            return transmit

        def _prepare_stream(
            self,
            scope: dict[str, Any],
        ) -> tuple[str, tuple[tuple[bytes, bytes], ...], ServerTiming | None, Compressor | None]:
            """Extract request ID, negotiate timing/compression for a stream."""
            headers_tuples = tuple((n, v) for n, v in scope["headers"])
            is_trusted = bool(
                self._config.trusted_hosts
                and (
                    self._config.trusted_hosts_wildcard
                    or scope["client"][0] in self._config.trusted_hosts
                )
            )
            request_id = extract_or_generate(headers_tuples, trusted=is_trusted)
            scope.setdefault("extensions", {})["request_id"] = request_id

            timing: ServerTiming | None = None
            if self._config.server_timing:
                timing = ServerTiming()

            compressor: Compressor | None = None
            if self._config.compression:
                accept = _get_header_from_list(headers_tuples, b"accept-encoding")
                if accept:
                    enc = negotiate_encoding(accept)
                    if enc:
                        compressor = create_compressor(enc)

            return request_id, headers_tuples, timing, compressor

        def _send_error_response(
            self,
            conn: _ZoomiesConnection,
            stream_id: int,
            addr: tuple[str, int],
            send_state: SendState,
        ) -> None:
            """Send a 500 error response on the stream after an app exception."""
            try:
                conn.h3.send_headers(
                    stream_id=stream_id,
                    headers=[
                        (b":status", b"500"),
                        (b"content-type", b"text/plain"),
                    ],
                )
                conn.h3.send_data(
                    stream_id=stream_id,
                    data=b"Internal Server Error",
                    end_stream=True,
                )
                self._flush(conn, addr)
            except (OSError, ConnectionError):  # fmt: skip
                pass
            if send_state.status == 0:
                send_state.status = 500

        def _log_access(
            self,
            scope: dict[str, Any],
            send_state: SendState,
            request_start: int,
            request_id: str,
        ) -> None:
            """Log an access log entry for the completed stream."""
            if not self._config.access_log:
                return
            duration = elapsed_ms(request_start)
            target = scope.get("path", "/")
            log_filter = self._config.access_log_filter
            if log_filter is not None and not log_filter(
                scope["method"], target, send_state.status
            ):
                return
            client_str = f"{scope['client'][0]}:{scope['client'][1]}"
            access_log(
                scope["method"],
                target,
                send_state.status,
                send_state.bytes_sent,
                duration,
                client_str,
                http_version="3",
                request_id=request_id,
            )

        def _maybe_handle_health_check(
            self,
            conn: _ZoomiesConnection,
            stream_id: int,
            scope: dict[str, Any],
            addr: tuple[str, int],
        ) -> bool:
            """Handle health check request if applicable. Returns True if handled."""
            health_path = self._config.health_check_path
            if health_path is None or scope["path"] != health_path or scope["method"] != "GET":
                return False
            h_status, h_headers, h_body = build_health_response(
                worker_id=0,
                active_connections=0,
            )
            conn.h3.send_headers(
                stream_id=stream_id,
                headers=[(b":status", str(h_status).encode()), *h_headers],
            )
            conn.h3.send_data(stream_id=stream_id, data=h_body, end_stream=True)
            self._flush(conn, addr)
            conn.stream_tasks.pop(stream_id, None)
            conn.stream_body_bytes.pop(stream_id, None)
            return True

        async def _run_stream(
            self,
            conn: _ZoomiesConnection,
            stream_id: int,
            scope: dict[str, Any],
            body_queue: asyncio.Queue[dict[str, Any]],
            addr: tuple[str, int],
        ) -> None:
            request_start = monotonic_ns()
            request_id, _, timing, compressor = self._prepare_stream(scope)

            if self._maybe_handle_health_check(conn, stream_id, scope, addr):
                return

            if timing:
                timing.add("parse", elapsed_ms(request_start))

            receive = create_h3_receive(body_queue)
            app_start = monotonic_ns()
            send_state = SendState()
            send = create_h3_send(
                conn.h3,
                stream_id,
                self._make_transmit(conn, addr),
                send_state,
                timing=timing,
                compressor=compressor,
                request_method=scope["method"],
                request_id=request_id,
            )

            try:
                await self._app(scope, receive, send)
            except Exception:
                self._logger.exception(
                    "ASGI app error on H3 stream %d %s %s",
                    stream_id,
                    scope["method"],
                    scope["path"],
                )
                self._send_error_response(conn, stream_id, addr, send_state)
            finally:
                conn.stream_tasks.pop(stream_id, None)
                conn.stream_body_bytes.pop(stream_id, None)

            if timing:
                timing.add("app", elapsed_ms(app_start))

            self._log_access(scope, send_state, request_start, request_id)

    return ZoomiesDatagramProtocol


def create_zoomies_datagram_protocol_factory(
    app: ASGIApp,
    config: ServerConfig,
    logger: logging.Logger,
    server: tuple[str, int],
    quic_config: Any,
) -> Callable[[], asyncio.DatagramProtocol]:
    """Create a factory for ZoomiesDatagramProtocol.

    Returns a no-arg callable suitable for create_datagram_endpoint().
    """
    if not is_h3_available():
        msg = "zoomies not installed; install with pip install pounce[h3]"
        raise RuntimeError(msg)

    protocol_cls = _create_zoomies_datagram_protocol(app, config, logger, server, quic_config)

    def factory() -> asyncio.DatagramProtocol:
        return protocol_cls()

    return factory
