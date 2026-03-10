---
title: When to Use Pounce
description: When Pounce fits, how it differs from process-based ASGI servers, and when to consider alternatives
draft: false
weight: 40
lang: en
type: doc
tags: [architecture, deployment]
keywords: [python asgi server, uvicorn alternative, architecture, deployment, free-threading]
category: explanation
---

# When to Use Pounce

Pounce is built for Python 3.14t and the free-threading model. If you are evaluating
Python ASGI servers, the main distinction is its worker model: threads on 3.14t,
automatic process fallback on GIL builds.

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
- You want a **Uvicorn-like CLI** with a different concurrency model

## When to Consider Alternatives

- **Uvicorn** — A reasonable choice if you want a more familiar default in process-based deployments
- **Hypercorn** — Worth considering if your team already depends on its deployment model or feature set
- **Django Channels** — If you need deep Django integration, other servers may have more mature support
- **Existing deployments** — If your current setup works and you're not on 3.14t, there's no urgent reason to switch

## See Also

- [[docs/about/performance|Performance]] — Benchmarks and design
- [[docs/about/thread-safety|Thread Safety]] — The shared memory model
