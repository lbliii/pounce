"""Real-worker proof for MCP Streamable HTTP forwarding and framing."""

from __future__ import annotations

import json

import pytest

from pounce._types import Receive, Scope, Send
from tests.conftest import send_raw_request, start_worker, with_lifespan

pytestmark = pytest.mark.issue(229)


def test_mcp_routing_and_authorization_headers_reach_asgi_unchanged() -> None:
    @with_lifespan
    async def inspect_headers(scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        selected = [
            [name.decode("ascii"), value.decode("ascii")]
            for name, value in scope["headers"]
            if name
            in {
                b"mcp-protocol-version",
                b"mcp-method",
                b"mcp-name",
                b"mcp-param-tenant",
                b"authorization",
            }
        ]
        body = json.dumps(selected, separators=(",", ":")).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})

    worker, sock, thread = start_worker(inspect_headers)
    try:
        addr = sock.getsockname()
        body = b'{"jsonrpc":"2.0","id":1,"method":"tools/call"}'
        request = (
            b"POST /mcp HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Connection: close\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"MCP-Protocol-Version: 2026-07-28\r\n"
            b"Mcp-Method: tools/call\r\n"
            b"Mcp-Name: =?base64?aMOpbGxv?=\r\n"
            b"Mcp-Param-Tenant: tenant-A/42\r\n"
            b"Authorization: Bearer token.with+symbols==\r\n"
            b"\r\n" + body
        )
        response = send_raw_request(addr, request)
    finally:
        worker.shutdown()
        thread.join(timeout=2)
        sock.close()

    payload = json.loads(response.split(b"\r\n\r\n", 1)[1])
    assert payload == [
        ["mcp-protocol-version", "2026-07-28"],
        ["mcp-method", "tools/call"],
        ["mcp-name", "=?base64?aMOpbGxv?="],
        ["mcp-param-tenant", "tenant-A/42"],
        ["authorization", "Bearer token.with+symbols=="],
    ]


def test_mcp_multi_event_sse_response_survives_http_framing() -> None:
    @with_lifespan
    async def stream_events(scope: Scope, receive: Receive, send: Send) -> None:
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
                "body": (
                    b'event: message\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
                ),
                "more_body": True,
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": (
                    b'event: message\ndata: {"jsonrpc":"2.0","id":1,'
                    b'"result":{"resultType":"complete"}}\n\n'
                ),
                "more_body": False,
            }
        )

    worker, sock, thread = start_worker(stream_events)
    try:
        addr = sock.getsockname()
        response = send_raw_request(
            addr,
            b"POST /mcp HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Connection: close\r\n"
            b"Content-Length: 0\r\n"
            b"\r\n",
        )
    finally:
        worker.shutdown()
        thread.join(timeout=2)
        sock.close()

    assert b"HTTP/1.1 200" in response
    assert b"content-type: text/event-stream" in response.lower()
    progress_at = response.index(b"notifications/progress")
    complete_at = response.index(b'"resultType":"complete"')
    assert progress_at < complete_at
