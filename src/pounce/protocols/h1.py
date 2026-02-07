"""
HTTP/1.1 protocol handler — sans-I/O wrapper around h11.

Translates between raw bytes and typed ProtocolEvents. The worker feeds
bytes in via receive_data() and reads serialized bytes out via send_response()
and send_body().

All state is per-connection, per-request-cycle. No shared mutable state.

"""

from __future__ import annotations

import h11

from pounce._errors import LimitError, ParseError
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
                raise ParseError(str(exc)) from exc

            if event is h11.NEED_DATA or event is h11.PAUSED:
                break

            if isinstance(event, h11.Request):
                events.append(
                    RequestReceived(
                        method=event.method,
                        target=event.target,
                        headers=tuple(
                            (name, value) for name, value in event.headers
                        ),
                        http_version=event.http_version.decode("ascii"),
                    )
                )
            elif isinstance(event, h11.Data):
                events.append(BodyReceived(data=event.data, more=True))
            elif isinstance(event, h11.EndOfMessage):
                events.append(BodyReceived(data=b"", more=False))
            elif isinstance(event, h11.ConnectionClosed):
                events.append(ConnectionClosed(reason="client closed"))

        return events

    def send_response(
        self, status: int, headers: list[tuple[bytes, bytes]]
    ) -> bytes:
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
        return self._conn.send(response)  # type: ignore[return-value]

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
            parts.append(self._conn.send(h11.Data(data=data)))  # type: ignore[arg-type]
        if not more:
            parts.append(self._conn.send(h11.EndOfMessage()))  # type: ignore[arg-type]
        return b"".join(parts)

    def start_new_cycle(self) -> None:
        """Prepare for the next request on keep-alive connections."""
        self._conn.start_next_cycle()

    # -- Introspection ------------------------------------------------------

    @property
    def their_state(self) -> h11._state.HasReason | type:
        """Current h11 client-side state (for diagnostics)."""
        return self._conn.their_state  # type: ignore[return-value]

    @property
    def our_state(self) -> h11._state.HasReason | type:
        """Current h11 server-side state (for diagnostics)."""
        return self._conn.our_state  # type: ignore[return-value]

    @property
    def client_is_waiting_for_100_continue(self) -> bool:
        """True if the client sent Expect: 100-continue."""
        return self._conn.client_is_waiting_for_100_continue  # type: ignore[return-value]
