"""Integration tests for pounce.testing — makes real HTTP requests."""

import pytest

from pounce._types import Receive, Scope, Send
from pounce.testing import RoundRobinTestProxy, TestServer, serve

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


@pytest.mark.integration
class TestRoundRobinTestProxy:
    """Two real Pounce instances stay connection-pinned behind one proxy."""

    @staticmethod
    def _instance_app(instance: str):
        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                return
            await receive()
            body = instance.encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-length", str(len(body)).encode())],
                }
            )
            await send({"type": "http.response.body", "body": body})

        return app

    @staticmethod
    def _sse_instance_app(instance: str):
        async def app(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                return
            await receive()
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")],
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": f"data: {instance}\n\n".encode(),
                    "more_body": True,
                }
            )
            await receive()

        return app

    @staticmethod
    def _read_sse_instance(proxy: RoundRobinTestProxy) -> str:
        import socket

        client = socket.create_connection((proxy.host, proxy.port), timeout=3.0)
        client.settimeout(3.0)
        try:
            client.sendall(b"GET /events HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            response = b""
            while b"\n\n" not in response:
                response += client.recv(4096)
            return response.split(b"data: ", 1)[1].split(b"\n", 1)[0].decode()
        finally:
            client.close()

    def test_routes_new_connections_across_two_instances(self):
        with (
            TestServer(self._instance_app("instance-a")) as first,
            TestServer(self._instance_app("instance-b")) as second,
            RoundRobinTestProxy([first, second]) as proxy,
        ):
            responses = [
                httpx.get(proxy.url, headers={"connection": "close"}).text for _ in range(4)
            ]

        assert responses == ["instance-a", "instance-b", "instance-a", "instance-b"]

    @pytest.mark.issue(238)
    def test_pins_sse_connections_across_two_instances(self):
        with (
            TestServer(self._sse_instance_app("instance-a")) as first,
            TestServer(self._sse_instance_app("instance-b")) as second,
            RoundRobinTestProxy([first, second]) as proxy,
        ):
            instances = [self._read_sse_instance(proxy) for _ in range(4)]

        assert instances == ["instance-a", "instance-b", "instance-a", "instance-b"]
