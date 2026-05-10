---
title: Performance
description: What makes Pounce fast and how the streaming-first design works
draft: false
weight: 20
lang: en
type: doc
tags: [performance, streaming]
keywords: [performance, benchmarks, streaming, compression, zstd]
category: explanation
---

## The Fast Path

Pounce's sync workers use a built-in HTTP/1.1 parser optimized for the common
request-head path. Local benchmark snapshots have measured it around **~3 us per
request** versus h11 around **~22 us** on the same parser microbenchmark, but
public performance claims should include the command, hardware, Python
build, workload, and variance. This isn't a C extension; it's pure Python using
direct `bytes.find()` operations on a `memoryview` buffer.

The fast parser has explicit tests for:
- Method token validation
- Header size limit (16 KB, matching nginx default)
- Null byte and control character injection detection
- Duplicate Content-Length rejection (request smuggling vector)
- Content-Length + Transfer-Encoding conflict detection (RFC 7230 section 3.3.3)
- Negative or non-numeric Content-Length rejection

On free-threaded Python, sync workers handle simple request/response at full thread parallelism. When a response requires streaming or WebSocket, the worker hands off to a dedicated async pool — asyncio overhead is only paid when needed.

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

## HTTP Parsing

Pounce uses two HTTP/1.1 parser paths: `_fast_h1` for sync workers and h11 for
async workers. Both are pure Python and designed for free-threading use. The fast parser
handles the common request-head path; chunked body decoding, obs-fold, and
trailer headers fall through to h11 or the async pool.

## CPU Affinity (Linux)

On Linux, you can pin each worker to a dedicated CPU core with `--cpu-affinity`. This reduces cache thrashing and can improve throughput on multi-core systems:

```bash
pounce myapp:app --workers 8 --cpu-affinity
```

No-op on non-Linux platforms or when `sched_setaffinity` fails (e.g. restricted cpusets in containers).

## AcceptDistributor

On macOS and Windows, where `SO_REUSEPORT` is unavailable, multi-worker servers suffer from thundering herd: all workers wake on every new connection, only one wins.

Pounce solves this with the AcceptDistributor — a single thread that calls `accept()` and distributes connections via per-worker queues. Fair distribution, zero contention.

This activates automatically when running multi-worker thread mode with a shared socket. On Linux with `SO_REUSEPORT`, the kernel handles distribution natively.

## Benchmarking

Pounce includes a built-in benchmark command:

```bash
# Run standard benchmarks (hello, json, body echo)
pounce bench --workers 4 --duration 10

# Compare against uvicorn
pounce bench --workers 4 --compare
```

Reports throughput (req/s), latency percentiles (p50, p95, p99), error rates, and RSS memory usage.

## See Also

- [[docs/deployment/compression|Compression]] — Configuration details
- [[docs/deployment/workers|Workers]] — Tuning worker count
- [[docs/about/comparison|Comparison]] — Performance vs other servers
