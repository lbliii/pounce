---
title: Production
description: Running Pounce in production environments
draft: false
weight: 30
lang: en
type: doc
tags: [production, deployment, hardening, scaling]
keywords: [production, deployment, reverse-proxy, nginx, caddy, systemd, docker]
category: how-to
---

## Recommended Configuration

```bash
pounce myapp:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 0 \
    --no-access-log \
    --log-level warning \
    --compression \
    --request-timeout 30 \
    --shutdown-timeout 15
```

## Behind a Reverse Proxy

In most production setups, Pounce runs behind a reverse proxy (nginx, Caddy, etc.) that handles TLS termination, static files, and load balancing.

### Nginx

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

        # WebSocket support
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
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

## Systemd Service

```ini
[Unit]
Description=Pounce ASGI Server
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/myapp
ExecStart=/opt/myapp/.venv/bin/pounce myapp:app --host 0.0.0.0 --workers 0
Restart=always
RestartSec=5

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
CMD ["pounce", "myapp:app", "--host", "0.0.0.0", "--workers", "0"]
```

## Graceful Shutdown

Pounce handles `SIGINT` and `SIGTERM`:

1. Stop accepting new connections
2. Wait for in-flight requests to complete (up to `shutdown_timeout`)
3. Send ASGI lifespan shutdown event
4. Exit cleanly

## Security Checklist

- [ ] Run behind a reverse proxy for TLS termination
- [ ] Set `trusted_hosts` to your proxy's address
- [ ] Set appropriate `max_request_size` for your application
- [ ] Disable `access_log` in production (or pipe to a log aggregator)
- [ ] Set `server_header` to a generic value (or empty) to avoid version fingerprinting
- [ ] Use `--workers 0` for auto-scaled parallelism

## See Also

- [[docs/configuration/tls|TLS]] — Direct TLS termination
- [[docs/deployment/workers|Workers]] — Worker count tuning
- [[docs/configuration/server-config|ServerConfig]] — All configuration options
