---
title: Railway
description: Deploying Pounce on Railway public networking
draft: false
weight: 35
lang: en
type: doc
tags: [railway, deployment, production, health-checks]
keywords: [railway, port, health-check, platform-tls, lb-sonic, chirp]
category: how-to
---

Railway public networking expects the web process to listen on the
Railway-provided `PORT` variable on `0.0.0.0`. Railway terminates public
TLS at the platform edge for HTTP services, so the usual Pounce deployment is
plain HTTP inside the container with `trusted_hosts` configured only after you
confirm the ingress peer addresses for your service.

## Start Command

Use a small entrypoint when your app needs programmatic config:

```python
# railway_app.py
import os

import pounce
from myapp import app


if __name__ == "__main__":
    pounce.run(
        app,
        host="0.0.0.0",
        port=int(os.environ["PORT"]),
        workers=0,
        health_check_path="/health",
        log_format="json",
        access_log=False,
    )
```

Then set the Railway start command to:

```bash
python railway_app.py
```

For a plain import-string app that does not need custom `ServerConfig` values:

```bash
pounce serve --app myapp:app --host 0.0.0.0 --port "$PORT" --workers 0 --health-check-path /health --log-format json --no-access-log
```

## Health Checks

Configure Railway's healthcheck path to `/health`, matching
`health_check_path="/health"`. Railway uses the service `PORT` for healthcheck
traffic, and healthchecks gate deployment activation rather than continuous
monitoring.

If your application rejects unexpected hostnames, allow
`healthcheck.railway.app` for the health endpoint.

## TLS And HTTP/3

Do not set `ssl_certfile`, `ssl_keyfile`, or `http3_enabled=True` for Railway
public HTTP unless you have a separate raw UDP/TCP networking design. Railway's
public HTTP path provides platform TLS and forwards HTTP traffic to the process
port.

Inside the ASGI scope, `scope["scheme"]` will be `http` unless you configure
trusted proxy headers and Railway forwards `X-Forwarded-Proto`. Keep
`trusted_hosts` empty until the ingress trust boundary for the deployment is
known; untrusted `X-Forwarded-*` headers are stripped by default.

## Graceful Deploys

Set Pounce shutdown and Railway drain windows together:

```bash
RAILWAY_DEPLOYMENT_DRAINING_SECONDS=30
```

```python
pounce.run(
    app,
    host="0.0.0.0",
    port=int(os.environ["PORT"]),
    shutdown_timeout=25,
    health_check_path="/health",
)
```

The Railway drain window should be slightly longer than Pounce's
`shutdown_timeout` so in-flight requests can finish before the platform sends a
hard kill.

## Multi-Tenant Host Routing

For Chirp or LB Sonic style host routing, prefer deriving tenant identity from
the ASGI `Host` header. Pounce preserves the request `Host` directly, and when
a trusted proxy supplies `X-Forwarded-Host`, Pounce rewrites both the ASGI
`Host` header and `scope["server"]` to that forwarded authority.

Confirm the actual Railway ingress headers for your deployed service before
enabling `trusted_hosts`. If those details are unknown, leave `trusted_hosts`
empty and route tenants from the normal `Host` header.

## References

- [Railway Public Networking](https://docs.railway.com/public-networking)
- [Railway Healthchecks](https://docs.railway.com/reference/healthchecks)
- [Railway Variables Reference](https://docs.railway.com/reference/variables)
