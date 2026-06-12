"""
Host-based multi-tenant routing — Chirp-shaped HTML-over-the-wire.

The Chirp flagship serves many tenants from one process behind a managed load
balancer, picking the tenant from the request authority (the ``Host`` header).
This example shows the copyable pattern: read ``Host`` from the ASGI scope,
map it to a tenant, and render per-tenant HTML.

Proxy trust is enforced by the **server**, not this app:

- With ``trusted_hosts`` empty (the safe default), pounce strips every inbound
  ``X-Forwarded-*`` header, so a client cannot spoof its authority — the app
  only ever sees the real ``Host``.
- When the direct peer IS in ``trusted_hosts``, pounce rewrites the scope
  ``Host`` (and ``scope["server"]``) from ``X-Forwarded-Host`` before dispatch
  (see ``pounce._proxy.apply_proxy_headers``).  This app reads the resulting
  ``Host`` either way, so the same tenant-resolution code is correct behind a
  trusted proxy and on a direct connection.

So: behind a managed LB you set ``trusted_hosts`` to the LB's peer IP(s) and the
LB's ``X-Forwarded-Host`` drives tenant selection.  Without it, an untrusted
client's ``X-Forwarded-Host`` is ignored and only the real ``Host`` is honored.

Run it (direct, no proxy trust)::

    pounce serve --app examples.multi_tenant_app:app

    curl -H 'Host: alpha.example' http://127.0.0.1:8000/
    curl -H 'Host: beta.example'  http://127.0.0.1:8000/

Behind a trusted proxy, configure trust in code (see build_config below) and
launch with ``python examples/multi_tenant_app.py``.

"""

import html
import os
from typing import Any

from pounce import ServerConfig, run

# Map request authority (Host, lowercased, port stripped) to a tenant label.
# In a real app this would be a database/registry lookup.
_TENANTS: dict[str, str] = {
    "alpha.example": "Alpha Company",
    "beta.example": "Beta Company",
    "localhost": "Local Tenant",
    "127.0.0.1": "Local Tenant",
}
_DEFAULT_TENANT = "Public Tenant"


def _host(scope: dict[str, Any]) -> str:
    """Extract the lowercased, port-stripped Host from the ASGI scope.

    Reads the scope ``Host`` header, which pounce has already rewritten from a
    trusted proxy's ``X-Forwarded-Host`` when ``trusted_hosts`` is configured
    (and otherwise left as the client's real Host).  Mirrors the host-extraction
    pattern in ``benchmarks/apps/chirp_forum.py``.
    """
    for name, value in scope["headers"]:
        if name == b"host":
            return value.decode("latin-1").split(":", 1)[0].lower()
    return "localhost"


def _tenant(scope: dict[str, Any]) -> str:
    return _TENANTS.get(_host(scope), _DEFAULT_TENANT)


async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Resolve the tenant from Host and render per-tenant HTML."""
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

    tenant = _tenant(scope)
    body = (
        f"<!doctype html><html><body>"
        f"<h1>{html.escape(tenant)}</h1>"
        f"<p>Served by pounce multi-tenant routing.</p>"
        f"</body></html>"
    ).encode()

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/html; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"x-tenant", tenant.encode("utf-8")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_config() -> ServerConfig:
    """Server config for running behind a managed load balancer.

    ``trusted_hosts`` is read from ``TRUSTED_PROXY_IPS`` (comma-separated) so you
    can opt into honoring ``X-Forwarded-Host`` ONLY for confirmed LB peer IPs.
    When unset, trust stays OFF and forwarded headers are stripped — the safe
    default.
    """
    raw = os.environ.get("TRUSTED_PROXY_IPS", "")
    trusted = frozenset(ip.strip() for ip in raw.split(",") if ip.strip())
    return ServerConfig(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        trusted_hosts=trusted,
        log_format="json",
    )


if __name__ == "__main__":
    run(app, config=build_config())
