# Pounce Roadmap

**Current version:** 0.4.0 (March 2026)
**Horizon:** April – September 2026

---

## Where We Are

Pounce is the only pure-Python ASGI server with full protocol coverage — HTTP/1.1, HTTP/2, HTTP/3, and WebSocket — built from the ground up for free-threaded Python 3.14t.

No C extensions. No Rust toolchain. `pip install` and go.

| Capability | Pounce | Uvicorn 0.42 | Granian 2.7 | Hypercorn |
|---|---|---|---|---|
| HTTP/1.1 | Yes | Yes | Yes | Yes |
| HTTP/2 | Yes | No | Yes | Yes |
| HTTP/3 (QUIC) | Yes | No | No | Yes (aioquic) |
| WebSocket | Yes | Yes | Yes | Yes |
| Free-threaded workers | Yes (native) | Untested | Yes (v2.0+) | No |
| Pure Python | Yes | Yes (uvloop optional) | No (Rust) | Yes |
| Built-in compression | zstd + gzip | No | No | No |
| Static file serving | Yes | No | No | No |

**Current performance:** ~30k req/s on the sync H1 path (parity with Uvicorn), fast built-in parser (~3 µs/req). Granian's Rust I/O layer is ~3x faster on raw throughput — but Granian can't serve HTTP/3, can't compress responses, and requires a Rust compiler to install.

**The opening:** Uvicorn has had HTTP/2 "on the roadmap" since 2017. It still ships HTTP/1.1 only. Python 3.14 has been GA for 5 months. Free-threading is supported, not experimental. The ecosystem is catching up — major libraries are adding 3.14t compatibility. Pounce is positioned to be the default ASGI server for the free-threaded era.

---

## Q2 2026 (April – June) — Make the Case

### Subinterpreter Workers

First ASGI server to ship a subinterpreter worker mode via `concurrent.interpreters` (PEP 734, shipped in 3.14 stdlib). A third worker mode alongside threads and processes — thread-like performance with process-like memory isolation. This is the headline feature for Q2.

### Framework Compatibility

Test and certify compatibility with every major ASGI framework:

- **FastAPI** — Full compatibility matrix, integration guide, pitch for inclusion in FastAPI deployment docs
- **Starlette** — Baseline compatibility (FastAPI depends on this)
- **Litestar** — Growing community, active maintainers
- **Django** (ASGI mode) — Largest potential user base
- **chirp** — Bengal ecosystem integration

The goal is not just "it works" — it's getting Pounce mentioned as a recommended server in framework documentation.

### Published Benchmarks

Reproducible, public benchmarks across realistic workloads:

- **HTTP/1.1 JSON API** — Pounce vs Uvicorn vs Granian (level playing field)
- **HTTP/2 multiplexed** — Pounce vs Granian vs Hypercorn (Uvicorn can't participate)
- **HTTP/3 QUIC** — Pounce vs Hypercorn (Granian can't participate)
- **WebSocket echo** — All servers
- **Static files** — Pounce vs Nginx (demonstrate built-in serving is viable)
- **SSE streaming** — Pounce vs Uvicorn

Publish as a dedicated site section with methodology, reproduction scripts, and hardware specs.

### Configuration File Support

`pounce.toml` or `[tool.pounce]` in `pyproject.toml`. Projects outgrow CLI flags fast — especially with 50+ config options. This unblocks adoption for teams with complex deployments.

---

## Q3 2026 (July – September) — Production Confidence

### io_uring Backend (Linux)

Batched I/O via io_uring for accept, read, and write operations. Third-party Python io_uring libraries are showing 36% throughput gains over standard asyncio. Ship as an opt-in backend (`--io-backend uring`) — standard asyncio remains the default.

### Compression Dictionary Transport (RFC 9842)

Delta compression using shared zstd dictionaries. Dramatically smaller responses for API endpoints with repetitive JSON payloads. Chrome has supported this since 123+. This is genuine innovation — no other Python ASGI server offers it.

### Memory Profiling & Leak Detection

Establish baseline memory profiles for all three worker modes (thread, process, subinterpreter) under sustained load. Identify and fix any leaks in long-running deployments. Publish results.

### Cloud Deployment Guides

- **Docker** — Optimized Dockerfile, multi-stage build, health check configuration
- **AWS Lambda** — Mangum-style adapter or native integration
- **Cloud Run / Fly.io** — Container-first deployment with HTTP/2 and graceful shutdown
- **Kubernetes** — Readiness/liveness probes, graceful termination

### Python 3.15 Beta Compatibility

3.15.0b1 expected mid-2026. Validate Pounce against each beta. Stay ahead of the release cycle — don't be the server that breaks on new Python.

### Stretch Goals

- **WebTransport** (HTTP/3 datagrams) — Bidirectional unreliable messaging over QUIC. Ship if spec and browser support mature enough.
- **sendfile()** — Zero-copy file transfer for static files. Nice optimization, not a headline.
- **Graceful reload improvements** — Smoother zero-downtime SIGHUP under high concurrency.

---

## Adoption & Community

This is not optional. A server nobody knows about is a server nobody uses.

- **PyCon US 2026** — Talk proposal: "ASGI Beyond HTTP/1.1: Serving HTTP/2, HTTP/3, and WebSocket in Pure Python"
- **Blog series** — "Why your ASGI server can't speak HTTP/2" (targeting Uvicorn users), "Free-threaded Python in production" (targeting early adopters)
- **Framework outreach** — PRs to FastAPI, Litestar, and Django docs adding Pounce as a deployment option
- **Migration guides** — Uvicorn (done), Hypercorn, Daphne, Gunicorn+Uvicorn workers

---

## Non-Goals

Pounce deliberately does not:

- **Include application-level logic.** No routing, no templates. Server-level middleware (CORS, security headers) and static file serving are opt-in server features.
- **Include a process manager.** Pounce manages its own workers but doesn't replace systemd or container orchestration.
- **Support Python < 3.14.** Free-threading is the reason Pounce exists.
- **Support WSGI.** ASGI only. WSGI apps can use an ASGI adapter.
- **Replace Nginx.** Pounce is an application server, not a reverse proxy.
- **Chase Rust on raw throughput.** Granian will always be faster at moving bytes. Pounce wins on protocol coverage, zero dependencies, and Python-native deployment.

---

## The Bengal Ecosystem

```
pounce      ASGI server       (serves apps)
chirp       Web framework     (builds apps)
kida        Template engine   (renders HTML)
patitas     Markdown parser   (parses content)
rosettes    Syntax highlighter (highlights code)
bengal      Static site gen   (builds sites)
```

Each tool is independent. Together they form a complete web platform for Python 3.14t.
