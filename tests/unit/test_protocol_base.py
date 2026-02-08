"""Tests for pounce.protocols._base — event types and protocol contract."""

from dataclasses import FrozenInstanceError

import pytest

from pounce.protocols._base import (
    BodyReceived,
    ConnectionClosed,
    ProtocolHandler,
    RequestReceived,
    Upgraded,
)


class TestRequestReceived:
    """RequestReceived is an immutable record of a parsed request head."""

    def test_create(self):
        req = RequestReceived(
            method=b"GET",
            target=b"/api/users",
            headers=((b"host", b"localhost"),),
            http_version="1.1",
        )
        assert req.method == b"GET"
        assert req.target == b"/api/users"
        assert req.headers == ((b"host", b"localhost"),)
        assert req.http_version == "1.1"

    def test_frozen(self):
        req = RequestReceived(
            method=b"GET",
            target=b"/",
            headers=(),
            http_version="1.1",
        )
        with pytest.raises(FrozenInstanceError):
            req.method = b"POST"  # type: ignore[misc]

    def test_equality(self):
        a = RequestReceived(method=b"GET", target=b"/", headers=(), http_version="1.1")
        b = RequestReceived(method=b"GET", target=b"/", headers=(), http_version="1.1")
        assert a == b


class TestBodyReceived:
    """BodyReceived carries a chunk of request body."""

    def test_create(self):
        body = BodyReceived(data=b"hello", more=True)
        assert body.data == b"hello"
        assert body.more is True

    def test_frozen(self):
        body = BodyReceived(data=b"x", more=False)
        with pytest.raises(FrozenInstanceError):
            body.data = b"y"  # type: ignore[misc]

    def test_final_chunk(self):
        body = BodyReceived(data=b"done", more=False)
        assert body.more is False


class TestConnectionClosed:
    """ConnectionClosed signals the end of a connection."""

    def test_create(self):
        closed = ConnectionClosed(reason="client disconnected")
        assert closed.reason == "client disconnected"

    def test_frozen(self):
        closed = ConnectionClosed(reason="eof")
        with pytest.raises(FrozenInstanceError):
            closed.reason = "other"  # type: ignore[misc]


class TestUpgraded:
    """Upgraded signals a protocol switch."""

    def test_websocket(self):
        upgraded = Upgraded(protocol="websocket")
        assert upgraded.protocol == "websocket"

    def test_h2c(self):
        upgraded = Upgraded(protocol="h2c")
        assert upgraded.protocol == "h2c"

    def test_frozen(self):
        upgraded = Upgraded(protocol="websocket")
        with pytest.raises(FrozenInstanceError):
            upgraded.protocol = "h2c"  # type: ignore[misc]


class TestProtocolEventUnion:
    """ProtocolEvent is a union of all event types."""

    def test_request_is_event(self):
        req = RequestReceived(method=b"GET", target=b"/", headers=(), http_version="1.1")
        # Type-level check — at runtime just verify it's one of the union types
        assert isinstance(req, RequestReceived)

    def test_body_is_event(self):
        body = BodyReceived(data=b"x", more=False)
        assert isinstance(body, BodyReceived)


class TestProtocolHandlerConformance:
    """A class with the right methods satisfies ProtocolHandler."""

    def test_conformance(self):
        class FakeHandler:
            def receive_data(self, data: bytes) -> list:
                return []

            def send_response(
                self, status: int, headers: list[tuple[bytes, bytes]]
            ) -> bytes:
                return b""

            def send_body(self, data: bytes, more: bool = False) -> bytes:
                return b""

            def start_new_cycle(self) -> None:
                pass

        handler = FakeHandler()
        # Structural typing — verify it matches the Protocol shape
        assert isinstance(handler, ProtocolHandler)

    def test_non_conforming_rejected(self):
        class Incomplete:
            def receive_data(self, data: bytes) -> list:
                return []
            # Missing other methods

        handler = Incomplete()
        assert not isinstance(handler, ProtocolHandler)
