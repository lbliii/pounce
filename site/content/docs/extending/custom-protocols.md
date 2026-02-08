---
title: Custom Protocols
description: The ProtocolHandler protocol and how to extend Pounce
draft: false
weight: 20
lang: en
type: doc
tags: [protocols, extending, custom]
keywords: [protocol, handler, custom, extending, interface]
category: explanation
---

## ProtocolHandler Protocol

Pounce's protocol handlers follow a common `Protocol` (in the `typing.Protocol` sense). Each handler implements a consistent interface for feeding bytes and extracting events.

The existing handlers are:

| Handler | Protocol | Module |
|---------|----------|--------|
| `H1Protocol` | HTTP/1.1 (h11) | `protocols/h1.py` |
| `H1HttptoolsProtocol` | HTTP/1.1 (httptools) | `protocols/h1_httptools.py` |
| `H2Protocol` | HTTP/2 (h2) | `protocols/h2.py` |
| `WSProtocol` | WebSocket (wsproto) | `protocols/ws.py` |

## Interface

Each protocol handler manages the state machine for its protocol:

1. **Feed bytes** — Raw bytes from the socket are fed to the handler
2. **Extract events** — The handler yields parsed events (requests, data frames, etc.)
3. **Generate bytes** — Response events are serialized back to bytes
4. **Track state** — Connection state (idle, active, closing) is maintained

## Extending

Pounce's protocol system is modular by design. Each protocol handler is a self-contained module with no dependencies on other handlers. The worker selects the appropriate handler based on connection type (plain HTTP, TLS+ALPN, WebSocket upgrade).

:::{note}
The protocol handler interface is internal and may change between releases. If you're building a custom protocol handler, pin your Pounce version.
:::

## See Also

- [[docs/protocols|Protocols]] — Built-in protocol handlers
- [[docs/extending/asgi-bridge|ASGI Bridge]] — How handlers connect to ASGI
- [[docs/about/architecture|Architecture]] — Where protocols fit in the pipeline
