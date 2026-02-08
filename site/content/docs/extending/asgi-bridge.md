---
title: ASGI Bridge
description: How Pounce translates protocol events to the ASGI interface
draft: false
weight: 10
lang: en
type: doc
tags: [asgi, bridge, internals]
keywords: [asgi, bridge, scope, receive, send, lifecycle]
category: explanation
---

## Overview

The ASGI bridge is the layer between Pounce's protocol parsers and your ASGI application. It constructs the `scope` dict, creates `receive` and `send` callables, and manages the per-request lifecycle.

## HTTP Bridge

For HTTP requests, the bridge:

1. **Builds scope** — Extracts method, path, headers, query string from the parsed request
2. **Creates receive** — Returns request body chunks as `http.request` events
3. **Creates send** — Accepts `http.response.start` and `http.response.body` events
4. **Tracks state** — Monitors response status, headers sent, body complete

```python
# Simplified flow
scope = build_scope(request, config)
receive = create_receive(request_body)
send = create_send(connection, config)

await app(scope, receive, send)
```

## Scope Construction

The ASGI scope follows the ASGI HTTP Connection Scope specification:

```python
{
    "type": "http",
    "asgi": {"version": "3.0", "spec_version": "2.4"},
    "http_version": "1.1",  # or "2"
    "method": "GET",
    "path": "/",
    "root_path": "",
    "scheme": "https",
    "query_string": b"",
    "headers": [(b"host", b"example.com"), ...],
    "server": ("127.0.0.1", 8000),
    "client": ("192.168.1.1", 54321),
}
```

## Streaming Send

The `send` callable writes response chunks directly to the socket:

```python
# Your ASGI app sends:
await send({"type": "http.response.start", "status": 200, "headers": [...]})
await send({"type": "http.response.body", "body": b"chunk1", "more_body": True})
await send({"type": "http.response.body", "body": b"chunk2", "more_body": True})
await send({"type": "http.response.body", "body": b"", "more_body": False})

# Pounce writes each chunk to the socket immediately — no buffering
```

## Disconnect Detection

Pounce monitors the client connection concurrently. If the client disconnects mid-request, your app receives a `http.disconnect` event from `receive()`:

```python
event = await receive()
if event["type"] == "http.disconnect":
    # Client disconnected — clean up and return
    return
```

## Lifespan Bridge

The lifespan bridge handles application startup and shutdown:

1. Send `lifespan.startup` event
2. Wait for `lifespan.startup.complete` or `lifespan.startup.failed`
3. *(server runs)*
4. Send `lifespan.shutdown` event
5. Wait for `lifespan.shutdown.complete`

## See Also

- [[docs/about/architecture|Architecture]] — Full pipeline overview
- [[docs/reference/api|API Reference]] — ASGI type definitions
