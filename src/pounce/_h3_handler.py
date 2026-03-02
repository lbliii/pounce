"""
HTTP/3 connection handler — manages QUIC/UDP streams via aioquic.

HTTP/3 uses QUIC (UDP) transport. There is no TCP accept() loop — the
QuicConnectionProtocol receives datagrams and manages QUIC connections
internally. Each QUIC connection has an H3Connection; each HTTP/3 stream
maps to one ASGI invocation.

Requires the ``h3`` optional dependency (``pip install pounce[h3]``).

"""

import asyncio
import logging

from pounce._compression import Compressor, create_compressor, negotiate_encoding
from pounce._health import build_health_response
from pounce._request_id import extract_or_generate
from pounce._timing import ServerTiming, elapsed_ms, monotonic_ns
from pounce._types import ASGIApp
from pounce.asgi.bridge import SendState
from pounce.asgi.h3_bridge import build_h3_scope, create_h3_receive, create_h3_send
from pounce.config import ServerConfig
from pounce.logging import access_log
from pounce.protocols.h3 import is_h3_available


def _get_header_from_list(
    headers: list[tuple[bytes, bytes]] | tuple[tuple[bytes, bytes], ...],
    name: bytes,
) -> bytes | None:
    """Get a header value by lowercase name from a headers list."""
    name_lower = name.lower()
    for header_name, header_value in headers:
        if header_name.lower() == name_lower:
            return header_value
    return None


def _get_client_addr(quic: object) -> tuple[str, int]:
    """Extract client (host, port) from aioquic QuicConnection.

    Uses internal _network_paths when available; falls back to placeholder.
    """
    try:
        paths = getattr(quic, "_network_paths", None)
        if paths and len(paths) > 0:
            addr = paths[0].addr
            return (str(addr[0]), int(addr[1]))
    except AttributeError, IndexError, TypeError:
        pass
    return ("0.0.0.0", 0)


def _is_0rtt(quic: object) -> bool:
    """Check if the current QUIC connection is in 0-RTT mode.

    Returns False if we cannot determine (safe default — no replay rejection).
    """
    try:
        return bool(getattr(quic, "_is_0rtt", False))
    except AttributeError, TypeError:
        return False


