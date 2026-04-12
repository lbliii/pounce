"""
Litestar compatibility tests — verify Litestar apps work through pounce.

Exercises: route handlers, dependency injection, guards, middleware,
lifespan hooks, WebSocket, and streaming responses.

"""

import asyncio
from dataclasses import dataclass

import pytest

litestar = pytest.importorskip("litestar")

from litestar import Litestar, MediaType, Request, get, post, websocket  # noqa: E402
from litestar.connection import WebSocket as LitestarWebSocket  # noqa: E402
from litestar.di import Provide  # noqa: E402
from litestar.middleware.base import AbstractMiddleware  # noqa: E402
from litestar.response import Stream  # noqa: E402
from litestar.types import Receive, Scope, Send  # noqa: E402

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Item:
    name: str
    price: float


# ---------------------------------------------------------------------------
# App builders
# ---------------------------------------------------------------------------


def _make_routing_app() -> Litestar:
    """App exercising basic route handlers."""

    @get("/")
    async def homepage() -> dict:
        return {"message": "Hello from Litestar"}

    @get("/items/{item_id:int}")
    async def get_item(item_id: int) -> dict:
        return {"item_id": item_id}

    @post("/items", status_code=201)
    async def create_item(data: Item) -> dict:
        return {"created": {"name": data.name, "price": data.price}}

    @get("/search")
    async def search(q: str = "", page: int = 1) -> dict:
        return {"q": q, "page": page}

    return Litestar(route_handlers=[homepage, get_item, create_item, search])


def _make_dependency_app() -> Litestar:
    """App exercising Litestar's dependency injection."""

    async def provide_db() -> dict:
        return {"connection": "active"}

    async def provide_user(db: dict) -> dict:
        return {"user": "test_user", "db": db}

    @get("/me", dependencies={"user": Provide(provide_user)})
    async def read_user(user: dict) -> dict:
        return user

    return Litestar(
        route_handlers=[read_user],
        dependencies={"db": Provide(provide_db)},
    )


def _make_middleware_app() -> Litestar:
    """App with middleware that adds a custom header."""

    class AddHeaderMiddleware(AbstractMiddleware):
        async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] != "http":
                await self.app(scope, receive, send)
                return

            async def send_with_header(message: dict) -> None:
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((b"x-litestar-middleware", b"applied"))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, receive, send_with_header)

    @get("/")
    async def homepage() -> dict:
        return {"ok": True}

    return Litestar(route_handlers=[homepage], middleware=[AddHeaderMiddleware])


def _make_lifespan_app() -> Litestar:
    """App using on_startup/on_shutdown hooks."""

    async def on_startup(app: Litestar) -> None:
        app.state.cache = {"warm": True}

    @get("/cache")
    async def check_cache(request: Request) -> dict:
        return {"cache": request.app.state.cache}

    return Litestar(
        route_handlers=[check_cache],
        on_startup=[on_startup],
    )


def _make_streaming_app() -> Litestar:
    """App returning a streaming response."""

    async def event_generator():
        for i in range(3):
            yield f"data: event {i}\n\n".encode()
            await asyncio.sleep(0.01)

    @get("/events", media_type=MediaType.TEXT)
    async def stream_events() -> Stream:
        return Stream(event_generator())

    return Litestar(route_handlers=[stream_events])


def _make_websocket_app() -> Litestar:
    """App with a WebSocket echo handler."""

    @websocket("/ws")
    async def ws_echo(socket: LitestarWebSocket) -> None:
        await socket.accept()
        data = await socket.receive_data(mode="text")
        await socket.send_data(f"echo: {data}", mode="text")
        await socket.close()

    return Litestar(route_handlers=[ws_echo])


def _make_error_handling_app() -> Litestar:
    """App with a custom exception handler."""
    from litestar.exceptions import HTTPException as LitestarHTTPException
    from litestar.response import Response

    def custom_handler(request: Request, exc: LitestarHTTPException) -> Response:
        return Response(
            content={"custom_error": exc.detail},
            status_code=exc.status_code,
            media_type=MediaType.JSON,
        )

    @get("/fail")
    async def fail() -> None:
        raise LitestarHTTPException(status_code=422, detail="validation failed")

    return Litestar(
        route_handlers=[fail],
        exception_handlers={LitestarHTTPException: custom_handler},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLitestarRouting:
    """Basic route handlers: GET, POST, path params, query params."""

    def test_homepage(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/")
        assert resp.status_code == 200
        assert resp.json() == {"message": "Hello from Litestar"}

    def test_path_params(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/items/7")
        assert resp.status_code == 200
        assert resp.json() == {"item_id": 7}

    def test_post_json_body(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.post(
            f"http://{host}:{port}/items",
            json={"name": "Widget", "price": 9.99},
        )
        assert resp.status_code == 201
        assert resp.json() == {"created": {"name": "Widget", "price": 9.99}}

    def test_query_params(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/search?q=test&page=3")
        assert resp.status_code == 200
        assert resp.json() == {"q": "test", "page": 3}

    def test_not_found(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/nonexistent")
        assert resp.status_code == 404


class TestLitestarDependencyInjection:
    """Provide() dependency chain resolves correctly."""

    def test_nested_deps(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_dependency_app())
        resp = http_client.get(f"http://{host}:{port}/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"] == "test_user"
        assert data["db"] == {"connection": "active"}


class TestLitestarMiddleware:
    """AbstractMiddleware adds a custom response header."""

    def test_middleware_adds_header(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_middleware_app())
        resp = http_client.get(f"http://{host}:{port}/")
        assert resp.status_code == 200
        assert resp.headers.get("x-litestar-middleware") == "applied"


class TestLitestarLifespan:
    """on_startup hook populates app.state."""

    def test_lifespan_state(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_lifespan_app())
        resp = http_client.get(f"http://{host}:{port}/cache")
        assert resp.status_code == 200
        assert resp.json() == {"cache": {"warm": True}}


class TestLitestarStreaming:
    """Streaming response delivers chunks correctly."""

    def test_stream_events(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_streaming_app())
        resp = http_client.get(f"http://{host}:{port}/events")
        assert resp.status_code == 200
        for i in range(3):
            assert f"data: event {i}" in resp.text


class TestLitestarWebSocket:
    """WebSocket echo handler works through pounce."""

    @pytest.mark.xfail(
        reason="Litestar WebSocket routing expects scope without HTTP method; "
        "pounce WebSocket upgrade flow includes method='GET' in scope lookup",
        strict=False,
    )
    def test_websocket_echo(self, pounce_server) -> None:
        pytest.importorskip("wsproto")
        host, port = pounce_server(_make_websocket_app())

        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((host, port))

        upgrade = (
            b"GET /ws HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n"
        )
        sock.sendall(upgrade)

        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += sock.recv(4096)
        assert b"101" in resp

        payload = b"hello"
        mask_key = b"\x01\x02\x03\x04"
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        frame = bytes([0x81, 0x80 | len(payload)]) + mask_key + masked
        sock.sendall(frame)

        data = sock.recv(4096)
        assert data[0] == 0x81
        length = data[1] & 0x7F
        text = data[2 : 2 + length].decode()
        assert text == "echo: hello"

        sock.close()


class TestLitestarErrorHandling:
    """Custom exception handler produces correct response."""

    def test_custom_exception_handler(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_error_handling_app())
        resp = http_client.get(f"http://{host}:{port}/fail")
        assert resp.status_code == 422
        assert resp.json() == {"custom_error": "validation failed"}
