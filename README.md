# =^..^= Pounce

[![PyPI version](https://img.shields.io/pypi/v/bengal-pounce.svg)](https://pypi.org/project/bengal-pounce/)
[![Build Status](https://github.com/lbliii/pounce/actions/workflows/ci.yml/badge.svg)](https://github.com/lbliii/pounce/actions/workflows/ci.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://pypi.org/project/bengal-pounce/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Status: Beta](https://img.shields.io/badge/status-beta-yellow.svg)](https://pypi.org/project/bengal-pounce/)

**A Python ASGI server for production apps, streaming responses, and free-threaded Python.**

```python
import pounce

pounce.run("myapp:app")
```

---

## What is Pounce?

Pounce is a Python ASGI server for Python 3.14+, with a worker model designed for
free-threaded Python 3.14t. It runs standard ASGI applications, supports streaming
responses, and gives you a clear upgrade path from process-based servers such as
Uvicorn.

On Python 3.14t, worker threads share one interpreter and one copy of your app. On GIL
builds, Pounce falls back to multi-process workers automatically.

**Why people pick it:**

- **ASGI-first** — Runs standard ASGI apps with CLI and programmatic entry points
- **Free-threading ready** — Threads, not processes, on Python 3.14t
- **Streaming-first** — Chunked HTML, event streams, and token streaming without buffering
- **Four protocols** — HTTP/1.1, HTTP/2, HTTP/3 (QUIC), and WebSocket (including WS over H2)
- **Batteries included** — TLS, compression, static files, middleware, rate limiting, observability
- **Migration path** — Familiar CLI for teams moving from Uvicorn-style deployments

## Use Pounce For

- **Serving ASGI apps** — Tunable workers, TLS, graceful shutdown, and deployment controls
- **Free-threaded Python deployments** — Shared-memory worker threads on Python 3.14t
- **Streaming workloads** — Server-sent events, streamed HTML, and token-by-token responses
- **Teams migrating from Uvicorn** — Similar CLI shape with a different worker model

---

## Performance

Pounce matches uvicorn on multi-worker throughput — pure Python, no C extensions.

| Scenario | Pounce | Uvicorn | Notes |
|----------|--------|---------|-------|
| 1 worker | ~12k req/s | ~12k req/s | Async event loop, h11 parser |
| 4 workers (threads) | ~30k req/s | ~30k req/s | Linear scaling on Python 3.14t |

*Measured with `wrk -t4 -c100 -d10s` on macOS, plain-text "hello world" ASGI app.*

Key optimizations in the sync worker path:
- **Fast HTTP/1.1 parser** — Direct bytes parsing (~3 µs/req) replaces h11 (~22 µs/req) with full safety checks (method validation, header size limits, request smuggling detection)
- **Keep-alive connections** — Connection reuse eliminates TCP handshake overhead
- **Shared socket distribution** — Single accept queue for thread workers avoids macOS SO_REUSEPORT limitations

---

## Installation

```bash
pip install bengal-pounce
```

Requires Python 3.14+

**Optional extras:**

```bash
pip install bengal-pounce[h2]     # HTTP/2 stream multiplexing
pip install bengal-pounce[ws]     # WebSocket via wsproto
pip install bengal-pounce[tls]    # TLS with truststore
pip install bengal-pounce[h3]     # HTTP/3 (QUIC/UDP, requires TLS)
pip install bengal-pounce[full]   # All protocol extras
```

---

## Quick Start

| Usage | Command |
|-------|---------|
| **Programmatic** | `pounce.run("myapp:app")` |
| **CLI** | `pounce myapp:app` |
| **Multi-worker** | `pounce myapp:app --workers 4` |
| **TLS** | `pounce myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem` |
| **HTTP/3** | `pounce myapp:app --http3 --ssl-certfile cert.pem --ssl-keyfile key.pem` |
| **Dev reload** | `pounce myapp:app --reload` |
| **App factory** | `pounce myapp:create_app()` |

---

## Features

| Feature | Description | Docs |
|---------|-------------|------|
| **Deployment** | Production workers, compression, observability, and shutdown behavior | [Deployment →](https://lbliii.github.io/pounce/docs/deployment/) |
| **Migration** | Move from Uvicorn with similar CLI concepts | [Migrate from Uvicorn →](https://lbliii.github.io/pounce/docs/tutorials/migrate-from-uvicorn/) |
| **HTTP/1.1** | h11 (async) + fast built-in parser (sync) | [HTTP/1.1 →](https://lbliii.github.io/pounce/docs/protocols/http1/) |
| **HTTP/2** | Stream multiplexing via h2 | [HTTP/2 →](https://lbliii.github.io/pounce/docs/protocols/http2/) |
| **HTTP/3** | QUIC/UDP via bengal-zoomies (requires TLS) | [HTTP/3 →](docs/design/http3-roadmap.md) |
| **WebSocket** | Full RFC 6455 via wsproto (including WS over H2) | [WebSocket →](https://lbliii.github.io/pounce/docs/protocols/websocket/) |
| **Static Files** | Zero-copy sendfile, pre-compressed, ETags | [Static Files →](docs/features/) |
| **Middleware** | ASGI3 middleware stack support | [Middleware →](docs/features/) |
| **OpenTelemetry** | Native distributed tracing (OTLP) | [OpenTelemetry →](docs/deployment/opentelemetry.md) |
| **Lifecycle Logging** | Structured JSON event logging | [Logging →](docs/features/lifecycle-logging.md) |
| **Graceful Shutdown** | Kubernetes-ready connection draining | [Shutdown →](docs/deployment/graceful-shutdown.md) |
| **Dev Error Pages** | Rich tracebacks with syntax highlighting | [Errors →](docs/development/error-pages.md) |
| **TLS** | SSL with truststore integration | [TLS →](https://lbliii.github.io/pounce/docs/configuration/tls/) |
| **Compression** | zstd (stdlib PEP 784) + gzip + WS compression | [Compression →](https://lbliii.github.io/pounce/docs/deployment/compression/) |
| **Workers** | Auto-detect: threads (3.14t) or processes (GIL) | [Workers →](https://lbliii.github.io/pounce/docs/deployment/workers/) |
| **Auto Reload** | Graceful restart on file changes | [Reload →](docs/deployment/graceful-reload.md) |
| **Rate Limiting** | Per-IP token bucket with 429 responses | [Rate Limiting →](docs/deployment/rate-limiting.md) |
| **Request Queueing** | Bounded queue with 503 load shedding | [Request Queueing →](docs/deployment/request-queueing.md) |
| **Prometheus** | Built-in `/metrics` endpoint | [Metrics →](docs/deployment/prometheus-metrics.md) |
| **Sentry** | Error tracking and performance monitoring | [Sentry →](docs/deployment/sentry.md) |

📚 **Full documentation**: [lbliii.github.io/pounce](https://lbliii.github.io/pounce/) | **[Complete Feature List →](docs/FEATURES.md)**

---

## Usage

<details>
<summary><strong>Programmatic Configuration</strong> — Full control from Python</summary>

```python
import pounce

pounce.run(
    "myapp:app",
    host="0.0.0.0",
    port=8000,
    workers=4,
)
```

</details>

<details>
<summary><strong>How It Works</strong> — Adaptive worker model</summary>

On **Python 3.14t** (free-threading): workers are threads. One process, N threads, each with
its own asyncio event loop. Shared memory, no fork overhead, no IPC.

On **GIL builds**: workers are processes. Same API, same config. The supervisor detects the
runtime via `sys._is_gil_enabled()` and adapts automatically.

A request flows through: socket accept -> protocol parser -> ASGI scope
construction -> `app(scope, receive, send)` -> response serialization -> socket write.
Async workers use h11; sync workers use a fast built-in parser for lower latency.

</details>

<details>
<summary><strong>Protocol Extras</strong> — Install only what you need</summary>

| Protocol | Backend | Install |
|----------|---------|---------|
| HTTP/1.1 | h11 (async) / fast built-in parser (sync) | built-in |
| HTTP/2 | h2 (stream multiplexing, priority signals) | `pounce[h2]` |
| WebSocket | wsproto (including WS over H2) | `pounce[ws]` |
| TLS | stdlib ssl + truststore | `pounce[tls]` |
| All | Everything above | `pounce[full]` |

Compression uses Python 3.14's stdlib `compression.zstd` — zero external dependencies.

</details>

---

## Key Ideas

- **Free-threading first.** Threads, not processes. One interpreter, N event loops, shared
  immutable state. On GIL builds, falls back to multi-process automatically.
- **Pure Python.** No Rust, no C extensions in the server core. Debuggable, hackable,
  readable.
- **Typed end-to-end.** Frozen config, typed ASGI definitions, zero `type: ignore` comments.
- **One dependency.** `h11` for HTTP/1.1 parsing. Everything else is optional.
- **Observable.** Structured lifecycle events — frozen dataclasses with nanosecond timestamps.
  Zero overhead when no collector is attached.
- **Chirp companion.** Built to serve Chirp apps natively, but works with any ASGI framework.
- **Batteries included.** Static files, middleware, rate limiting, request queueing,
  Prometheus metrics, Sentry, and OpenTelemetry — all built in, all optional.

---

## Documentation

📚 **[lbliii.github.io/pounce](https://lbliii.github.io/pounce/)**

| Section | Description |
|---------|-------------|
| [Get Started](https://lbliii.github.io/pounce/docs/get-started/) | Installation and quickstart |
| [Protocols](https://lbliii.github.io/pounce/docs/protocols/) | HTTP/1.1, HTTP/2, WebSocket |
| [Configuration](https://lbliii.github.io/pounce/docs/configuration/) | Server config, TLS, CLI |
| [Deployment](https://lbliii.github.io/pounce/docs/deployment/) | Workers, compression, production |
| [Extending](https://lbliii.github.io/pounce/docs/extending/) | ASGI bridge, custom protocols |
| [Tutorials](https://lbliii.github.io/pounce/docs/tutorials/) | Uvicorn migration guide |
| [Troubleshooting](https://lbliii.github.io/pounce/docs/troubleshooting/) | Common issues and fixes |
| [Reference](https://lbliii.github.io/pounce/docs/reference/) | API documentation |
| [About](https://lbliii.github.io/pounce/docs/about/) | Architecture, performance, FAQ |

---

## Development

```bash
git clone https://github.com/lbliii/pounce.git
cd pounce
uv sync --group dev
pytest
```

---

## The Bengal Ecosystem

A structured reactive stack — every layer written in pure Python for 3.14t free-threading.

| | | | |
|--:|---|---|---|
| **ᓚᘏᗢ** | [Bengal](https://github.com/lbliii/bengal) | Static site generator | [Docs](https://lbliii.github.io/bengal/) |
| **∿∿** | [Purr](https://github.com/lbliii/purr) | Content runtime | — |
| **⌁⌁** | [Chirp](https://github.com/lbliii/chirp) | Web framework | [Docs](https://lbliii.github.io/chirp/) |
| **=^..^=** | **Pounce** | ASGI server ← You are here | [Docs](https://lbliii.github.io/pounce/) |
| **)彡** | [Kida](https://github.com/lbliii/kida) | Template engine | [Docs](https://lbliii.github.io/kida/) |
| **ฅᨐฅ** | [Patitas](https://github.com/lbliii/patitas) | Markdown parser | [Docs](https://lbliii.github.io/patitas/) |
| **⌾⌾⌾** | [Rosettes](https://github.com/lbliii/rosettes) | Syntax highlighter | [Docs](https://lbliii.github.io/rosettes/) |

Python-native. Free-threading ready. No npm required.

---

## License

MIT
