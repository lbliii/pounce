"""Bengal-shaped static site benchmark app."""

from pathlib import Path
from typing import Any

from pounce import StaticFiles
from pounce._static import StaticMount

_SITE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "bengal_site"


async def _fallback(scope: dict[str, Any], receive: Any, send: Any) -> None:
    await receive()
    body = b"not found"
    await send(
        {
            "type": "http.response.start",
            "status": 404,
            "headers": [
                (b"content-type", b"text/plain"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


app = StaticFiles(
    _fallback,
    mounts=[
        StaticMount(
            url_path="/",
            directory=_SITE_DIR,
            cache_control="public, max-age=0",
            extra_mime_types={".xml": "application/atom+xml"},
        ),
    ],
)

__all__ = ["app"]
