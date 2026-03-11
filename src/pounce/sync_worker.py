"""
SyncWorker — blocking I/O worker for request-response workloads.

One request at a time per thread, no asyncio. On 3.14t, runs in a thread
with true parallelism. Handles HTTP/1.1 keep-alive in a tight recv/send loop.

When the ASGI app returns a streaming response (more_body=True) or WebSocket
upgrade, raises NeedsAsync — the supervisor hands off to the async pool
(Phase 2). For Phase 1, streaming requests receive 501 Not Implemented.

"""

import asyncio
import contextlib
import logging
import queue
import socket
import ssl
import threading
from typing import Any, cast

from pounce._compression import Compressor, create_compressor, negotiate_encoding
from pounce._errors import ParseError
from pounce._health import build_health_response
from pounce._request_id import extract_or_generate
from pounce._types import ASGIApp
from pounce.sync_protocol import RawRequest, RawResponse, SyncApp
from pounce.asgi.bridge import build_scope
from pounce.asgi.sync_bridge import NeedsAsync, call_asgi_sync
from pounce.async_pool import AsyncPool, StreamingHandoff, WebSocketHandoff
from pounce.config import ServerConfig
from pounce.lifecycle import (
    ClientDisconnected,
    ConnectionOpened,
    LifecycleCollector,
    NoopCollector,
    RequestStarted,
    ResponseCompleted,
    next_connection_id,
)
from pounce.lifecycle import ConnectionClosed as LifecycleConnectionClosed
from pounce.lifecycle import monotonic_ns as lifecycle_ns
from pounce.logging import access_log
from pounce.protocols._base import BodyReceived, ConnectionClosed, RequestReceived
from pounce.protocols.h1 import H1Protocol

try:
    from pounce.protocols.h1_httptools import is_httptools_available

    _use_httptools = is_httptools_available()
except ImportError:
    _use_httptools = False


def _create_h1_protocol(
    *,
    max_incomplete_event_size: int | None = None,
) -> H1Protocol:
    """Create the best available HTTP/1.1 protocol handler."""
    if _use_httptools:
        from pounce.protocols.h1_httptools import H1HttpToolsProtocol

        return H1HttpToolsProtocol(  # type: ignore[return-value]
            max_incomplete_event_size=max_incomplete_event_size,
        )
    return H1Protocol(max_incomplete_event_size=max_incomplete_event_size)


def _get_header(headers: tuple[tuple[bytes, bytes], ...], name: bytes) -> bytes | None:
    """Get a header value by lowercase name."""
    name_lower = name.lower()
    for hname, hvalue in headers:
        if hname.lower() == name_lower:
            return hvalue
    return None


def _is_websocket_upgrade(request: RequestReceived) -> bool:
    """Check if the request is a WebSocket upgrade."""
    has_upgrade = False
    has_websocket = False
    for name, value in request.headers:
        if name.lower() == b"connection" and b"upgrade" in value.lower():
            has_upgrade = True
        elif name.lower() == b"upgrade" and value.lower() == b"websocket":
            has_websocket = True
    return has_upgrade and has_websocket


