"""Tests for package re-exports — verify all __init__.py wiring is correct."""

from dataclasses import fields


class TestTopLevelExports:
    """pounce.* exports are importable."""

    def test_server_config(self):
        from pounce import ServerConfig

        assert ServerConfig is not None

    def test_display_config(self):
        from pounce import DisplayConfig

        assert DisplayConfig is not None

    def test_run(self):
        from pounce import run

        assert callable(run)

    def test_server_config_kwargs_mirror_server_config(self):
        from pounce import ServerConfig, ServerConfigKwargs

        expected = {
            field.name for field in fields(ServerConfig) if field.init and not field.name.startswith("_")
        }
        actual = set(ServerConfigKwargs.__annotations__)

        assert actual == expected

    def test_version(self):
        from pounce import __version__

        assert isinstance(__version__, str)
        assert __version__.count(".") >= 2  # Semver (e.g. 0.2.0)

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

    def test_create_listeners(self):
        from pounce.net import create_listeners

        assert callable(create_listeners)


class TestPhase2Exports:
    """Phase 2 modules are importable."""

    def test_runtime_module(self):
        from pounce._runtime import (
            default_worker_count,
            detect_worker_mode,
            is_gil_enabled,
        )

        assert callable(is_gil_enabled)
        assert callable(detect_worker_mode)
        assert callable(default_worker_count)

    def test_supervisor_module(self):
        from pounce.supervisor import Supervisor

        assert Supervisor is not None

    def test_error_types(self):
        from pounce._errors import SupervisorError, WorkerError

        assert issubclass(SupervisorError, Exception)
        assert issubclass(WorkerError, Exception)


class TestPhase3ProtocolExports:
    """Phase 3 protocol exports are importable via pounce.protocols."""

    def test_ws_protocol(self):
        from pounce.protocols import WSProtocol

        assert WSProtocol is not None

    def test_h2_connection(self):
        from pounce.protocols import H2Connection

        assert H2Connection is not None

    def test_h2_event_types(self):
        from pounce.protocols import (
            H2BodyReceived,
            H2GoAway,
            H2RequestReceived,
            H2StreamReset,
            H2WebSocketRequest,
            H2WindowUpdated,
        )

        assert H2RequestReceived is not None
        assert H2BodyReceived is not None
        assert H2StreamReset is not None
        assert H2GoAway is not None
        assert H2WindowUpdated is not None
        assert H2WebSocketRequest is not None

    def test_ws_event_types(self):
        from pounce.protocols import (
            WebSocketConnected,
            WebSocketDataReceived,
            WebSocketDisconnected,
        )

        assert WebSocketConnected is not None
        assert WebSocketDataReceived is not None
        assert WebSocketDisconnected is not None


class TestPhase3AsgiExports:
    """Phase 3 ASGI bridge exports."""

    def test_ws_bridge(self):
        from pounce.asgi import build_ws_scope, create_ws_receive, create_ws_send

        assert callable(build_ws_scope)
        assert callable(create_ws_receive)
        assert callable(create_ws_send)

    def test_h2_bridge(self):
        from pounce.asgi import build_h2_scope, create_h2_receive, create_h2_send

        assert callable(build_h2_scope)
        assert callable(create_h2_receive)
        assert callable(create_h2_send)


class TestPhase3NetExports:
    """Phase 3 network exports."""

    def test_tls_exports(self):
        from pounce.net import create_tls_context, is_tls_configured

        assert callable(create_tls_context)
        assert callable(is_tls_configured)


class TestPhase3ErrorExports:
    """Phase 3 error types."""

    def test_tls_error(self):
        from pounce._errors import TLSError

        assert issubclass(TLSError, Exception)

    def test_reload_error(self):
        from pounce._errors import ReloadError

        assert issubclass(ReloadError, Exception)
