"""Compatibility entrypoint for the official Railway deployment recipe.

Railway (and most PaaS platforms: Render, Fly, Heroku, Cloud Run) inject the
port the process must listen on via the ``PORT`` environment variable and
terminate public TLS at the platform edge.  The container therefore speaks
plain HTTP on ``0.0.0.0:$PORT`` — you do **not** set ``ssl_certfile`` /
``ssl_keyfile`` / ``http3_enabled`` here.

The complete Dockerfile, Railway config, and deploy/redeploy smoke proof live in
``examples/deploy/railway``. This module preserves the original import and run
path for users of the earlier single-file example.

Key choices:
- ``host="0.0.0.0"`` so the platform router can reach the process.
- ``port=int(os.environ.get("PORT", "8000"))`` reads the injected port and
  falls back to 8000 for local runs.
- ``health_check_path="/readyz"`` so the platform healthcheck (which gates
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
        --health-check-path /readyz --log-format json --no-access-log

Or run this module directly (it reads PORT itself)::

    PORT=8000 python examples/railway_deploy.py
    curl http://127.0.0.1:8000/readyz
    curl http://127.0.0.1:8000/

"""

from examples.deploy.railway.app import app, build_config
from pounce import run

if __name__ == "__main__":
    run(app, config=build_config())
