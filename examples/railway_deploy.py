"""
Railway / PaaS deploy recipe — bind 0.0.0.0 on the injected ``$PORT``.

Railway (and most PaaS platforms: Render, Fly, Heroku, Cloud Run) inject the
port the process must listen on via the ``PORT`` environment variable and
terminate public TLS at the platform edge.  The container therefore speaks
plain HTTP on ``0.0.0.0:$PORT`` — you do **not** set ``ssl_certfile`` /
``ssl_keyfile`` / ``http3_enabled`` here.

This file mirrors the documented recipe in
``site/content/docs/deployment/railway.md`` so the runnable example and the doc
snippet stay in sync.

Key choices:
- ``host="0.0.0.0"`` so the platform router can reach the process.
- ``port=int(os.environ.get("PORT", "8000"))`` reads the injected port and
  falls back to 8000 for local runs.
- ``health_check_path="/health"`` so the platform healthcheck (which gates
  deployment activation) gets a built-in JSON ``{"status": "ok", ...}`` reply
  before the request ever reaches your app.
- ``log_format="json"`` for structured logs the platform can index.
- Platform-terminated TLS: no ``ssl_certfile`` / ``http3_enabled``.
- ``trusted_hosts`` is **empty by default**.  With no trusted hosts, every
  inbound ``X-Forwarded-*`` header is stripped from the ASGI scope, so a client
  cannot spoof its IP, scheme, or authority.  Only enable the commented knobs
  below once you have confirmed the ingress peer addresses for your service.

Run on Railway (start command) — canonical CLI form::

    pounce serve --app examples.railway_deploy:app \
        --host 0.0.0.0 --port "$PORT" \
        --health-check-path /health --log-format json --no-access-log

Or run this module directly (it reads PORT itself)::

    PORT=8000 python examples/railway_deploy.py
    curl http://127.0.0.1:8000/health
    curl http://127.0.0.1:8000/

"""

import json
import os

from pounce import ServerConfig, run


async def app(scope, receive, send):
    """Minimal JSON API that also answers ``/health`` itself.

    The built-in ``health_check_path`` short-circuits ``GET /health`` before
    dispatch, but we also handle it here so the example works identically when
    served without that config (e.g. plain ``pounce serve --app ...`` without
    ``--health-check-path``).
    """
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

    if scope["path"] == "/health":
        body = b'{"status": "ok", "service": "pounce-railway"}'
    else:
        body = json.dumps(
            {
                "message": "Hello from pounce on Railway!",
                # scope["scheme"] stays "http" inside the container: the
                # platform terminates TLS at the edge. It only becomes "https"
                # if a trusted proxy forwards X-Forwarded-Proto AND that peer
                # is listed in trusted_hosts (see build_config below).
                "scheme": scope["scheme"],
            }
        ).encode("utf-8")

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_config() -> ServerConfig:
    """Build the Railway-shaped server config from the environment.

    Reads ``PORT`` (falling back to 8000 for local runs) and binds 0.0.0.0,
    matching the documented Railway recipe.
    """
    return ServerConfig(
        # Bind all interfaces so the platform router reaches the process.
        host="0.0.0.0",
        # Read the platform-injected port; fall back to 8000 for local runs.
        port=int(os.environ.get("PORT", "8000")),
        # Built-in healthcheck the platform can probe; gates deploy activation.
        health_check_path="/health",
        # Structured JSON logs for the platform's log pipeline.
        log_format="json",
        # Platform terminates TLS at the edge — speak plain HTTP in-container.
        # Do NOT set ssl_certfile / ssl_keyfile / http3_enabled for Railway
        # public HTTP.
        #
        # Proxy trust is OFF by default: with trusted_hosts empty, every
        # X-Forwarded-* header is stripped so clients cannot spoof their IP,
        # scheme, or Host. Only enable these once you confirm the ingress peer
        # addresses for your service:
        #
        #   trusted_hosts=frozenset({"100.64.0.0"}),  # confirmed ingress peer
        #   forwarded_for_trusted_hops=1,
        #
        # Keep the platform drain window slightly longer than shutdown_timeout
        # so in-flight requests finish before a hard kill:
        #   shutdown_timeout=25.0,
    )


if __name__ == "__main__":
    run(app, config=build_config())
