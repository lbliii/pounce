"""
HTTP/1.1 protocol handler — sans-I/O wrapper around h11.

Translates between raw bytes and typed ProtocolEvents. The worker feeds
bytes in via receive_data() and reads serialized bytes out via send_response()
and send_body().

All state is per-connection, per-request-cycle. No shared mutable state.

"""

from typing import Any, cast

import h11

from pounce._errors import ParseError
from pounce._sendfile import SendfileRegion
from pounce.protocols._base import (
    BodyReceived,
    ConnectionClosed,
    ProtocolEvent,
    RequestReceived,
)


class H1Protocol:
    """HTTP/1.1 protocol handler backed by h11.

    Implements the ProtocolHandler contract. Each instance manages a single
    TCP connection through one or more request-response cycles (keep-alive).

    Args:
        max_incomplete_event_size: Maximum bytes h11 will buffer for an
            incomplete event. None uses h11's default (16 KB).

    """

    __slots__ = ("_conn",)

    def __init__(self, *, max_incomplete_event_size: int | None = None) -> None:
        kwargs: dict[str, int] = {}
        if max_incomplete_event_size is not None:
            kwargs["max_incomplete_event_size"] = max_incomplete_event_size
        self._conn = h11.Connection(h11.SERVER, **kwargs)

    # -- ProtocolHandler interface ------------------------------------------

    def receive_data(self, data: bytes) -> list[ProtocolEvent]:
        """Feed raw bytes from the socket, return parsed protocol events.

        Args:
            data: Raw bytes received from the network.

        Returns:
            List of protocol events parsed from the input.

        Raises:
            ParseError: If h11 encounters malformed HTTP.
            LimitError: If the request exceeds configured size limits.

        """
        self._conn.receive_data(data)
        events: list[ProtocolEvent] = []

        while True:
            try:
                event = self._conn.next_event()
            except h11.RemoteProtocolError as exc:
                raise ParseError(str(exc), code="POUNCE_PARSE_H11_REJECTED") from exc

            if event is h11.NEED_DATA or event is h11.PAUSED:
                break

            match event:
                case h11.Request():
                    events.append(
                        RequestReceived(
                            method=event.method,
                            target=event.target,
                            headers=tuple((name, value) for name, value in event.headers),
                            http_version=event.http_version.decode("ascii"),
                        )
                    )
                case h11.Data():
                    events.append(BodyReceived(data=event.data, more=True))
                case h11.EndOfMessage():
                    events.append(BodyReceived(data=b"", more=False))
                case h11.ConnectionClosed():
                    events.append(ConnectionClosed(reason="client closed"))

        return events

    def send_response(self, status: int, headers: list[tuple[bytes, bytes]]) -> bytes:
        """Serialize a response status line and headers into bytes.

        Args:
            status: HTTP status code.
            headers: Response headers as (name, value) byte pairs.

        Returns:
            Serialized HTTP/1.1 response head bytes.

        """
        response = h11.Response(
            status_code=status,
            headers=headers,
        )
        return self._conn.send(response)

    def send_informational(self, status: int, headers: list[tuple[bytes, bytes]]) -> bytes:
        """Serialize a 1xx informational response (e.g. 103 Early Hints).

        h11 models interim 1xx responses as :class:`h11.InformationalResponse`,
        which does not terminate the request-response cycle, so the final
        response is still serialized and sent afterwards. Modern browsers
        (Chrome 103+, Firefox) honour 103 Early Hints over HTTP/1.1.

        Args:
            status: 1xx HTTP status code.
            headers: Response headers as (name, value) byte pairs.

        Returns:
            Serialized interim response head bytes.

        """
        return self._conn.send(h11.InformationalResponse(status_code=status, headers=headers))

    def send_100_continue(self) -> bytes:
        """Serialize an interim ``100 Continue`` informational response.

        A client that sends ``Expect: 100-continue`` withholds the request
        body until it observes this interim status line. h11 models it as an
        :class:`h11.InformationalResponse` (status 1xx), which does not
        terminate the request-response cycle, so the final response is still
        sent normally afterwards.

        Returns:
            Serialized ``HTTP/1.1 100 Continue`` head bytes.

        """
        return self._conn.send(
            h11.InformationalResponse(status_code=100, headers=[], reason=b"Continue")
        )

    def send_body(self, data: bytes, more: bool = False) -> bytes:
        """Serialize a response body chunk into bytes.

        Args:
            data: Body bytes to send.
            more: True if more body chunks will follow.

        Returns:
            Serialized bytes to write to the socket.

        """
        parts: list[bytes] = []
        if data:
            parts.append(self._conn.send(h11.Data(data=data)))
        if not more:
            parts.append(self._conn.send(h11.EndOfMessage()))
        return b"".join(parts)

    def send_body_parts(
        self,
        data: bytes | SendfileRegion,
        more: bool = False,
    ) -> list[bytes | SendfileRegion]:
        """Serialize a response body chunk without combining passthrough data.

        h11's ``send_with_data_passthrough`` preserves the exact object passed
        as ``Data.data`` while still applying Content-Length or chunked writer
        accounting. The ASGI bridge uses this for protocol-owned sendfile:
        h11 validates the declared body length, and the bridge writes any
        framing bytes around the file transfer in order.
        """
        parts: list[bytes | SendfileRegion] = []
        if data:
            data_parts = self._conn.send_with_data_passthrough(h11.Data(data=cast(Any, data)))
            if data_parts:
                parts.extend(cast("list[bytes | SendfileRegion]", data_parts))
        if not more:
            parts.append(self._conn.send(h11.EndOfMessage()))
        return parts

    def start_new_cycle(self) -> None:
        """Prepare for the next request on keep-alive connections."""
        self._conn.start_next_cycle()

    # -- Introspection ------------------------------------------------------

    @property
    def their_state(self) -> Any:
        """Current h11 client-side state (for diagnostics)."""
        return self._conn.their_state

    @property
    def our_state(self) -> Any:
        """Current h11 server-side state (for diagnostics)."""
        return self._conn.our_state

    @property
    def client_is_waiting_for_100_continue(self) -> bool:
        """True if the client sent Expect: 100-continue."""
        return self._conn.client_is_waiting_for_100_continue

    def has_pending_data(self) -> bool:
        """True if h11 has buffered data from a pipelined request.

        After ``start_new_cycle()``, h11 may still have unconsumed
        bytes from a previous read.  Check this before doing another
        socket read to avoid blocking when data is already available.

        """
        trailing, _ = self._conn.trailing_data
        return bool(trailing)