class SyncWorker:
    """Blocking I/O worker — one request at a time, no event loop.

    On 3.14t, runs in a thread with true parallelism. Handles HTTP/1.1
    keep-alive in a tight recv/send loop. Streaming and WebSocket require
    handoff to the async pool (Phase 2).

    """

    __slots__ = (
        "_active_connections",
        "_app",
        "_async_pool",
        "_config",
        "_conn_queue",
        "_ext_shutdown",
        "_lifecycle",
        "_logger",
        "_lifespan_state",
        "_sock",
        "_ssl_context",
        "_sync_app",
        "_worker_id",
    )

    def __init__(
        self,
        config: ServerConfig,
        app: ASGIApp,
        sock: socket.socket | None,
        *,
        worker_id: int = 0,
        shutdown_event: threading.Event | None = None,
        ssl_context: ssl.SSLContext | None = None,
        lifecycle_collector: LifecycleCollector | None = None,
        async_pool: AsyncPool | None = None,
        conn_queue: queue.Queue[tuple[socket.socket, object]] | None = None,
        sync_app: SyncApp | None = None,
    ) -> None:
        self._config = config
        self._async_pool = async_pool
        self._conn_queue = conn_queue
        self._sync_app = sync_app
        if config.middleware:
            from pounce._middleware import MiddlewareStack

            self._app = MiddlewareStack(config.middleware, app)
        else:
            self._app = app
        self._sock = sock
        self._worker_id = worker_id
        self._ext_shutdown = shutdown_event
        self._ssl_context = ssl_context
        self._lifecycle: LifecycleCollector = lifecycle_collector or NoopCollector()
        self._lifespan_state: dict[str, Any] = {}
        self._logger = logging.getLogger(f"pounce.sync_worker.{worker_id}")
        self._active_connections = 0

    def set_lifespan_state(self, state: dict[str, Any]) -> None:
        """Set the lifespan state dict shared with all requests."""
        self._lifespan_state = state

    def start_draining(self) -> None:
        """Mark this worker as draining (stop accepting new connections)."""
        if self._ext_shutdown:
            self._ext_shutdown.set()

    def is_idle(self) -> bool:
        """True if no connection is currently being handled."""
        return self._active_connections == 0

    def run(self) -> None:
        """Accept connections until shutdown (blocking)."""
        _POLL_INTERVAL = 0.25

        runner = asyncio.Runner()
        try:
            if self._conn_queue is not None:
                self._run_from_queue(_POLL_INTERVAL, runner)
            else:
                self._run_accept_loop(_POLL_INTERVAL, runner)
        finally:
            runner.close()

    def _run_from_queue(self, poll_interval: float, runner: asyncio.Runner) -> None:
        """Get connections from distributor queue (no thundering herd)."""
        assert self._conn_queue is not None
        while not (self._ext_shutdown and self._ext_shutdown.is_set()):
            try:
                conn, addr = self._conn_queue.get(timeout=poll_interval)
            except queue.Empty:
                continue
            self._handle_connection(conn, addr, runner)

    def _run_accept_loop(self, poll_interval: float, runner: asyncio.Runner) -> None:
        """Accept directly from socket (SO_REUSEPORT or single worker)."""
        assert self._sock is not None
        self._sock.setblocking(True)
        while not (self._ext_shutdown and self._ext_shutdown.is_set()):
            self._sock.settimeout(poll_interval)
            try:
                conn, addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._ext_shutdown and self._ext_shutdown.is_set():
                    break
                raise
            conn.setblocking(True)
            if self._ssl_context:
                try:
                    conn = self._ssl_context.wrap_socket(conn, server_side=True)
                except ssl.SSLError:
                    conn.close()
                    continue
            self._handle_connection(conn, addr, runner)

    def _handle_connection(
        self, conn: socket.socket, addr: tuple[str, int], runner: asyncio.Runner
    ) -> None:
        """Handle a single TCP connection through request-response cycles."""
        self._active_connections += 1
        handed_off = False
        try:
            handed_off = self._handle_connection_impl(conn, addr, runner)
        finally:
            self._active_connections -= 1
            if not handed_off:
                with contextlib.suppress(OSError, ConnectionError):
                    conn.close()

    def _handle_connection_impl(
        self, conn: socket.socket, addr: tuple[str, int], runner: asyncio.Runner
    ) -> bool:
        """Inner connection handling. Returns True if handed off to async pool."""
        conn.settimeout(self._config.header_timeout)
        try:
            peername = conn.getpeername()
            client = (str(peername[0]), int(peername[1])) if len(peername) >= 2 else ("unix", 0)
        except OSError:
            client = ("unknown", 0)
        client_str = f"{client[0]}:{client[1]}"
        try:
            sockname = conn.getsockname()
            server = (str(sockname[0]), int(sockname[1])) if len(sockname) >= 2 else (self._config.host, self._config.port)
        except OSError:
            server = (self._config.host, self._config.port)
        if self._config.uds:
            server = (self._config.uds, 0)
        conn_id = next_connection_id()
        conn_start = lifecycle_ns()

        self._lifecycle.record(
            ConnectionOpened(
                connection_id=conn_id,
                worker_id=self._worker_id,
                client_addr=client[0],
                client_port=client[1],
                server_addr=server[0],
                server_port=server[1],
                protocol="h1",
                timestamp_ns=conn_start,
            )
        )

        proto = _create_h1_protocol(
            max_incomplete_event_size=self._config.h11_max_incomplete_event_size,
        )
        request_count = 0
        max_requests = self._config.max_requests_per_connection

        try:
            while True:
                if self._ext_shutdown and self._ext_shutdown.is_set():
                    break
                conn.settimeout(self._config.header_timeout)
                request, body = self._recv_request(conn, proto)
                if request is None:
                    break

                request_count += 1
                self._lifecycle.record(
                    RequestStarted(
                        connection_id=conn_id,
                        worker_id=self._worker_id,
                        method=request.method.decode("ascii", errors="replace"),
                        path=request.target.decode("ascii", errors="replace"),
                        http_version=request.http_version,
                        timestamp_ns=lifecycle_ns(),
                    )
                )

                if _is_websocket_upgrade(request):
                    if self._async_pool:
                        self._async_pool.accept_handoff(
                            WebSocketHandoff(
                                conn=conn,
                                request=request,
                                client=client,
                                server=server,
                                scope={},
                            )
                        )
                        return True
                    self._send_error(conn, proto, 501, "WebSocket requires worker_mode=async")
                    break

                # Fused sync path: try SyncApp.handle_sync() before ASGI
                if self._sync_app is not None:
                    target = request.target
                    path_bytes = target.split(b"?", 1)[0] if b"?" in target else target
                    query_bytes = target.split(b"?", 1)[1] if b"?" in target else b""
                    raw_req = RawRequest(
                        method=request.method,
                        path=path_bytes,
                        query_string=query_bytes,
                        headers=request.headers,
                        body=body,
                        client=client,
                        server=server,
                        http_version=request.http_version,
                    )
                    raw_resp = self._sync_app.handle_sync(raw_req)
                    if raw_resp is not None:
                        # Fast path: direct response, no asyncio
                        headers_list = list(raw_resp.headers)
                        compressor: Compressor | None = None
                        if self._config.compression:
                            accept_enc = _get_header(request.headers, b"accept-encoding")
                            if accept_enc:
                                enc = negotiate_encoding(accept_enc)
                                if enc:
                                    compressor = create_compressor(enc)
                        if compressor and len(raw_resp.body) >= self._config.compression_min_size:
                            body_out = compressor.compress(raw_resp.body) + compressor.flush()
                            headers_list.append(
                                (b"content-encoding", compressor.encoding.encode("ascii"))
                            )
                            headers_list = [(n, v) for n, v in headers_list if n.lower() != b"content-length"]
                        else:
                            body_out = raw_resp.body
                        if not any(n.lower() == b"content-length" for n, _ in headers_list):
                            headers_list.append(
                                (b"content-length", str(len(body_out)).encode("ascii"))
                            )
                        headers_list.append((b"connection", b"close"))
                        raw = proto.send_response(raw_resp.status, headers_list)
                        raw += proto.send_body(body_out, more=False)
                        conn.sendall(raw)
                        self._lifecycle.record(
                            ResponseCompleted(
                                connection_id=conn_id,
                                worker_id=self._worker_id,
                                status=raw_resp.status,
                                bytes_sent=len(body_out),
                                duration_ms=0,
                                timestamp_ns=lifecycle_ns(),
                            )
                        )
                        if self._config.access_log:
                            access_log(
                                request.method.decode("ascii", errors="replace"),
                                path_bytes.decode("ascii", errors="replace"),
                                raw_resp.status,
                                len(body_out),
                                0,
                                client_str,
                            )
                        if max_requests > 0 and request_count >= max_requests:
                            break
                        break

                scope = build_scope(request, self._config, client, server, state=self._lifespan_state)
                is_trusted = bool(
                    self._config.trusted_hosts
                    and ("*" in self._config.trusted_hosts or client[0] in self._config.trusted_hosts)
                )
                request_id = extract_or_generate(request.headers, trusted=is_trusted)
                extensions = scope.setdefault("extensions", {})
                extensions["request_id"] = request_id
                extensions["pounce.inline_sync"] = True

                if (
                    self._config.health_check_path
                    and scope["path"] == self._config.health_check_path
                    and request.method == b"GET"
                ):
                    status, health_headers, body_bytes = build_health_response(
                        worker_id=self._worker_id,
                        active_connections=1,
                    )
                    health_headers = list(health_headers) + [(b"connection", b"close")]
                    raw = proto.send_response(status, health_headers)
                    raw += proto.send_body(body_bytes, more=False)
                    conn.sendall(raw)
                    self._lifecycle.record(
                        ResponseCompleted(
                            connection_id=conn_id,
                            worker_id=self._worker_id,
                            status=status,
                            bytes_sent=len(body_bytes),
                            duration_ms=0,
                            timestamp_ns=lifecycle_ns(),
                        )
                    )
                    if self._config.access_log:
                        access_log("GET", scope["path"], status, len(body_bytes), 0, client_str)
                else:
                    try:
                        response = call_asgi_sync(
                            cast(ASGIApp, self._app),
                            scope,
                            body,
                            runner=runner,
                        )
                    except NeedsAsync:
                        if self._async_pool:
                            self._async_pool.accept_handoff(
                                StreamingHandoff(
                                    conn=conn,
                                    scope=scope,
                                    body=body,
                                    request_id=request_id,
                                )
                            )
                            return True
                        self._send_error(
                            conn,
                            proto,
                            501,
                            "Streaming responses require worker_mode=async or handoff",
                        )
                        break
                    except Exception:
                        self._logger.exception("ASGI app error")
                        self._send_error(conn, proto, 500, "Internal Server Error")
                        break

                    compressor: Compressor | None = None
                    if self._config.compression:
                        accept_enc = _get_header(request.headers, b"accept-encoding")
                        if accept_enc:
                            enc = negotiate_encoding(accept_enc)
                            if enc:
                                compressor = create_compressor(enc)

                    headers = list(response.headers)
                    if compressor:
                        body_out = compressor.compress(response.body) + compressor.flush()
                        headers.append((b"content-encoding", compressor.encoding.encode("ascii")))
                        headers = [(n, v) for n, v in headers if n.lower() != b"content-length"]
                    else:
                        body_out = response.body

                    if not any(n.lower() == b"content-length" for n, _ in headers):
                        headers.append(
                            (b"content-length", str(len(body_out)).encode("ascii"))
                        )
                    if request_id:
                        headers.append((b"x-request-id", request_id.encode("latin-1")))
                    headers.append((b"connection", b"close"))

                    raw = proto.send_response(response.status, headers)
                    raw += proto.send_body(body_out, more=False)
                    conn.sendall(raw)

                    self._lifecycle.record(
                        ResponseCompleted(
                            connection_id=conn_id,
                            worker_id=self._worker_id,
                            status=response.status,
                            bytes_sent=len(body_out),
                            duration_ms=0,
                            timestamp_ns=lifecycle_ns(),
                        )
                    )
                    if self._config.access_log:
                        access_log(
                            scope["method"],
                            scope["path"],
                            response.status,
                            len(body_out),
                            0,
                            client_str,
                        )

                if max_requests > 0 and request_count >= max_requests:
                    break

                # Sync workers: one request per connection (like Gunicorn sync).
                # Keep-alive wastes worker time waiting for the next request
                # while other accepted connections queue. Closing lets the
                # worker immediately serve the next waiting connection.
                break
        except (ConnectionError, OSError):
            self._lifecycle.record(
                ClientDisconnected(
                    connection_id=conn_id,
                    worker_id=self._worker_id,
                    during_streaming=False,
                    timestamp_ns=lifecycle_ns(),
                )
            )
        finally:
            self._lifecycle.record(
                LifecycleConnectionClosed(
                    connection_id=conn_id,
                    worker_id=self._worker_id,
                    requests_served=request_count,
                    total_bytes_sent=0,
                    duration_ms=round((lifecycle_ns() - conn_start) / 1_000_000, 1),
                    reason="complete",
                    timestamp_ns=lifecycle_ns(),
                )
            )
        return False

    def _recv_request(
        self, conn: socket.socket, proto: H1Protocol
    ) -> tuple[RequestReceived | None, bytes]:
        """Read until we have a complete HTTP request. Returns (request, body) or (None, b"")."""
        body_parts: list[bytes] = []
        received_request: RequestReceived | None = None

        while True:
            try:
                chunk = conn.recv(65536)
            except (ConnectionError, OSError, TimeoutError):
                return (None, b"")

            if not chunk:
                return (None, b"")

            try:
                events = proto.receive_data(chunk)
            except ParseError:
                return (None, b"")

            for event in events:
                if isinstance(event, RequestReceived):
                    received_request = event
                elif isinstance(event, BodyReceived):
                    body_parts.append(event.data)
                    if not event.more:
                        return (received_request or None, b"".join(body_parts))
                elif isinstance(event, ConnectionClosed):
                    return (None, b"")

            if received_request and not body_parts:
                content_length = _get_header(received_request.headers, b"content-length")
                if content_length is None or content_length == b"0":
                    return (received_request, b"")
                transfer_encoding = _get_header(received_request.headers, b"transfer-encoding")
                if transfer_encoding and b"chunked" in transfer_encoding.lower():
                    pass
                else:
                    try:
                        cl = int(content_length)
                        if cl == 0:
                            return (received_request, b"")
                    except ValueError:
                        pass

    def _send_error(
        self, conn: socket.socket, proto: H1Protocol, status: int, message: str
    ) -> None:
        """Send a plain-text error response."""
        body = message.encode("utf-8")
        try:
            raw = proto.send_response(
                status,
                [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"connection", b"close"),
                ],
            )
            raw += proto.send_body(body, more=False)
            conn.sendall(raw)
        except (OSError, ConnectionError):
            pass
