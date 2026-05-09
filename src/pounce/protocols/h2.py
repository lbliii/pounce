"""
HTTP/2 connection handler — sans-I/O wrapper around the h2 library.

Unlike H1Protocol (which handles one request at a time), H2Connection
manages a multiplexed HTTP/2 connection with concurrent streams. Each
stream maps to one ASGI request.

The h2 library is a sans-I/O state machine: we feed it bytes and it
produces events and output bytes. H2Connection translates between h2's
internal events and pounce's typed ProtocolEvent system.

Requires the ``h2`` optional dependency (``pip install bengal-pounce[h2]``).

All state is per-connection. No shared mutable state.

"""

import logging
from dataclasses import dataclass

from pounce.protocols._base import (
    BodyReceived,
    RequestReceived,
)

try:
    import h2.config
    import h2.connection
    import h2.errors
    import h2.events
    import h2.exceptions

    _HAS_H2 = True
except ImportError:
    _HAS_H2 = False

logger = logging.getLogger("pounce.protocols.h2")


def is_h2_available() -> bool:
    """Check if h2 is installed."""
    return _HAS_H2


# ---------------------------------------------------------------------------
# H2 Stream Events — wrapper around pounce events with stream context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class H2RequestReceived:
    """HTTP/2 request headers received on a stream.

    Attributes:
        stream_id: The h2 stream identifier.
        request: The pounce RequestReceived event.
    """

    stream_id: int
    request: RequestReceived


@dataclass(frozen=True, slots=True)
class H2BodyReceived:
    """HTTP/2 request body data received on a stream.

    Attributes:
        stream_id: The h2 stream identifier.
        body: The pounce BodyReceived event.
    """

    stream_id: int
    body: BodyReceived


@dataclass(frozen=True, slots=True)
class H2StreamReset:
    """Client sent RST_STREAM — cancel the ASGI task for this stream.

    Attributes:
        stream_id: The h2 stream identifier.
        error_code: The h2 error code.
    """

    stream_id: int
    error_code: int


@dataclass(frozen=True, slots=True)
class H2GoAway:
    """Remote sent GOAWAY — no new streams, finish existing ones.

    Attributes:
        last_stream_id: The last stream the remote will process.
        error_code: The h2 error code.
    """

    last_stream_id: int
    error_code: int


@dataclass(frozen=True, slots=True)
class H2WindowUpdated:
    """Flow control window was updated — may resume sending.

    Attributes:
        stream_id: The stream id (0 = connection-level).
    """

    stream_id: int


@dataclass(frozen=True, slots=True)
class H2WebSocketRequest:
    """Extended CONNECT for WebSocket over HTTP/2 (RFC 8441).

    The client used ``:method = CONNECT`` with ``:protocol = websocket``
    to request a WebSocket stream within the HTTP/2 connection.

    Attributes:
        stream_id: The h2 stream identifier.
        request: The pounce RequestReceived event (method set to CONNECT).
        ws_path: The ``:path`` pseudo-header (WebSocket target).
    """

    stream_id: int
    request: RequestReceived
    ws_path: bytes


type H2Event = (
    H2RequestReceived
    | H2BodyReceived
    | H2StreamReset
    | H2GoAway
    | H2WindowUpdated
    | H2WebSocketRequest
)

# ---------------------------------------------------------------------------
# Per-stream state
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _StreamState:
    """Mutable per-stream tracking within an H2Connection."""

    headers_received: bool = False
    ended: bool = False


# ---------------------------------------------------------------------------
# H2Connection — manages the full HTTP/2 connection
# ---------------------------------------------------------------------------


