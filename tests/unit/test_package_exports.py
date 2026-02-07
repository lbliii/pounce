"""Tests for package re-exports — verify all __init__.py wiring is correct."""


class TestTopLevelExports:
    """pounce.* exports are importable."""

    def test_server_config(self):
        from pounce import ServerConfig
        assert ServerConfig is not None

    def test_run(self):
        from pounce import run
        assert callable(run)

    def test_version(self):
        from pounce import __version__
        assert "0.1.0" in __version__

    def test_asgi_types(self):
        from pounce import ASGIApp, Receive, Scope, Send
        # Type aliases exist
        assert ASGIApp is not None
        assert Receive is not None
        assert Scope is not None
        assert Send is not None


class TestProtocolsExports:
    """pounce.protocols.* exports are importable."""

    def test_protocol_handler(self):
        from pounce.protocols import ProtocolHandler
        assert ProtocolHandler is not None

    def test_h1_protocol(self):
        from pounce.protocols import H1Protocol
        assert H1Protocol is not None

    def test_event_types(self):
        from pounce.protocols import (
            BodyReceived,
            ConnectionClosed,
            RequestReceived,
            Upgraded,
        )
        assert BodyReceived is not None
        assert ConnectionClosed is not None
        assert RequestReceived is not None
        assert Upgraded is not None

    def test_protocol_event_union(self):
        from pounce.protocols import ProtocolEvent
        assert ProtocolEvent is not None


class TestAsgiExports:
    """pounce.asgi.* exports are importable."""

    def test_build_scope(self):
        from pounce.asgi import build_scope
        assert callable(build_scope)

    def test_create_receive(self):
        from pounce.asgi import create_receive
        assert callable(create_receive)

    def test_create_send(self):
        from pounce.asgi import create_send
        assert callable(create_send)

    def test_run_lifespan(self):
        from pounce.asgi import run_lifespan
        # It's an async context manager factory
        assert callable(run_lifespan)


class TestNetExports:
    """pounce.net.* exports are importable."""

    def test_create_listener(self):
        from pounce.net import create_listener
        assert callable(create_listener)