def _create_h3_server_protocol(
    app: ASGIApp,
    config: ServerConfig,
    logger: logging.Logger,
    server: tuple[str, int],
) -> type:
    """Factory that returns an H3ServerProtocol class bound to app/config/logger/server."""

    from aioquic.asyncio import QuicConnectionProtocol
    from aioquic.h3.connection import H3Connection
    from aioquic.h3.events import DataReceived, HeadersReceived
    from aioquic.quic.events import ProtocolNegotiated, QuicEvent

    class H3ServerProtocol(QuicConnectionProtocol):
        """HTTP/3 server protocol using aioquic QuicConnectionProtocol."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self._http: H3Connection | None = None
            self._stream_tasks: dict[int, tuple[asyncio.Task[None], asyncio.Queue[dict]]] = {}
            self._stream_body_bytes: dict[int, int] = {}
            self._app = app
            self._config = config
            self._logger = logger
            self._server = server

        def quic_event_received(self, event: QuicEvent) -> None:
            if isinstance(event, ProtocolNegotiated):
                self._http = H3Connection(self._quic)

            if self._http is None:
                return

            for h3_event in self._http.handle_event(event):
                if isinstance(h3_event, HeadersReceived):
                    self._handle_headers(h3_event)
                elif isinstance(h3_event, DataReceived):
                    self._handle_data(h3_event)

        def _handle_headers(self, event: HeadersReceived) -> None:
            stream_id = event.stream_id
            if stream_id in self._stream_tasks:
                return

            client = _get_client_addr(self._quic)
            is_0rtt = _is_0rtt(self._quic)

            # Build scope from headers
            scope = build_h3_scope(
                list(event.headers),
                self._config,
                client,
                self._server,
                stream_id=stream_id,
                is_0rtt=is_0rtt,
            )

            method = scope["method"]

            # 0-RTT: reject non-idempotent methods (replay risk)
            if is_0rtt and method in {"POST", "PUT", "DELETE", "PATCH"}:
                self._http.send_headers(
                    stream_id=stream_id,
                    headers=[(b":status", b"425")],
                )
                self.transmit()
                return

            body_queue: asyncio.Queue[dict] = asyncio.Queue()
            task = asyncio.create_task(
                self._run_stream(stream_id, scope, body_queue),
            )
            self._stream_tasks[stream_id] = (task, body_queue)

            if event.stream_ended:
                body_queue.put_nowait(
                    {
                        "type": "http.request",
                        "body": b"",
                        "more_body": False,
                    }
                )

        def _handle_data(self, event: DataReceived) -> None:
            stream_id = event.stream_id
            pair = self._stream_tasks.get(stream_id)
            if pair is None:
                return

            _, body_queue = pair
            self._stream_body_bytes[stream_id] = self._stream_body_bytes.get(stream_id, 0) + len(
                event.data
            )

            if self._stream_body_bytes[stream_id] > self._config.max_request_size:
                self._logger.warning(
                    "H3 stream %d body exceeds max_request_size (%d bytes)",
                    stream_id,
                    self._config.max_request_size,
                )
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
                        "more_body": not event.stream_ended,
                    }
                )

        async def _run_stream(
            self,
            stream_id: int,
            scope: dict,
            body_queue: asyncio.Queue[dict],
        ) -> None:
            request_start = monotonic_ns()
            headers_tuples = tuple((n, v) for n, v in scope["headers"])
            is_trusted = bool(
                self._config.trusted_hosts
                and (
                    "*" in self._config.trusted_hosts
                    or scope["client"][0] in self._config.trusted_hosts
                )
            )
            request_id = extract_or_generate(
                headers_tuples,
                trusted=is_trusted,
            )
            scope.setdefault("extensions", {})["request_id"] = request_id

            # Built-in health check
            health_path = self._config.health_check_path
            if (
                health_path is not None
                and scope["path"] == health_path
                and scope["method"] == "GET"
            ):
                h_status, h_headers, h_body = build_health_response(
                    worker_id=0,
                    active_connections=0,
                )
                self._http.send_headers(
                    stream_id=stream_id,
                    headers=[(b":status", str(h_status).encode()), *h_headers],
                )
                self._http.send_data(stream_id=stream_id, data=h_body, end_stream=True)
                self.transmit()
                self._stream_tasks.pop(stream_id, None)
                self._stream_body_bytes.pop(stream_id, None)
                return

            timing: ServerTiming | None = None
            if self._config.server_timing:
                timing = ServerTiming()
                timing.add("parse", elapsed_ms(request_start))

            compressor: Compressor | None = None
            if self._config.compression:
                accept = _get_header_from_list(headers_tuples, b"accept-encoding")
                if accept:
                    enc = negotiate_encoding(accept)
                    if enc:
                        compressor = create_compressor(enc)

            receive = create_h3_receive(body_queue)
            app_start = monotonic_ns()
            send_state = SendState()
            send = create_h3_send(
                self._http,
                stream_id,
                self.transmit,
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
                try:
                    self._http.send_headers(
                        stream_id=stream_id,
                        headers=[
                            (b":status", b"500"),
                            (b"content-type", b"text/plain"),
                        ],
                    )
                    self._http.send_data(
                        stream_id=stream_id,
                        data=b"Internal Server Error",
                        end_stream=True,
                    )
                    self.transmit()
                except OSError, ConnectionError:
                    pass
                if send_state.status == 0:
                    send_state.status = 500
            finally:
                self._stream_tasks.pop(stream_id, None)
                self._stream_body_bytes.pop(stream_id, None)

            if timing:
                timing.add("app", elapsed_ms(app_start))

            if self._config.access_log:
                duration = elapsed_ms(request_start)
                target = scope.get("path", "/")
                log_filter = self._config.access_log_filter
                if log_filter is None or log_filter(
                    scope["method"],
                    target,
                    send_state.status,
                ):
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

    return H3ServerProtocol


def create_h3_protocol_factory(
    app: ASGIApp,
    config: ServerConfig,
    logger: logging.Logger,
    server: tuple[str, int],
) -> type:
    """Create an H3ServerProtocol factory for aioquic serve().

    Returns a class that aioquic can use as create_protocol=.
    """
    if not is_h3_available():
        msg = "aioquic not installed; install with pip install pounce[h3]"
        raise RuntimeError(msg)

    return _create_h3_server_protocol(app, config, logger, server)
