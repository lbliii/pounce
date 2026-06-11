"""Unit tests for the pounce.testing module."""

import pytest

from pounce._types import Receive, Scope, Send
from pounce.testing import TestServer

# ---------------------------------------------------------------------------
# Minimal ASGI app for tests
# ---------------------------------------------------------------------------


async def _test_app(scope: Scope, receive: Receive, send: Send) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return
    if scope["type"] != "http":
        return
    await receive()
    body = b"ok"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# TestServer unit tests
# ---------------------------------------------------------------------------


class TestTestServerDocstring:
    """Regression: docstring must attach to the class (issue #159)."""

    def test_docstring_is_attached(self):
        """``__test__ = False`` must not precede the docstring."""
        assert TestServer.__doc__ is not None
        assert TestServer.__doc__.strip()
        assert "background thread" in TestServer.__doc__


class TestTestServerProperties:
    """Test TestServer property access before/after start."""

    def test_host_before_start_raises(self):
        server = TestServer(_test_app)
        with pytest.raises(RuntimeError, match="not started"):
            _ = server.host

    def test_port_before_start_raises(self):
        server = TestServer(_test_app)
        with pytest.raises(RuntimeError, match="not started"):
            _ = server.port

    def test_url_before_start_raises(self):
        server = TestServer(_test_app)
        with pytest.raises(RuntimeError, match="not started"):
            _ = server.url

    def test_is_running_false_before_start(self):
        server = TestServer(_test_app)
        assert not server.is_running

    def test_double_start_raises(self):
        server = TestServer(_test_app)
        server.start()
        try:
            with pytest.raises(RuntimeError, match="already running"):
                server.start()
        finally:
            server.stop()

    def test_stop_is_idempotent(self):
        """Calling stop() when not started should be a no-op."""
        server = TestServer(_test_app)
        server.stop()  # Should not raise


class TestTestServerLifecycle:
    """Test start/stop and context manager usage."""

    def test_context_manager(self):
        with TestServer(_test_app) as server:
            assert server.is_running
            assert server.port > 0
            assert server.url.startswith("http://127.0.0.1:")
        assert not server.is_running

    def test_ephemeral_port(self):
        """port=0 should bind to a random available port."""
        with TestServer(_test_app) as server:
            assert server.port != 0

    def test_custom_host(self):
        with TestServer(_test_app, host="127.0.0.1") as server:
            assert server.host == "127.0.0.1"


class TestPounceServerFixture:
    """Test the auto-registered pytest fixture."""

    def test_factory_creates_server(self, pounce_server):
        server = pounce_server(_test_app)
        assert server.is_running
        assert server.port > 0

    def test_factory_multiple_servers(self, pounce_server):
        s1 = pounce_server(_test_app)
        s2 = pounce_server(_test_app)
        assert s1.port != s2.port
        assert s1.is_running
        assert s2.is_running
