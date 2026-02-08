---
title: Quickstart
description: Serve your first ASGI application with Pounce
draft: false
weight: 20
lang: en
type: doc
tags: [quickstart, tutorial]
keywords: [quickstart, asgi, hello world, first app]
category: onboarding
---

## Create an ASGI App

Create a file called `app.py`:

```python
async def app(scope, receive, send):
    """Minimal ASGI application."""
    assert scope["type"] == "http"

    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            [b"content-type", b"text/plain"],
        ],
    })
    await send({
        "type": "http.response.body",
        "body": b"Hello from Pounce!",
    })
```

## Serve It

:::{tab-set}

:::{tab-item} Command Line

```bash
pounce app:app
```

:::

:::{tab-item} Programmatic

```python
import pounce

pounce.run("app:app")
```

:::

:::{/tab-set}

Open `http://127.0.0.1:8000` in your browser. You should see "Hello from Pounce!".

## Configure Workers

On Python 3.14t (free-threading), workers are threads. On GIL builds, workers are processes. The API is the same:

```bash
# Auto-detect worker count (based on CPU cores)
pounce app:app --workers 0

# Explicit 4 workers
pounce app:app --workers 4
```

## Enable Development Reload

```bash
pounce app:app --reload
```

Pounce watches your source files and restarts workers automatically when changes are detected.

## Add Protocol Extras

```bash
# HTTP/2 + WebSocket
pounce app:app  # after: pip install bengal-pounce[h2,ws]

# TLS termination
pounce app:app --ssl-certfile cert.pem --ssl-keyfile key.pem
```

## App Factory Pattern

Pounce supports the app factory pattern with callable syntax:

```bash
pounce myapp:create_app()
```

This calls `create_app()` in the `myapp` module and uses the returned ASGI application.

## Next Steps

- [[docs/configuration/server-config|ServerConfig]] — All configuration options
- [[docs/protocols|Protocols]] — HTTP/1.1, HTTP/2, and WebSocket details
- [[docs/deployment|Deployment]] — Production configuration and scaling
