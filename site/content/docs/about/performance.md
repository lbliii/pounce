---
title: Performance
description: What makes Pounce fast and how the streaming-first design works
draft: false
weight: 20
lang: en
type: doc
tags: [performance, benchmarks, streaming]
keywords: [performance, benchmarks, streaming, compression, zstd]
category: explanation
---

## Streaming-First Design

The dominant response patterns of modern web applications — chunked HTML, server-sent events, AI token delivery — are all streaming. Pounce's response pipeline is designed around this reality:

1. **No buffering** — Response body chunks flow from `send()` directly to the socket
2. **Per-chunk compression** — Zstd and gzip compressors operate in streaming mode
3. **Immediate delivery** — Each chunk is written to the wire as soon as it's ready

This means time-to-first-byte (TTFB) is determined by your application, not by server buffering.

## Memory Model

The shared-memory architecture provides a fundamental advantage over fork-based servers:

| Workers | Pounce (threads) | Fork-based (processes) |
|---------|-------------------|------------------------|
| 1 | 1x app memory | 1x app memory |
| 4 | ~1x app memory | ~4x app memory |
| 8 | ~1x app memory | ~8x app memory |

On Python 3.14t, all workers share the same interpreter, the same application object, and the same frozen configuration. Immutable data requires zero synchronization.

## Compression

Pounce negotiates content-encoding automatically via `Accept-Encoding`:

| Encoding | Library | Priority | Notes |
|----------|---------|----------|-------|
| zstd | `compression.zstd` (stdlib) | Highest | PEP 784, zero-dependency |
| gzip | `zlib` (stdlib) | Medium | Universal browser support |
| identity | — | Fallback | No compression |

Zstd provides better compression ratios than gzip at lower CPU cost — and in Python 3.14, it's in the standard library.

Compression is skipped for:
- Responses smaller than `compression_min_size` (default: 500 bytes)
- Already-compressed content types (images, video, archives)
- WebSocket frames

## Server-Timing

When `server_timing=True`, Pounce injects a `Server-Timing` header into every response:

```
Server-Timing: parse;dur=0.12, app;dur=4.56, encode;dur=0.34
```

This appears directly in browser DevTools (Network tab → Timing), enabling zero-config latency profiling.

## Connection Handling

- **Backpressure** — Per-worker connection limits prevent overload
- **Keep-alive** — Configurable timeout (default: 5s) to reuse TCP connections
- **SO_REUSEPORT** — Kernel-level load balancing across workers
- **Graceful shutdown** — In-flight requests complete before workers exit

## Fused Sync Path (Chirp)

When Chirp runs behind Pounce with no middleware, sync handlers that return `dict`, `list`, `str`, or `bytes` use a fused path that bypasses ASGI and the HTTP protocol layer. Pounce uses:

- **Reusable recv buffer** — `recv_into()` with a per-worker `bytearray` to avoid per-request allocations
- **Scatter-gather send** — `sendmsg([head, body])` when available to avoid concatenating response head and body

## See Also

- [[docs/deployment/compression|Compression]] — Configuration details
- [[docs/deployment/workers|Workers]] — Tuning worker count
- [[docs/about/comparison|Comparison]] — Performance vs other servers
