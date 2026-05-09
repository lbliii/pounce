---
title: Testing
description: Run integration tests against a real Pounce server
draft: false
weight: 80
lang: en
type: doc
tags: [testing, pytest, integration]
keywords: [testing, pytest, testserver, integration]
category: guide
---

Pounce ships a testing helper that starts a real server in a background thread.
Use it when your test needs socket-level behavior instead of direct ASGI calls.

## Context Manager

```python
from pounce.testing import TestServer


async def app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def test_app():
    with TestServer(app) as server:
        assert server.url.startswith("http://")
```

## Pytest Fixture

Installing `bengal-pounce` registers the `pounce_server` pytest fixture through
the package entry point.

```python
def test_with_fixture(pounce_server):
    with pounce_server(app) as server:
        assert server.is_running
```

The helper uses Pounce's normal listener, worker, lifespan, and shutdown paths,
so it catches integration bugs that a direct ASGI harness would miss.
