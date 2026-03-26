---
title: Production
description: Running Pounce in production environments
draft: false
weight: 30
lang: en
type: doc
tags: [production, deployment, hardening, scaling]
keywords: [production, deployment, reverse-proxy, nginx, caddy, systemd, docker, unix-socket, health-check]
category: how-to
---

## Recommended Configuration

```bash
pounce myapp:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 0 \
    --log-level warning \
    --log-format json \
    --header-timeout 10 \
    --health-check-path /health \
    --shutdown-timeout 15
```

## Behind a Reverse Proxy

In most production setups, Pounce runs behind a reverse proxy (nginx, Caddy, etc.) that handles TLS termination, static files, and load balancing.

### Nginx (TCP)

```nginx
upstream pounce {
    server 127.0.0.1:8000;
}

server {
    listen 443 ssl http2;
    server_name example.com;

    ssl_certificate /etc/ssl/cert.pem;
    ssl_certificate_key /etc/ssl/key.pem;

    location / {
        proxy_pass http://pounce;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # Health check — bypass logging for probe traffic
    location = /health {
        proxy_pass http://pounce;
        access_log off;
    }
}
```

### Nginx (Unix Domain Socket)

Using a Unix socket eliminates TCP overhead and is the recommended approach when nginx and Pounce run on the same host.

```bash
pounce myapp:app --uds /run/pounce.sock --workers 0
```

```nginx
upstream pounce {
    server unix:/run/pounce.sock;
}

server {
    listen 443 ssl http2;
    server_name example.com;

    ssl_certificate /etc/ssl/cert.pem;
    ssl_certificate_key /etc/ssl/key.pem;

    location / {
        proxy_pass http://pounce;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
    }
}
```

### Caddy

```caddyfile
example.com {
    reverse_proxy 127.0.0.1:8000
}
```

Caddy handles TLS automatically via Let's Encrypt.

### Proxy Header Trust

When behind a reverse proxy, configure `trusted_hosts` so Pounce honours `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host` from trusted peers. Without this, proxy headers are stripped for security.

```python
import pounce

pounce.run(
    "myapp:app",
    trusted_hosts=frozenset({"10.0.0.1"}),  # Your proxy's IP (tuples also accepted)
)
```

Or via CLI:

```bash
# Trust is configured via ServerConfig (no CLI flag — intentionally
# requires programmatic configuration for safety)
```

:::{warning}
Never set `trusted_hosts=("*",)` in internet-facing deployments. A wildcard trusts every peer, allowing any client to spoof their IP via `X-Forwarded-For`.
:::

## Health Checks

Pounce has a built-in health check endpoint that responds before the ASGI app is invoked — fast, lightweight, and independent of your application.

```bash
pounce myapp:app --health-check-path /health
```

The endpoint returns a JSON payload:

```json
{
    "status": "ok",
    "uptime_seconds": 3600.1,
    "worker_id": 0,
    "active_connections": 42
}
```

Health check requests are excluded from access logs to reduce noise from Kubernetes probes, load balancer checks, and monitoring agents.

### Kubernetes Probes

```yaml
livenessProbe:
    httpGet:
        path: /health
        port: 8000
    initialDelaySeconds: 5
    periodSeconds: 10
readinessProbe:
    httpGet:
        path: /health
        port: 8000
    initialDelaySeconds: 2
    periodSeconds: 5
```

## Request Tracing

Every request is assigned a unique `X-Request-ID` (UUID4 hex). The ID appears in:

- **Response headers** — `X-Request-ID` header on every response
- **ASGI scope** — `scope["extensions"]["request_id"]` for app-level access
- **Access logs** — Both text and JSON formats include the request ID

If a trusted proxy sends `X-Request-ID`, Pounce uses that value instead of generating a new one. This enables end-to-end tracing across services.

## Systemd Service

```ini
[Unit]
Description=Pounce ASGI Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/myapp
ExecStart=/opt/myapp/.venv/bin/pounce myapp:app \
    --host 0.0.0.0 \
    --workers 0 \
    --health-check-path /health \
    --log-format json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Unix Socket with Systemd

```ini
[Unit]
Description=Pounce ASGI Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/myapp
ExecStart=/opt/myapp/.venv/bin/pounce myapp:app \
    --uds /run/pounce.sock \
    --workers 0 \
    --health-check-path /health \
    --log-format json
Restart=always
RestartSec=5
RuntimeDirectory=pounce

[Install]
WantedBy=multi-user.target
```

## Docker

```dockerfile
FROM python:3.14t-slim

WORKDIR /app
COPY . .
RUN pip install bengal-pounce[full] .

EXPOSE 8000
HEALTHCHECK CMD curl -f http://localhost:8000/health || exit 1
CMD ["pounce", "myapp:app", "--host", "0.0.0.0", "--workers", "0", "--health-check-path", "/health", "--log-format", "json"]
```

## Graceful Shutdown

Pounce handles `SIGINT` and `SIGTERM`:

1. Stop accepting new connections
2. Wait for in-flight requests to complete (up to `shutdown_timeout`)
3. Send ASGI lifespan shutdown event
4. Clean up Unix socket file (if using UDS)
5. Exit cleanly

## Connection Backpressure

When the connection limit is reached (`max_connections`), Pounce returns an HTTP `503 Service Unavailable` response with a `Retry-After: 5` header instead of silently dropping the connection. This gives clients actionable feedback.

## Security Checklist

- [ ] Run behind a reverse proxy for TLS termination
- [ ] Set `trusted_hosts` to your proxy's IP address
- [ ] Set appropriate `max_request_size` for your application
- [ ] Use `--header-timeout` to protect against slowloris attacks (default: 10s)
- [ ] Use `--log-format json` for structured logging (parseable by log aggregators)
- [ ] Set `server_header` to a generic value (or empty) to avoid version fingerprinting
- [ ] Use `--workers 0` for auto-scaled parallelism
- [ ] Enable `--health-check-path /health` for load balancer integration

## See Also

- [[docs/deployment/security|Security]] — Proxy headers, CRLF protection, request smuggling
- [[docs/deployment/observability|Observability]] — Health checks, request IDs, Prometheus metrics
- [[docs/configuration/tls|TLS]] — Direct TLS termination
- [[docs/deployment/workers|Workers]] — Worker count tuning
- [[docs/configuration/server-config|ServerConfig]] — All configuration options
