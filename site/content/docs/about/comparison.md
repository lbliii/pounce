---
title: When to Use Pounce
description: Pounce's architecture and when it fits
draft: false
weight: 40
lang: en
type: doc
tags: [architecture, deployment]
keywords: [architecture, deployment, asgi, server]
category: explanation
---

# When to Use Pounce

Pounce is built for Python 3.14t and the free-threading model.

## Pounce's Model

- **Thread-based parallelism** — N worker threads share one interpreter, one copy of your app
- **Shared memory** — Lower memory footprint than process-based workers
- **Streaming-first** — Body chunks sent immediately to socket
- **Pure Python** — One dependency (h11). Debuggable, hackable, readable
- **Optional extras** — HTTP/2, WebSocket, TLS via `pounce[h2]`, `pounce[ws]`, `pounce[tls]`

## When Pounce Fits

- You're on **Python 3.14t** and want thread-based parallelism
- You want **shared memory** across workers (lower memory footprint)
- You need **streaming responses** with minimal latency
- You want **stdlib compression** (zstd) without external dependencies
- You prefer **pure Python** for debuggability and extensibility

## When to Consider Alternatives

- **Django Channels** — If you need deep Django integration, other servers may have more mature support
- **Existing deployments** — If your current setup works and you're not on 3.14t, there's no urgent reason to switch

## See Also

- [[docs/about/performance|Performance]] — Benchmarks and design
- [[docs/about/thread-safety|Thread Safety]] — The shared memory model
