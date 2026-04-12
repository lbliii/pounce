"""
Starlette compatibility tests — verify Starlette apps work through pounce.

Exercises: routing, middleware, lifespan with state, streaming responses,
WebSocket, background tasks, and exception handlers.

"""

import asyncio
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("starlette")

from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket

# ---------------------------------------------------------------------------
# App builders — each test gets a fresh app instance
# ---------------------------------------------------------------------------


def _make_routing_app() -> Starlette:
    """App exercising basic routing: GET, POST, path params, query params."""

    async def homepage(request: Request) -> PlainTextResponse:
        return PlainTextResponse("Hello from Starlette")

    async def get_item(request: Request) -> JSONResponse:
        item_id = request.path_params["item_id"]
        return JSONResponse({"item_id": int(item_id)})

    async def create_item(request: Request) -> JSONResponse:
        body = await request.json()
        return JSONResponse({"created": body}, status_code=201)

    async def query_params(request: Request) -> JSONResponse:
        return JSONResponse({"params": dict(request.query_params)})

    return Starlette(
        routes=[
            Route("/", homepage),
            Route("/items/{item_id:int}", get_item),
            Route("/items", create_item, methods=["POST"]),
            Route("/search", query_params),
        ],
    )


def _make_middleware_app() -> Starlette:
    """App with BaseHTTPMiddleware that adds a custom header."""

    class AddHeaderMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Custom-Middleware"] = "applied"
            return response

    async def homepage(request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    return Starlette(
        routes=[Route("/", homepage)],
        middleware=[Middleware(AddHeaderMiddleware)],
    )


def _make_lifespan_app() -> Starlette:
    """App using lifespan with app.state sharing (pre-ASGI scope["state"] pattern)."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(app):
        app.state.db_pool = "connected"
        yield
        app.state.db_pool = None

    async def check_state(request: Request) -> JSONResponse:
        return JSONResponse({"db_pool": request.app.state.db_pool})

    return Starlette(
        routes=[Route("/state", check_state)],
        lifespan=lifespan,
    )


def _make_streaming_app() -> Starlette:
    """App that returns a StreamingResponse (SSE-like)."""

    async def stream_events(request: Request) -> StreamingResponse:
        async def event_generator():
            for i in range(3):
                yield f"data: event {i}\n\n"
                await asyncio.sleep(0.01)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return Starlette(routes=[Route("/events", stream_events)])


def _make_websocket_app() -> Starlette:
    """App with a WebSocket echo endpoint."""

    async def ws_echo(websocket: WebSocket) -> None:
        await websocket.accept()
        data = await websocket.receive_text()
        await websocket.send_text(f"echo: {data}")
        await websocket.close()

    return Starlette(routes=[WebSocketRoute("/ws", ws_echo)])


def _make_background_task_app(marker_path: str) -> Starlette:
    """App that schedules a BackgroundTask to write a file after response."""

    def write_marker(path: str) -> None:
        Path(path).write_text("done")

    async def with_bg(request: Request) -> PlainTextResponse:
        task = BackgroundTask(write_marker, marker_path)
        return PlainTextResponse("accepted", background=task)

    return Starlette(routes=[Route("/bg", with_bg)])


def _make_error_handling_app() -> Starlette:
    """App with a custom exception handler."""

    class NotFoundError(Exception):
        def __init__(self, item_id: int) -> None:
            self.item_id = item_id

    async def not_found_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse({"error": f"Item {exc.item_id} not found"}, status_code=404)

    async def get_item(request: Request) -> JSONResponse:
        raise NotFoundError(42)

    return Starlette(
        routes=[Route("/items/{item_id:int}", get_item)],
        exception_handlers={NotFoundError: not_found_handler},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStarletteRouting:
    """Basic routing: GET, POST, path params, query params, JSON body."""

    def test_homepage(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/")
        assert resp.status_code == 200
        assert resp.text == "Hello from Starlette"

    def test_path_params(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/items/7")
        assert resp.status_code == 200
        assert resp.json() == {"item_id": 7}

    def test_post_json_body(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.post(
            f"http://{host}:{port}/items",
            json={"name": "Widget"},
        )
        assert resp.status_code == 201
        assert resp.json() == {"created": {"name": "Widget"}}

    def test_query_params(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/search?q=test&page=2")
        assert resp.status_code == 200
        assert resp.json() == {"params": {"q": "test", "page": "2"}}

    def test_method_not_allowed(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.delete(f"http://{host}:{port}/items")
        assert resp.status_code == 405

    def test_not_found(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_routing_app())
        resp = http_client.get(f"http://{host}:{port}/nonexistent")
        assert resp.status_code == 404


class TestStarletteMiddleware:
    """BaseHTTPMiddleware adds a custom response header."""

    def test_middleware_adds_header(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_middleware_app())
        resp = http_client.get(f"http://{host}:{port}/")
        assert resp.status_code == 200
        assert resp.headers.get("x-custom-middleware") == "applied"


class TestStarletteLifespan:
    """Lifespan startup populates app.state, accessible in routes."""

    def test_lifespan_state(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_lifespan_app())
        resp = http_client.get(f"http://{host}:{port}/state")
        assert resp.status_code == 200
        assert resp.json() == {"db_pool": "connected"}


class TestStarletteStreaming:
    """StreamingResponse delivers chunks correctly."""

    def test_sse_stream(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_streaming_app())
        resp = http_client.get(f"http://{host}:{port}/events")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        # All 3 events should be in the response body
        for i in range(3):
            assert f"data: event {i}" in resp.text


class TestStarletteWebSocket:
    """WebSocket echo endpoint works through pounce."""

    def test_websocket_echo(self, pounce_server) -> None:
        pytest.importorskip("wsproto")
        host, port = pounce_server(_make_websocket_app())

        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect((host, port))

        # Send WebSocket upgrade request
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

        # Read 101 response
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += sock.recv(4096)
        assert b"101" in resp

        # Send a text frame: "hello"
        payload = b"hello"
        # Masked text frame (opcode 0x1)
        mask_key = b"\x01\x02\x03\x04"
        masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        frame = bytes([0x81, 0x80 | len(payload)]) + mask_key + masked
        sock.sendall(frame)

        # Read the echo response frame
        data = sock.recv(4096)
        # Server sends unmasked text frame
        assert data[0] == 0x81  # FIN + text opcode
        length = data[1] & 0x7F
        text = data[2 : 2 + length].decode()
        assert text == "echo: hello"

        sock.close()


class TestStarletteBackgroundTask:
    """BackgroundTask runs after response is sent."""

    def test_background_task_completes(self, pounce_server, http_client) -> None:
        with tempfile.NamedTemporaryFile(suffix=".marker", delete=False) as f:
            marker_path = f.name

        # Remove the file so we can detect when the task writes it
        Path(marker_path).unlink()

        host, port = pounce_server(_make_background_task_app(marker_path))
        resp = http_client.get(f"http://{host}:{port}/bg")
        assert resp.status_code == 200
        assert resp.text == "accepted"

        # Wait for background task to complete
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if Path(marker_path).exists():
                break
            time.sleep(0.05)

        assert Path(marker_path).read_text() == "done"
        Path(marker_path).unlink(missing_ok=True)


class TestStarletteErrorHandling:
    """Custom exception handler produces correct response."""

    def test_custom_exception_handler(self, pounce_server, http_client) -> None:
        host, port = pounce_server(_make_error_handling_app())
        resp = http_client.get(f"http://{host}:{port}/items/42")
        assert resp.status_code == 404
        assert resp.json() == {"error": "Item 42 not found"}
