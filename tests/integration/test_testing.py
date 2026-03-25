"""Integration tests for pounce.testing — makes real HTTP requests."""

import pytest

from pounce._types import Receive, Scope, Send
from pounce.testing import TestServer, serve

# ---------------------------------------------------------------------------
# ASGI test apps
# ---------------------------------------------------------------------------


async def _hello_app(scope: Scope, receive: Receive, send: Send) -> None:
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
    body = b"Hello, World!"
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


async def _echo_path_app(scope: Scope, receive: Receive, send: Send) -> None:
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
    body = scope["path"].encode()
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
# Integration tests
# ---------------------------------------------------------------------------

httpx = pytest.importorskip("httpx")


@pytest.mark.integration
class TestTestServerHTTP:
    """Make real HTTP requests to a TestServer."""

    def test_get_hello(self):
        with TestServer(_hello_app) as server:
            resp = httpx.get(f"{server.url}/")
            assert resp.status_code == 200
            assert resp.text == "Hello, World!"

    def test_echo_path(self):
        with TestServer(_echo_path_app) as server:
            resp = httpx.get(f"{server.url}/foo/bar")
            assert resp.status_code == 200
            assert resp.text == "/foo/bar"

    def test_multiple_requests(self):
        with TestServer(_hello_app) as server:
            for _ in range(5):
                resp = httpx.get(f"{server.url}/")
                assert resp.status_code == 200


@pytest.mark.integration
class TestServeAsyncContextManager:
    """Test the async serve() helper."""

    async def test_serve_basic(self):
        async with serve(_hello_app) as server, httpx.AsyncClient() as client:
            resp = await client.get(f"{server.url}/")
            assert resp.status_code == 200
            assert resp.text == "Hello, World!"


@pytest.mark.integration
class TestPounceServerFixtureIntegration:
    """Test the pounce_server fixture with real HTTP."""

    def test_fixture_http_request(self, pounce_server):
        server = pounce_server(_hello_app)
        resp = httpx.get(f"{server.url}/")
        assert resp.status_code == 200
        assert resp.text == "Hello, World!"
