"""Real-server tests for ServerConfig.middleware."""

import httpx

from pounce import CORSMiddleware, Response, SecurityHeadersMiddleware
from pounce._types import Receive, Scope, Send
from pounce.testing import TestServer
from tests.conftest import with_lifespan


@with_lifespan
async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    await receive()
    body = f"ok:{scope.get('tenant', 'none')}".encode()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


@with_lifespan
async def _boom_app(scope: Scope, receive: Receive, send: Send) -> None:
    await receive()
    raise ValueError("bad route")


def test_pre_request_middleware_short_circuits_real_server() -> None:
    app_called = False

    async def auth(scope: Scope) -> Scope | Response:
        nonlocal app_called
        if b"authorization" not in dict(scope["headers"]):
            return Response(
                status=401,
                headers=[(b"content-type", b"text/plain")],
                body=b"missing auth",
            )
        app_called = True
        scope["tenant"] = "alpha"
        return scope

    with TestServer(_ok_app, middleware=[auth]) as server:
        rejected = httpx.get(f"{server.url}/")
        accepted = httpx.get(f"{server.url}/", headers={"authorization": "token"})

    assert rejected.status_code == 401
    assert rejected.text == "missing auth"
    assert accepted.status_code == 200
    assert accepted.text == "ok:alpha"
    assert app_called is True


def test_post_response_middleware_runs_on_real_server() -> None:
    with TestServer(
        _ok_app,
        middleware=[
            CORSMiddleware(allow_origin="https://tenant.example"),
            SecurityHeadersMiddleware(csp="default-src 'self'; frame-ancestors 'none'"),
        ],
    ) as server:
        response = httpx.get(f"{server.url}/")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://tenant.example"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["content-security-policy"] == "default-src 'self'; frame-ancestors 'none'"


def test_exception_middleware_handles_real_server_app_error() -> None:
    async def handle_value_error(scope: Scope, exc: Exception) -> Response | None:
        if isinstance(exc, ValueError):
            return Response(status=418, body=b"handled")
        return None

    with TestServer(_boom_app, middleware=[handle_value_error]) as server:
        response = httpx.get(f"{server.url}/")

    assert response.status_code == 418
    assert response.text == "handled"
