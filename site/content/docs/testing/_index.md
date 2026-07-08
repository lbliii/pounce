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

## Multi-instance SSE

`RoundRobinTestProxy` pins each incoming TCP connection to the next real
`TestServer`. It preserves long-lived response bytes and is intended for tests
such as cross-instance EventStream fan-out; it is not a production proxy and
does not add forwarding headers.

```python
from pounce.testing import RoundRobinTestProxy, TestServer


with (
    TestServer(app_a) as instance_a,
    TestServer(app_b) as instance_b,
    RoundRobinTestProxy([instance_a, instance_b]) as proxy,
):
    assert proxy.url.startswith("http://")
    # Open SSE clients through proxy.url; successive TCP connections alternate
    # between instance_a and instance_b.
```
