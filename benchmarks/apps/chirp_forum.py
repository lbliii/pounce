"""Chirp/LB Sonic-shaped HTML-over-the-wire forum workload."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from pounce import StaticFiles
from pounce._static import StaticMount

_ASSET_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "chirp_forum" / "assets"
_TENANTS = {
    "localhost": "Local Table",
    "127.0.0.1": "Local Table",
    "alpha.example": "Alpha Company",
    "beta.example": "Beta Company",
}
_THREADS = {
    "1": {
        "title": "Session zero planning",
        "posts": [
            "Welcome to the campaign.",
            "Post your character hooks before Friday.",
            "The first scene opens at the spaceport.",
        ],
    },
    "2": {
        "title": "Downtime actions",
        "posts": [
            "List your crafting, research, and contacts.",
            "Resolve travel before the next scene starts.",
        ],
    },
}


def _header(scope: dict[str, Any], name: bytes) -> bytes | None:
    for header_name, value in scope["headers"]:
        if header_name == name:
            return value
    return None


def _tenant_name(scope: dict[str, Any]) -> str:
    host = (_header(scope, b"host") or b"localhost").decode("latin-1").split(":", 1)[0]
    registry = scope.get("state", {}).get("tenants", _TENANTS)
    return registry.get(host, "Public Table")


async def _read_body(receive: Any) -> bytes:
    body = bytearray()
    while True:
        message = await receive()
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return bytes(body)


def _html_response(body: str) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    payload = body.encode("utf-8")
    return (
        200,
        [
            (b"content-type", b"text/html; charset=utf-8"),
            (b"content-length", str(len(payload)).encode("ascii")),
            (b"x-chirp-middleware", b"forum"),
        ],
        payload,
    )


def _not_found() -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    body = b"not found"
    return (
        404,
        [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
        body,
    )


async def _forum_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    if scope["type"] == "lifespan":
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                scope["state"]["tenants"] = dict(_TENANTS)
                scope["state"]["started"] = True
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
        return

    if scope["type"] != "http":
        return

    path = scope["path"]
    tenant = _tenant_name(scope)

    if path == "/events":
        await receive()
        event = json.dumps({"tenant": tenant, "event": "connected"})
        payload = f"event: forum\ndata: {event}\n\n".encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream"),
                    (b"cache-control", b"no-cache"),
                    (b"x-chirp-middleware", b"forum"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload, "more_body": False})
        return

    if scope["method"] == "POST" and path == "/threads/1/reply":
        form = parse_qs((await _read_body(receive)).decode("utf-8"))
        reply = html.escape(form.get("body", [""])[0])
        status, headers, body = _html_response(
            f"<main><h1>{html.escape(tenant)}</h1><p class=\"thread\">{reply}</p></main>"
        )
    elif path == "/":
        await receive()
        items = "".join(
            f'<li><a href="/threads/{thread_id}">{html.escape(thread["title"])}</a></li>'
            for thread_id, thread in _THREADS.items()
        )
        status, headers, body = _html_response(
            f'<main><h1>{html.escape(tenant)}</h1><ul>{items}</ul></main>'
        )
    elif path.startswith("/threads/"):
        await receive()
        thread_id = path.removeprefix("/threads/").strip("/")
        thread = _THREADS.get(thread_id)
        if thread is None:
            status, headers, body = _not_found()
        else:
            posts = "".join(
                f'<article class="thread">{html.escape(post)}</article>' for post in thread["posts"]
            )
            status, headers, body = _html_response(
                f'<main><h1>{html.escape(thread["title"])}</h1>{posts}</main>'
            )
    else:
        await receive()
        status, headers, body = _not_found()

    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


app = StaticFiles(
    _forum_app,
    mounts=[
        StaticMount(
            url_path="/assets",
            directory=_ASSET_DIR,
            cache_control="public, max-age=300",
        ),
    ],
)

__all__ = ["app"]
