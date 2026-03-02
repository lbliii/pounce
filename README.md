# =^..^= Pounce

[![PyPI version](https://img.shields.io/pypi/v/bengal-pounce.svg)](https://pypi.org/project/bengal-pounce/)
[![Build Status](https://github.com/lbliii/pounce/actions/workflows/ci.yml/badge.svg)](https://github.com/lbliii/pounce/actions/workflows/ci.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://pypi.org/project/bengal-pounce/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Status: Beta](https://img.shields.io/badge/status-beta-yellow.svg)](https://pypi.org/project/bengal-pounce/)

**A free-threading-native ASGI server for Python 3.14t.**

```python
import pounce

pounce.run("myapp:app")
```

---

## What is Pounce?

Pounce is a free-threading-native ASGI server for Python 3.14t. N worker threads share one interpreter, one copy of your app, one set of frozen config. On GIL builds, it falls back to multi-process automatically.

**What's good about it:**

- **Free-threading native** — Threads, not processes. One interpreter, N event loops, shared immutable state. Zero synchronization for immutable data.
- **2026-native features** — Stdlib `compression.zstd` (PEP 784) for zero-dependency zstd content-encoding. Server-Timing headers for built-in observability.
- **Streaming-first** — Chunked HTML, event streams, AI token delivery. Body chunks sent immediately to socket, never buffered.

---

## Installation

```bash
pip install bengal-pounce
```

Requires Python 3.14+

**Optional extras:**

```bash
pip install bengal-pounce[fast]   # httptools C-accelerated HTTP/1.1
pip install bengal-pounce[h2]     # HTTP/2 stream multiplexing
pip install bengal-pounce[ws]     # WebSocket via wsproto
pip install bengal-pounce[tls]    # TLS with truststore
pip install bengal-pounce[h3]     # HTTP/3 (QUIC/UDP, requires TLS)
pip install bengal-pounce[full]   # Everything above (except httptools)
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
| **HTTP/1.1** | h11 (pure Python) or httptools (C-accelerated) | [HTTP/1.1 →](https://lbliii.github.io/pounce/docs/protocols/http1/) |
| **HTTP/2** | Stream multiplexing via h2 | [HTTP/2 →](https://lbliii.github.io/pounce/docs/protocols/http2/) |
| **HTTP/3** | QUIC/UDP via aioquic (requires TLS) | — |
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

A request flows through: socket accept -> protocol parser (h11 or httptools) -> ASGI scope
construction -> `app(scope, receive, send)` -> response serialization -> socket write.

</details>

<details>
<summary><strong>Protocol Extras</strong> — Install only what you need</summary>

| Protocol | Backend | Install |
|----------|---------|---------|
| HTTP/1.1 | h11 (pure Python, default) | built-in |
| HTTP/1.1 | httptools (C-accelerated) | `pounce[fast]` |
| HTTP/2 | h2 (stream multiplexing, priority signals) | `pounce[h2]` |
| WebSocket | wsproto (including WS over H2) | `pounce[ws]` |
| TLS | stdlib ssl + truststore | `pounce[tls]` |
| All | Everything above (except httptools) | `pounce[full]` |

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