class H2Connection:
    """HTTP/2 connection state machine backed by the h2 library.

    Sans-I/O: accepts raw bytes from the network and produces:
    1. H2 stream events for the worker to dispatch as ASGI scopes
    2. Raw bytes to write back to the network (flow control, acks, etc.)

    The worker is responsible for:
    - Calling ``receive_data(bytes)`` with incoming network data
    - Calling ``data_to_send()`` to get output bytes after each operation
    - Calling ``send_response_headers()`` / ``send_data()`` for responses
    - Managing per-stream ASGI tasks

    Args:
        client_side: If True, create a client-side connection (for testing).
            Default is False (server-side).

    Raises:
        RuntimeError: If h2 is not installed.

    """

    __slots__ = ("_closed", "_conn", "_streams")

    def __init__(self, *, client_side: bool = False) -> None:
        if not _HAS_H2:
            raise RuntimeError("HTTP/2 support requires h2. Install with: pip install bengal-pounce[h2]")
        config = h2.config.H2Configuration(
            client_side=client_side,
            header_encoding="utf-8",
        )
        self._conn = h2.connection.H2Connection(config=config)
        self._streams: dict[int, _StreamState] = {}
        self._closed = False

    def initiate_connection(self) -> None:
        """Send the HTTP/2 connection preface.

        Must be called once after creation. The output bytes (settings
        frame) should be written to the network via ``data_to_send()``.

        Enables RFC 8441 Extended CONNECT protocol for WebSocket over H2.

        """
        self._conn.initiate_connection()
        # Advertise Extended CONNECT support (RFC 8441)
        # SETTINGS_ENABLE_CONNECT_PROTOCOL = 0x8
        try:
            import h2.settings

            self._conn.update_settings(
                {
                    h2.settings.SettingCodes.ENABLE_CONNECT_PROTOCOL: 1,
                }
            )
        except (AttributeError, ValueError):  # fmt: skip  # older h2 versions may lack this setting
            pass

    def receive_data(self, data: bytes) -> list[H2Event]:
        """Feed raw bytes from the network and return h2 stream events.

        After calling this, always call ``data_to_send()`` to get any
        pending output (e.g., window updates, settings acks).

        Args:
            data: Raw bytes from the network.

        Returns:
            List of typed H2 events for the worker to process.

        """
        h2_events = self._conn.receive_data(data)
        pounce_events: list[H2Event] = []

        for event in h2_events:
            if isinstance(event, h2.events.RequestReceived):
                stream_id = event.stream_id
                self._streams[stream_id] = _StreamState()

                # Convert h2 headers to pounce format
                method: bytes | None = None
                target: bytes | None = None
                scheme: bytes | None = None
                authority: bytes | None = None
                h2_protocol = b""
                headers_list: list[tuple[bytes, bytes]] = []
                host: bytes | None = None
                seen_pseudo_headers: set[bytes] = set()
                malformed = False

                for name, value in event.headers:
                    name_bytes = name.encode() if isinstance(name, str) else name
                    value_bytes = value.encode() if isinstance(value, str) else value

                    if name_bytes.startswith(b":"):
                        if name_bytes in seen_pseudo_headers:
                            malformed = True
                            break
                        seen_pseudo_headers.add(name_bytes)

                    if name_bytes == b":method":
                        method = value_bytes
                    elif name_bytes == b":path":
                        target = value_bytes
                    elif name_bytes == b":authority":
                        authority = value_bytes
                    elif name_bytes == b":scheme":
                        scheme = value_bytes
                    elif name_bytes == b":protocol":
                        h2_protocol = value_bytes
                    elif not name_bytes.startswith(b":"):
                        headers_list.append((name_bytes, value_bytes))
                        if name_bytes.lower() == b"host":
                            host = value_bytes

                if authority is not None and host is not None and authority != host:
                    malformed = True
                effective_authority = authority or host
                if (
                    malformed
                    or method is None
                    or target is None
                    or scheme is None
                    or effective_authority is None
                ):
                    logger.warning("Rejecting malformed H2 request pseudo-headers")
                    self.reset_stream(
                        stream_id,
                        error_code=int(h2.errors.ErrorCodes.PROTOCOL_ERROR),
                    )
                    pounce_events.append(
                        H2StreamReset(
                            stream_id=stream_id,
                            error_code=int(h2.errors.ErrorCodes.PROTOCOL_ERROR),
                        )
                    )
                    continue

                # Add host header from :authority if not present
                if host is None:
                    headers_list.insert(0, (b"host", effective_authority))

                request = RequestReceived(
                    method=method,
                    target=target,
                    headers=tuple(headers_list),
                    http_version="2",
                )
                self._streams[stream_id].headers_received = True

                # RFC 8441: Extended CONNECT with :protocol = websocket
                if method == b"CONNECT" and h2_protocol == b"websocket":
                    pounce_events.append(
                        H2WebSocketRequest(
                            stream_id=stream_id,
                            request=request,
                            ws_path=target,
                        )
                    )
                else:
                    pounce_events.append(
                        H2RequestReceived(
                            stream_id=stream_id,
                            request=request,
                        )
                    )

                    # If stream ended with headers (GET, HEAD, etc.)
                    if event.stream_ended is not None:
                        self._streams[stream_id].ended = True
                        pounce_events.append(
                            H2BodyReceived(
                                stream_id=stream_id,
                                body=BodyReceived(data=b"", more=False),
                            )
                        )

            elif isinstance(event, h2.events.DataReceived):
                stream_id = event.stream_id
                # Acknowledge the data for flow control
                self._conn.acknowledge_received_data(
                    event.flow_controlled_length,
                    stream_id,
                )
                more = event.stream_ended is None
                pounce_events.append(
                    H2BodyReceived(
                        stream_id=stream_id,
                        body=BodyReceived(data=event.data, more=more),
                    )
                )
                if not more and stream_id in self._streams:
                    self._streams[stream_id].ended = True

            elif isinstance(event, h2.events.StreamEnded):
                stream_id = event.stream_id
                if stream_id in self._streams:
                    state = self._streams[stream_id]
                    if not state.ended:
                        state.ended = True
                        pounce_events.append(
                            H2BodyReceived(
                                stream_id=stream_id,
                                body=BodyReceived(data=b"", more=False),
                            )
                        )

            elif isinstance(event, h2.events.StreamReset):
                stream_id = event.stream_id
                pounce_events.append(
                    H2StreamReset(
                        stream_id=stream_id,
                        error_code=event.error_code,
                    )
                )
                self._streams.pop(stream_id, None)

            elif isinstance(event, h2.events.WindowUpdated):
                pounce_events.append(
                    H2WindowUpdated(
                        stream_id=event.stream_id,
                    )
                )

            elif isinstance(event, h2.events.RemoteSettingsChanged):
                # Settings are handled automatically by h2
                pass

            elif isinstance(event, h2.events.ConnectionTerminated):
                self._closed = True
                pounce_events.append(
                    H2GoAway(
                        last_stream_id=event.last_stream_id or 0,
                        error_code=event.error_code or 0,
                    )
                )

        return pounce_events

    # -- Send methods -------------------------------------------------------

    def send_response_headers(
        self,
        stream_id: int,
        status: int,
        headers: list[tuple[bytes, bytes]],
        *,
        end_stream: bool = False,
    ) -> None:
        """Send response headers on a stream.

        Args:
            stream_id: The h2 stream identifier.
            status: HTTP status code.
            headers: Response headers as (name, value) byte pairs.
            end_stream: If True, close the stream after headers.

        """
        h2_headers: list[tuple[str, str]] = [
            (":status", str(status)),
        ]
        for name, value in headers:
            h2_headers.append(
                (
                    name.decode("ascii", errors="replace"),
                    value.decode("ascii", errors="replace"),
                )
            )

        self._conn.send_headers(
            stream_id,
            h2_headers,
            end_stream=end_stream,
        )

    def send_data(
        self,
        stream_id: int,
        data: bytes,
        *,
        end_stream: bool = False,
    ) -> None:
        """Send response body data on a stream.

        Respects h2 flow control: only sends up to the available window
        size. Returns the amount actually sent; the caller should retry
        with remaining data after a WindowUpdated event.

        Args:
            stream_id: The h2 stream identifier.
            data: Response body bytes to send.
            end_stream: If True, close the stream after this data.

        """
        if data:
            self._conn.send_data(stream_id, data, end_stream=end_stream)
        elif end_stream:
            self._conn.send_data(stream_id, b"", end_stream=True)

    def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """Send RST_STREAM to cancel a stream.

        Args:
            stream_id: The stream to reset.
            error_code: The h2 error code (default: NO_ERROR).

        """
        self._conn.reset_stream(stream_id, error_code=error_code)
        self._streams.pop(stream_id, None)

    def close_connection(self, error_code: int = 0) -> None:
        """Send GOAWAY to gracefully shut down the connection.

        Args:
            error_code: The h2 error code (default: NO_ERROR).

        """
        self._conn.close_connection(error_code=error_code)
        self._closed = True

    def data_to_send(self) -> bytes:
        """Get pending output bytes to write to the network.

        Must be called after every ``receive_data()``, ``send_*()`` call.

        Returns:
            Bytes to write to the socket. May be empty.

        """
        return self._conn.data_to_send()

    def local_flow_control_window(self, stream_id: int) -> int:
        """Check available flow control window for a stream.

        Args:
            stream_id: The stream to check.

        Returns:
            Number of bytes that can be sent without blocking.

        """
        return self._conn.local_flow_control_window(stream_id)

    # -- Properties ---------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        """True if the connection has received GOAWAY."""
        return self._closed

    @property
    def active_stream_count(self) -> int:
        """Number of currently active streams."""
        return len(self._streams)

    def stream_ended(self, stream_id: int) -> bool:
        """Check if a stream's request is complete."""
        state = self._streams.get(stream_id)
        return state is not None and state.ended

    def remove_stream(self, stream_id: int) -> None:
        """Remove a stream from tracking (after ASGI task completes)."""
        self._streams.pop(stream_id, None)
