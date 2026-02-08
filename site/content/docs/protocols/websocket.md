---
title: WebSocket
description: WebSocket support via wsproto, including WebSocket over HTTP/2
draft: false
weight: 30
lang: en
type: doc
tags: [websocket, wsproto, protocol, realtime]
keywords: [websocket, wsproto, ws, realtime, bidirectional, h2]
category: reference
---

## Overview

WebSocket support is provided via the `wsproto` library. Install the extra:

```bash
uv add "bengal-pounce[ws]"
```

Pounce supports:

- **Standard WebSocket** — Upgrade from HTTP/1.1
- **WebSocket over HTTP/2** — Multiplexed with other HTTP/2 streams
- **Per-message compression** — Via the permessage-deflate extension

## ASGI WebSocket Lifecycle

WebSocket connections follow the ASGI WebSocket spec:

1. **Connect** — `websocket.connect` event received
2. **Accept/Reject** — App sends `websocket.accept` or `websocket.close`
3. **Messages** — Bidirectional `websocket.receive` and `websocket.send`
4. **Disconnect** — `websocket.disconnect` event

```python
async def websocket_app(scope, receive, send):
    assert scope["type"] == "websocket"

    # Wait for connection
    event = await receive()
    assert event["type"] == "websocket.connect"

    # Accept the connection
    await send({"type": "websocket.accept"})

    # Echo messages
    while True:
        event = await receive()
        if event["type"] == "websocket.disconnect":
            break
        await send({
            "type": "websocket.send",
            "text": event.get("text", ""),
        })
```

## WebSocket over HTTP/2

When both `pounce[h2]` and `pounce[ws]` are installed, WebSocket connections can be established over HTTP/2 using the extended CONNECT method (RFC 8441). This allows WebSocket streams to be multiplexed with regular HTTP/2 requests on the same connection.

## Configuration

WebSocket connections respect the same timeout and limit settings as HTTP connections:

| Setting | Applies To |
|---------|-----------|
| `keep_alive_timeout` | Idle WebSocket connections |
| `max_connections` | Total connections (HTTP + WebSocket) |
| `shutdown_timeout` | Graceful close during shutdown |

## See Also

- [[docs/protocols/http1|HTTP/1.1]] — WebSocket upgrade source
- [[docs/protocols/http2|HTTP/2]] — WebSocket over H2
- [[docs/reference/api|API Reference]] — ASGI type definitions
