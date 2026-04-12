"""
FastAPI compatibility tests — verify FastAPI apps work through pounce.

Exercises: path operations, dependency injection, Pydantic validation,
middleware, exception handlers, lifespan, WebSocket, and streaming.

"""

import asyncio
from contextlib import asynccontextmanager

import pytest

fastapi = pytest.importorskip("fastapi")

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket  # noqa: E402
from fastapi.responses import JSONResponse, StreamingResponse  # noqa: E402
from pydantic import BaseModel  # noqa: E402

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class Item(BaseModel):
    name: str
    price: float
    in_stock: bool = True


class ItemResponse(BaseModel):
    id: int
    item: Item


# ---------------------------------------------------------------------------
# App builders
# ---------------------------------------------------------------------------


def _make_routing_app() -> FastAPI:
    """App exercising path operations, path/query params, and JSON body."""
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"message": "Hello from FastAPI"}

    @app.get("/items/{item_id}")
    async def get_item(item_id: int):
        return {"item_id": item_id}

    @app.post("/items", status_code=201)
    async def create_item(item: Item):
        return ItemResponse(id=1, item=item)

    @app.get("/search")
    async def search(q: str = Query(default=""), page: int = Query(default=1)):
        return {"q": q, "page": page}

    @app.put("/items/{item_id}")
    async def update_item(item_id: int, item: Item):
        return {"item_id": item_id, "item": item.model_dump()}

    @app.delete("/items/{item_id}")
    async def delete_item(item_id: int):
        return {"deleted": item_id}

    return app


def _make_dependency_app() -> FastAPI:
    """App exercising FastAPI's dependency injection."""
    app = FastAPI()

    async def get_db():
        return {"connection": "active"}

    async def get_current_user(db=Depends(get_db)):  # noqa: B008
        return {"user": "test_user", "db": db}

    @app.get("/me")
    async def read_user(user=Depends(get_current_user)):  # noqa: B008
        return user

    return app


def _make_middleware_app() -> FastAPI:
    """App with ASGI middleware that adds a custom header."""
    app = FastAPI()

    @app.middleware("http")
    async def add_header(request, call_next):
        response = await call_next(request)
        response.headers["X-FastAPI-Middleware"] = "applied"
        return response

    @app.get("/")
    async def root():
        return {"ok": True}

    return app


def _make_exception_handler_app() -> FastAPI:
    """App with custom exception handlers."""
    app = FastAPI()

    @app.exception_handler(HTTPException)
    async def custom_http_exception(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"custom_error": exc.detail},
        )

    @app.get("/fail")
    async def fail():
        raise HTTPException(status_code=422, detail="validation failed")

    return app


def _make_lifespan_app() -> FastAPI:
    """App using the modern lifespan context manager."""

    @asynccontextmanager
    async def lifespan(app):
        app.state.cache = {"warm": True}
        yield
        app.state.cache = None

    app = FastAPI(lifespan=lifespan)

    @app.get("/cache")
    async def check_cache():
        return {"cache": app.state.cache}

    return app


def _make_streaming_app() -> FastAPI:
    """App returning a StreamingResponse (SSE pattern)."""
    app = FastAPI()

    @app.get("/events")
    async def stream_events():
        async def generate():
            for i in range(3):
                yield f"data: event {i}\n\n"
                await asyncio.sleep(0.01)

        return StreamingResponse(generate(), media_type="text/event-stream")

    return app


def _make_websocket_app() -> FastAPI:
    """App with a WebSocket endpoint."""
    app = FastAPI()

    @app.websocket("/ws")
    async def ws_echo(websocket: WebSocket):
        await websocket.accept()
        data = await websocket.receive_text()
        await websocket.send_text(f"echo: {data}")
        await websocket.close()

    return app


def _make_validation_app() -> FastAPI:
    """App that exercises Pydantic request validation."""
    app = FastAPI()

    @app.post("/validate")
    async def validate_item(item: Item):
        return {"valid": True, "name": item.name}

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFastAPIRouting:
    """Path operations: GET, POST, PUT, DELETE with params and JSON body."""

    def test_root(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/")
        assert resp.status_code == 200
        assert resp.json() == {"message": "Hello from FastAPI"}

    def test_path_params(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/items/42")
        assert resp.status_code == 200
        assert resp.json() == {"item_id": 42}

    def test_post_with_pydantic(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.post(
            f"http://{host}:{port}/items",
            json={"name": "Widget", "price": 9.99},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == 1
        assert data["item"]["name"] == "Widget"
        assert data["item"]["price"] == 9.99
        assert data["item"]["in_stock"] is True

    def test_query_params(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/search?q=test&page=3")
        assert resp.status_code == 200
        assert resp.json() == {"q": "test", "page": 3}

    def test_put(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.put(
            f"http://{host}:{port}/items/5",
            json={"name": "Updated", "price": 19.99, "in_stock": False},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["item_id"] == 5
        assert data["item"]["name"] == "Updated"

    def test_delete(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.delete(f"http://{host}:{port}/items/3")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 3}

    def test_not_found(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/nonexistent")
        assert resp.status_code == 404

    def test_openapi_schema(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "openapi" in schema
        assert "/items/{item_id}" in schema["paths"]


class TestFastAPIDependencyInjection:
    """Depends() chain resolves correctly through pounce."""

    def test_nested_depends(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_dependency_app())
        resp = http_client.get(f"http://{host}:{port}/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["user"] == "test_user"
        assert data["db"] == {"connection": "active"}


class TestFastAPIMiddleware:
    """ASGI middleware adds a custom response header."""

    def test_middleware_adds_header(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_middleware_app())
        resp = http_client.get(f"http://{host}:{port}/")
        assert resp.status_code == 200
        assert resp.headers.get("x-fastapi-middleware") == "applied"


class TestFastAPIExceptionHandlers:
    """Custom exception handlers produce correct responses."""

    def test_custom_http_exception(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_exception_handler_app())
        resp = http_client.get(f"http://{host}:{port}/fail")
        assert resp.status_code == 422
        assert resp.json() == {"custom_error": "validation failed"}


class TestFastAPILifespan:
    """Lifespan context manager populates app.state."""

    def test_lifespan_state(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_lifespan_app())
        resp = http_client.get(f"http://{host}:{port}/cache")
        assert resp.status_code == 200
        assert resp.json() == {"cache": {"warm": True}}


class TestFastAPIStreaming:
    """StreamingResponse delivers chunks correctly."""

    def test_sse_stream(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_streaming_app())
        resp = http_client.get(f"http://{host}:{port}/events")
        assert resp.status_code == 200
        for i in range(3):
            assert f"data: event {i}" in resp.text


class TestFastAPIWebSocket:
    """WebSocket endpoint works through pounce."""

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

        # Send masked text frame
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


class TestFastAPIValidation:
    """Pydantic request validation works through pounce."""

    def test_valid_request(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_validation_app())
        resp = http_client.post(
            f"http://{host}:{port}/validate",
            json={"name": "Test", "price": 5.0},
        )
        assert resp.status_code == 200
        assert resp.json() == {"valid": True, "name": "Test"}

    def test_invalid_request(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_validation_app())
        resp = http_client.post(
            f"http://{host}:{port}/validate",
            json={"name": "Test"},  # missing required 'price'
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data
