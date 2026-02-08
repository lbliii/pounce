---
title: TLS
description: Setting up TLS termination for HTTPS and HTTP/2
draft: false
weight: 30
lang: en
type: doc
tags: [tls, ssl, https, security]
keywords: [tls, ssl, https, certificate, alpn, http2]
category: how-to
---

## Overview

Pounce supports TLS termination using Python's stdlib `ssl` module, with optional `truststore` integration for system certificate stores.

## Basic Setup

```bash
pounce myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem
```

Or programmatically:

```python
import pounce

pounce.run(
    "myapp:app",
    ssl_certfile="cert.pem",
    ssl_keyfile="key.pem",
)
```

:::{note}
Both `ssl_certfile` and `ssl_keyfile` must be provided together. Setting only one raises `ValueError`.
:::

## Self-Signed Certificates (Development)

For local development, generate a self-signed certificate:

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem \
    -days 365 -nodes -subj '/CN=localhost'
```

Then run:

```bash
pounce myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem
```

Your browser will warn about the self-signed certificate — this is expected in development.

## ALPN and HTTP/2

When TLS is enabled and `pounce[h2]` is installed, Pounce advertises both `h2` and `http/1.1` via ALPN (Application-Layer Protocol Negotiation). Clients that support HTTP/2 will automatically use it.

## Truststore Integration

For production, install the `truststore` extra for system certificate store integration:

```bash
uv add "bengal-pounce[tls]"
```

This uses the operating system's trusted CA certificates instead of certifi or a bundled CA file.

## Reverse Proxy

In many production setups, TLS is terminated at a reverse proxy (nginx, Caddy, etc.) and Pounce receives plain HTTP. In this case:

1. Don't set `ssl_certfile` / `ssl_keyfile` on Pounce
2. Set `root_path` if the proxy serves at a subpath
3. Set `trusted_hosts` to your proxy's address

```python
pounce.run(
    "myapp:app",
    root_path="/api",
    trusted_hosts=("127.0.0.1",),
)
```

## See Also

- [[docs/protocols/http2|HTTP/2]] — Requires TLS with ALPN
- [[docs/deployment/production|Production]] — Full production setup
- [[docs/configuration/server-config|ServerConfig]] — All TLS options
