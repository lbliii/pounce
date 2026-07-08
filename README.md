# =^..^= Pounce

[![PyPI version](https://img.shields.io/pypi/v/bengal-pounce.svg)](https://pypi.org/project/bengal-pounce/)
[![Build Status](https://github.com/lbliii/pounce/actions/workflows/ci.yml/badge.svg)](https://github.com/lbliii/pounce/actions/workflows/ci.yml)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-blue.svg)](https://pypi.org/project/bengal-pounce/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Status: Beta](https://img.shields.io/badge/status-beta-yellow.svg)](https://pypi.org/project/bengal-pounce/)

**Pure Python ASGI server for Python 3.14t, with a frozen config model and a low-overhead HTTP/1.1 fast path.**

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

Pounce's built-in HTTP/1.1 parser is optimized for the sync worker hot path, its
`ServerConfig` object is frozen after construction, and thread-worker reloads
use generational worker swaps with drain behavior.

On Python 3.14t, worker threads share one interpreter and one copy of your app. On GIL
builds, Pounce falls back to multi-process workers automatically.

**Why people pick it:**

- **ASGI-first** — Runs standard ASGI apps with CLI and programmatic entry points
- **Free-threading native** — True thread parallelism with a frozen shared `ServerConfig`
- **Fast-path parsing** — Built-in HTTP/1.1 parser for sync workers with tested smuggling and header-limit checks
- **Protocol extras** — HTTP/2, HTTP/3, and WebSocket are install-gated optional paths with protocol-specific support boundaries
- **Thread-worker reloads** — Rolling restart uses generational worker swap with drain behavior on supported worker modes
- **Observable surfaces** — Typed lifecycle events, optional Prometheus metrics, OpenTelemetry, and Server-Timing headers
- **Optional helpers** — TLS, compression, static files, middleware, rate limiting, and request queueing stay opt-in
- **Migration path** — Familiar CLI for teams moving from Uvicorn-style deployments

See [docs/design/core-contract.md](docs/design/core-contract.md) for the supported core,
optional helpers, and proof required for public claims.

## Use Pounce For

- **Serving ASGI apps** — Tunable workers, TLS, graceful shutdown, and deployment controls
- **Free-threaded Python deployments** — Shared-memory worker threads on Python 3.14t
- **Streaming workloads** — Server-sent events, streamed HTML, and token-by-token responses
- **Teams migrating from Uvicorn** — Similar CLI shape with a different worker model

---

## Framework Compatibility

Tested in CI with 48 integration tests across every major ASGI framework:

| Framework | Status | Features Verified |
|-----------|--------|-------------------|
| **FastAPI** 0.135+ | Full | Routing, Pydantic validation, dependency injection, middleware, exception handlers, lifespan, WebSocket, streaming, OpenAPI schema |
| **Starlette** 1.0+ | Full | Routing, BaseHTTPMiddleware, lifespan with state, streaming, WebSocket, background tasks, exception handlers |
| **Django** 6.0+ | Full | Async views, URL routing, path params, JSON body, query params, middleware, error handling |
| **Litestar** 2.21+ | Core | Routing, dependency injection, middleware, lifespan, streaming, exception handlers. WebSocket: known routing issue |

Pounce achieves compatibility through correct ASGI 3.0 implementation — no framework-specific code or workarounds.

---

## Performance

Pounce is designed to make the pure-Python request path competitive while keeping
the server core free of C extensions. In a sustained Chirp-shaped
[GitHub Actions snapshot](https://github.com/lbliii/pounce/actions/runs/28981909028),
four Pounce workers completed every request at a fixed 1,000 req/s target:

| Python / Pounce mode | Completed req/s | p99 | p999 | Peak RSS | Errors |
|---|---:|---:|---:|---:|---:|
| 3.14 / processes | 1,000 | 0.436 ms | 0.721 ms | 196.7 MiB | 0 |
| 3.14t / threads | 1,000 | 0.443 ms | 0.521 ms | 108.0 MiB | 0 |

The thread-mode sample used about 45% less aggregate peak RSS than the process
sample. These are medians from three 120-second samples on GitHub-hosted Ubuntu
runners with four persistent connections, not maximum-throughput results or
universal targets. The [benchmark evidence notes](benchmarks/README.md) include
the uvicorn, Hypercorn, and Granian results, scheduler drops, commands, and raw
artifact caveats.

Run `pounce bench --workers 4 --compare` to reproduce on your machine.
For release or PR evidence, use
`python benchmarks/run_benchmark.py --repeat 5 --artifact-output results.json`
so the run carries the metadata required by `benchmarks/artifact-schema.json`
and grouped variance across samples. The scheduled/release workflow adds
two-minute fixed-rate p50/p99/p999 evidence for Python 3.14 process workers and
Python 3.14t thread workers, with uvicorn, Hypercorn, and Granian comparisons
where supported.

Key optimizations in the sync worker path:
- **Fast HTTP/1.1 parser** — Direct bytes parsing is benchmarked separately from h11 and covers method validation, header size limits, duplicate `Content-Length`, and `Content-Length`/`Transfer-Encoding` ambiguity
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
| **CLI** | `pounce serve --app myapp:app` |
| **Multi-worker** | `pounce serve --app myapp:app --workers 4` |
| **TLS** | `pounce serve --app myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem` |
| **HTTP/3** | `pounce serve --app myapp:app --http3 --ssl-certfile cert.pem --ssl-keyfile key.pem` |
| **Dev reload** | `pounce serve --app myapp:app --reload` |
| **App factory** | `pounce serve --app myapp:create_app()` |
| **Testing** | `with TestServer(app) as server: ...` |

Most settings also live in `pounce.toml` or `pyproject.toml` under `[tool.pounce]`.
Run `pounce config schema --output-format toml-template` for every available field.

---

## Features

| Feature | Description | Docs |
|---------|-------------|------|
| **Deployment** | Production workers, compression, observability, and shutdown behavior | [Deployment →](https://lbliii.github.io/pounce/docs/deployment/) |
| **Migration** | Move from Uvicorn with similar CLI concepts | [Migrate from Uvicorn →](https://lbliii.github.io/pounce/docs/tutorials/migrate-from-uvicorn/) |
| **HTTP/1.1** | h11 (async) + fast built-in parser (sync) | [HTTP/1.1 →](https://lbliii.github.io/pounce/docs/protocols/http1/) |
| **HTTP/2** | Optional stream multiplexing via h2 | [HTTP/2 →](https://lbliii.github.io/pounce/docs/protocols/http2/) |
| **HTTP/3** | Optional QUIC/UDP via bengal-zoomies, with real-socket lifecycle and benchmark proof | [HTTP/3 →](https://lbliii.github.io/pounce/docs/protocols/http3/) |
| **WebSocket** | Optional RFC 6455 support via wsproto; WS-over-H2 requires h2 + ws extras | [WebSocket →](https://lbliii.github.io/pounce/docs/protocols/websocket/) |
| **Static Files** | Pre-compressed files, ETags, range requests | [Static Files →](https://lbliii.github.io/pounce/docs/features/static-files/) |
| **Middleware** | ASGI3 middleware stack support | [Middleware →](https://lbliii.github.io/pounce/docs/features/middleware/) |
| **OpenTelemetry** | Optional distributed tracing (OTLP) | [OpenTelemetry →](https://lbliii.github.io/pounce/docs/deployment/observability/) |
| **Lifecycle Logging** | Structured JSON event logging | [Logging →](https://lbliii.github.io/pounce/docs/features/lifecycle-logging/) |
| **Graceful Shutdown** | Mode-scoped connection draining for deploys | [Shutdown →](https://lbliii.github.io/pounce/docs/deployment/lifecycle/) |
| **Dev Error Pages** | Rich tracebacks with syntax highlighting | [Errors →](https://lbliii.github.io/pounce/docs/features/error-pages/) |
| **TLS** | SSL with truststore integration | [TLS →](https://lbliii.github.io/pounce/docs/configuration/tls/) |
| **Compression** | zstd (stdlib PEP 784) + gzip + WS compression | [Compression →](https://lbliii.github.io/pounce/docs/deployment/compression/) |
| **Workers** | Auto-detect: threads (3.14t) or processes (GIL) | [Workers →](https://lbliii.github.io/pounce/docs/deployment/workers/) |
| **Auto Reload** | Graceful restart on file changes | [Reload →](https://lbliii.github.io/pounce/docs/deployment/lifecycle/) |
| **Rate Limiting** | Optional per-IP token bucket with 429 responses | [Rate Limiting →](https://lbliii.github.io/pounce/docs/deployment/backpressure/) |
| **Request Queueing** | Optional bounded queue with 503 load shedding | [Request Queueing →](https://lbliii.github.io/pounce/docs/deployment/backpressure/) |
| **Prometheus** | Optional `/metrics` endpoint | [Metrics →](https://lbliii.github.io/pounce/docs/deployment/observability/) |
| **Sentry** | Optional error tracking and performance monitoring | [Sentry →](https://lbliii.github.io/pounce/docs/deployment/observability/) |
| **Introspection** | Opt-in `/_pounce/info` endpoint for live config, build, and runtime identity (loopback-only by default) | [Introspection →](https://lbliii.github.io/pounce/docs/deployment/observability/) |
| **Testing** | `TestServer` + pytest fixture for integration tests | [Testing →](https://lbliii.github.io/pounce/docs/testing/) |
| **Benchmarking** | Built-in `pounce bench` command with comparative analysis | [Bench →](https://lbliii.github.io/pounce/docs/features/) |
| **Lifecycle Events** | Public API for typed connection/request events | [API →](https://lbliii.github.io/pounce/docs/reference/api/) |

📚 **Full documentation**: [lbliii.github.io/pounce](https://lbliii.github.io/pounce/) | **[Complete Feature List →](https://lbliii.github.io/pounce/docs/features/)**

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

With `workers=1`, Pounce uses the direct single-worker async path. With multiple
workers and `worker_mode="auto"`, **Python 3.14t** resolves to sync thread
workers in one shared process, while a **GIL build** resolves to async process
workers. The supervisor detects the runtime through `sys._is_gil_enabled()`.
Startup output and `/_pounce/info` expose the resolved model.

`worker_mode="subinterpreter"` is explicit and uses isolated async workers even
when `workers=1`; embedded `Server` callers must provide an importable
`app_path="module:attribute"`.

A request flows through: socket accept -> protocol parser -> ASGI scope
construction -> `app(scope, receive, send)` -> response serialization -> socket write.
Async workers use h11; sync workers use a fast built-in parser for lower latency.

</details>

<details>
<summary><strong>Protocol Extras</strong> — Install only what you need</summary>

| Protocol | Backend | Install |
|----------|---------|---------|
| HTTP/1.1 | h11 (async) / fast built-in parser (sync) | built-in |
| HTTP/2 | h2 (stream multiplexing, priority signals) | `bengal-pounce[h2]` |
| WebSocket | wsproto (HTTP/1 WebSocket; WS-over-H2 also requires h2) | `bengal-pounce[ws]` |
| TLS | stdlib ssl + truststore | `bengal-pounce[tls]` |
| HTTP/3 | bengal-zoomies (QUIC/UDP) | `bengal-pounce[h3]` |
| All | Everything above | `bengal-pounce[full]` |

Compression uses Python 3.14's stdlib `compression.zstd` — zero external dependencies.

</details>

<details>
<summary><strong>Testing</strong> — Real server for integration tests</summary>

```python
from pounce.testing import RoundRobinTestProxy, TestServer
import httpx

def test_homepage(my_app):
    with TestServer(my_app) as server:
        resp = httpx.get(f"{server.url}/")
        assert resp.status_code == 200
```

The `pounce_server` pytest fixture is auto-registered when pounce is installed:

```python
def test_api(pounce_server, my_app):
    server = pounce_server(my_app)
    resp = httpx.get(f"{server.url}/health")
    assert resp.status_code == 200
```

For multi-instance HTTP/SSE tests, run two `TestServer` instances behind
`RoundRobinTestProxy([first, second])`; each new TCP connection is pinned to
the next backend.

</details>

---

## Key Ideas

- **Free-threading first.** Threads, not processes. One interpreter, N event loops, and a
  frozen shared `ServerConfig`. On GIL builds, falls back to multi-process automatically.
- **Pure Python.** No Rust, no C extensions in the server core. Debuggable, hackable,
  readable.
- **Typed end-to-end.** Frozen config, typed ASGI definitions, and no new type suppressions without review.
- **Lean dependencies.** Two required runtime deps: `h11` for HTTP/1.1 parsing and `milo-cli` for the CLI. The request hot path depends only on `h11`; everything else is optional.
- **Observable by design.** Lifecycle events are public API — `from pounce import BufferedCollector, ResponseCompleted`. Frameworks build dashboards on typed events, not log parsing.
- **Framework tested.** Verified against FastAPI, Starlette, Django, and Litestar with 48 integration tests.
- **Optional helpers.** Static files, middleware, rate limiting, request queueing,
  Prometheus metrics, Sentry, and OpenTelemetry are available without becoming
  mandatory request-path dependencies.

---

## Documentation

📚 **[lbliii.github.io/pounce](https://lbliii.github.io/pounce/)**

| Section | Description |
|---------|-------------|
| [Get Started](https://lbliii.github.io/pounce/docs/get-started/) | Installation and quickstart |
| [Protocols](https://lbliii.github.io/pounce/docs/protocols/) | HTTP/1.1, HTTP/2, WebSocket, HTTP/3 |
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

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, feedback loops, and recipes
(how to add a test, a config field, or an error). Read [AGENTS.md](AGENTS.md)
for the project's design philosophy and stop-and-ask escape hatches.

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
