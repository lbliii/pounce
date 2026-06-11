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

::::{tab-set}
::::{tab-item} Command Line
```bash
pounce serve --app app:app
```
::::{/tab-item}

::::{tab-item} Programmatic
```python
import pounce

pounce.run("app:app")
```
::::{/tab-item}
::::{/tab-set}

Open `http://127.0.0.1:8000` in your browser. You should see "Hello from Pounce!".

## Enable Development Reload

```bash
pounce serve --app app:app --reload
```

Pounce watches your source files and restarts workers automatically when changes are detected.

## Configure Workers

```bash
# Auto-detect worker count (based on CPU cores)
pounce serve --app app:app --workers 0

# Explicit 4 workers
pounce serve --app app:app --workers 4
```

::::{note}
`--workers 0` auto-detects based on CPU cores. The default is 1 (single worker). On free-threaded Python 3.14t, workers run as threads sharing one interpreter. With the GIL enabled, workers run as separate processes.
::::

::::{dropdown} App Factory Pattern
Pounce supports the app factory pattern with callable syntax:

```bash
pounce serve --app myapp:create_app()
```

This calls `create_app()` in the `myapp` module and uses the returned ASGI application.
::::

## Next Steps

::::{related}
:limit: 3
:section_title: Next Steps
::::
