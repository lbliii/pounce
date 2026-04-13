"""
Static file serving example with custom mount points and cache control.

Demonstrates:
- Serving static files from multiple directories
- Custom cache control per mount
- Custom MIME types for modern file extensions
- Combining static files with a dynamic ASGI app

Run it:
    python examples/static_files.py

Then visit:
    http://127.0.0.1:8000/            - Dynamic API response
    http://127.0.0.1:8000/static/     - Files from ./public/
    http://127.0.0.1:8000/assets/     - Files from ./dist/ (long cache)

"""

from pathlib import Path

from pounce import StaticFiles, run
from pounce._static import StaticMount


# A simple dynamic ASGI app for non-static routes.
async def app(scope, receive, send):
    """Return a JSON response for API requests."""
    if scope["type"] != "http":
        return

    await receive()
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b'{"message": "Hello from the dynamic app"}',
        }
    )


if __name__ == "__main__":
    # Ensure example directories exist (for demonstration purposes).
    Path("./public").mkdir(exist_ok=True)
    Path("./dist").mkdir(exist_ok=True)

    # Wrap the dynamic app with StaticFiles middleware.
    static_app = StaticFiles(
        app,
        mounts=[
            # General static files with a short cache.
            StaticMount(
                url_path="/static",
                directory=Path("./public"),
                cache_control="public, max-age=3600",
            ),
            # Build assets with a long cache and custom MIME types.
            StaticMount(
                url_path="/assets",
                directory=Path("./dist"),
                cache_control="public, max-age=31536000, immutable",
                extra_mime_types={".map": "application/json"},
            ),
        ],
    )

    run(static_app, host="127.0.0.1", port=8000)
