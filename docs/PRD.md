# Product Requirements Document: Pounce

**Version**: 0.1.0-dev
**Date**: 2026-02-07
**Status**: Phase 1 implemented

---

## 1. Overview

Pounce is a pure-Python ASGI server built for Python 3.14t's free-threading mode. It uses
real OS threads sharing a single interpreter for parallel request handling — no fork model,
no GIL contention, no per-process memory duplication.

Pounce targets Python 3.14+ and is designed as the production server for the Bengal
ecosystem, with first-class support for chirp applications. It leverages Python 3.14's
new stdlib features — `compression.zstd` for HTTP content encoding, `concurrent.interpreters`
for future isolation models — alongside free-threading for a server that could only exist
in 2026.

---

## 2. Problem Statement

### 2.1 The Gap

Every existing Python ASGI server was designed around the GIL:

| Server | Architecture | GIL Strategy |
|--------|-------------|--------------|
| Uvicorn | Single-threaded event loop | Fork N processes via Gunicorn |
| Hypercorn | Single-threaded event loop | Fork N processes |
| Granian | Rust I/O + Python callbacks | Fork (GIL) / threads (nogil) |
| Daphne | Twisted reactor | Single process |

Python 3.14t removes the GIL. For the first time, Python threads can execute in parallel
without contention. But no ASGI server is built to exploit this:

- **Uvicorn** runs one event loop per process. Under nogil, it could run one event loop per
  thread — sharing the application, config, and route tables — but its architecture assumes
  fork-based isolation.

- **Granian** supports nogil threads, but its I/O layer is Rust. The Python side is a thin
  callback wrapper. There's no pure-Python server that uses nogil threads natively.

- **Hypercorn** has no nogil awareness. Its worker model is process-based.

### 2.2 Specific Gaps

1. **No thread-based worker model.** Existing servers use processes for parallelism. On
   3.14t, threads sharing one interpreter would eliminate fork overhead and memory duplication.

2. **No GIL detection at startup.** No server inspects `sys._is_gil_enabled()` and adapts
   its worker model accordingly.

3. **No shared-memory architecture.** Process-based servers duplicate the application,
   config, route tables, and template environments in each worker. Thread-based workers
   share everything immutable for free.

4. **No pure-Python nogil server.** Granian uses Rust for I/O. There's no server that proves
   free-threaded Python is fast enough to handle HTTP I/O directly.

5. **No ecosystem integration.** Chirp's `app.run()` shells out to uvicorn. A purpose-built
   server could integrate directly — zero config, zero import overhead.

6. **No zstd content-encoding.** Python 3.14 ships `compression.zstd` in the stdlib (PEP 784).
   Browsers support `Accept-Encoding: zstd` — Chrome 123+, Firefox 126+, ~76% global
   coverage. No existing ASGI server negotiates zstd because the stdlib didn't have it until
   now. Every server is stuck on gzip.

7. **No streaming-first architecture.** htmx 4.0 switches to the `fetch()` API with built-in
   streaming response support. LLM APIs stream tokens via SSE. The dominant response patterns
   of 2026 — chunked HTML, event streams, AI token delivery — are all streaming. Existing
   servers handle streaming but don't optimize for it as the primary response path.

---

## 3. Target Users

### 3.1 Primary: Chirp Application Developers

Developers building chirp applications who need a production server. Today they run
`uvicorn myapp:app`. With pounce, they run `pounce myapp:app` and get thread-based
parallelism on 3.14t with zero configuration.

**Needs:**
- Familiar CLI interface (`pounce myapp:app`) — same invocation pattern, fundamentally
  different server: thread-based parallelism, zstd compression, streaming-first pipeline
- Automatic worker scaling based on CPU count
- Graceful shutdown without dropping connections
- Access logging compatible with standard tooling

### 3.2 Secondary: ASGI Framework Authors

Authors of any ASGI framework (Starlette, FastAPI, Litestar) who want to test their
applications under free-threading. Pounce serves as a reference implementation of a
nogil-native ASGI server.

**Needs:**
- Full ASGI 3.0 compliance
- Lifespan protocol support
- WebSocket support (phase 3)
- HTTP/2 support (phase 3)

### 3.3 Tertiary: Performance-Conscious Deployers

Operators running Python web services on memory-constrained infrastructure (containers,
edge, small VMs). Thread-based workers share memory instead of duplicating it per process.

**Needs:**
- Lower memory footprint than multi-process deployments
- Predictable latency under load
- TLS termination without a reverse proxy (phase 3)
- Metrics for monitoring (connections, requests, latency)

---

## 4. Functional Requirements

### 4.1 Core Server (P0 — Must Have)

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| F-001 | ASGI 3.0 interface | Calls `app(scope, receive, send)` correctly |
| F-002 | HTTP/1.1 support | Parse and serve HTTP/1.1 requests via h11 |
| F-003 | ASGI lifespan protocol | Sends startup/shutdown events to the app |
| F-004 | Frozen server config | `ServerConfig` dataclass, immutable at runtime |
| F-005 | Programmatic API | `pounce.run("app:app", host=..., port=...)` |
| F-006 | Graceful shutdown | SIGINT/SIGTERM drain connections, then exit |
| F-007 | Keep-alive | HTTP/1.1 persistent connections with timeout |
| F-008 | Access logging | Method, path, status, timing per request |
| F-009 | Error responses | Malformed requests get 400, server errors get 500 |
| F-010 | Request size limits | Reject oversized headers and bodies |
| F-011 | `root_path` support | Reverse proxy path prefix passed in ASGI scope |
| F-012 | Content-encoding negotiation | `zstd > br > gzip > identity` via `Accept-Encoding` |
| F-013 | Streaming response pipeline | Chunked transfer, SSE, long-lived connections as primary path |

### 4.2 Multi-Worker (P0 — Must Have)

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| F-014 | Thread-based workers (nogil) | N threads, each with own event loop |
| F-015 | Process-based fallback (GIL) | N processes when GIL is enabled |
| F-016 | GIL detection | `sys._is_gil_enabled()` check at startup |
| F-017 | Supervisor lifecycle | Start workers, monitor health, restart on crash |
| F-018 | SO_REUSEPORT | Kernel-level connection distribution (Linux) |
| F-019 | Worker count auto-detect | Default to `os.cpu_count()` workers |

### 4.3 CLI (P1 — Should Have)

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| F-020 | CLI entry point | `pounce myapp:app` starts the server |
| F-021 | Host/port flags | `--host`, `--port` override config |
| F-022 | Worker count flag | `--workers N` sets worker count |
| F-023 | Log level flag | `--log-level debug\|info\|warning\|error` |
| F-024 | Reload flag | `--reload` for development (file watcher) |

### 4.4 Content Encoding (P1 — Should Have)

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| F-025 | Zstd encoding | Compress responses via stdlib `compression.zstd` (zero deps) |
| F-026 | Gzip/deflate encoding | Compress responses via stdlib `zlib` (universal fallback) |
| F-027 | Brotli encoding | Optional `pounce[br]` extra for `Accept-Encoding: br` |
| F-028 | Quality negotiation | Parse `Accept-Encoding` q-values, waterfall: zstd > br > gzip > identity |

### 4.5 HTTP/2 (P2 — Nice to Have)

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| F-029 | HTTP/2 via h2 library | Optional dependency, stream multiplexing |
| F-030 | ALPN negotiation | Automatic H1/H2 selection with TLS |
| F-031 | Server push | ASGI extension for H2 push promises |
| F-032 | WebSocket over H2 (RFC 8441) | Multiplexed WS on single TCP connection |
| F-033 | Priority Signals (RFC 9218) | Respect browser urgency/incremental hints |

### 4.6 WebSocket (P2 — Nice to Have)

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| F-034 | WebSocket upgrade | HTTP/1.1 upgrade to WebSocket |
| F-035 | wsproto integration | Optional dependency for WS parsing |
| F-036 | ASGI websocket scope | Full ASGI WebSocket lifecycle |

### 4.7 TLS (P2 — Nice to Have)

| ID | Requirement | Acceptance Criteria |
|----|-------------|---------------------|
| F-037 | TLS termination | Direct HTTPS without reverse proxy |
| F-038 | Certificate config | `ssl_certfile` and `ssl_keyfile` in config |
| F-039 | ALPN for H2 | Advertise HTTP/2 support via ALPN |

---

## 5. Non-Functional Requirements

### 5.1 Performance

| ID | Requirement | Target |
|----|-------------|--------|
| NF-001 | Throughput (single worker) | > 15,000 req/s for 'Hello World' |
| NF-002 | Throughput (4 workers, nogil) | > 50,000 req/s for 'Hello World' |
| NF-003 | Latency p99 | < 5ms for 'Hello World' at 10k req/s |
| NF-004 | Memory (single worker) | < 20MB RSS idle |
| NF-005 | Memory (4 workers, threads) | < 30MB RSS idle (shared interpreter) |
| NF-006 | Memory (4 workers, processes) | < 80MB RSS idle (separate interpreters) |
| NF-007 | Startup time | < 200ms to first request |
| NF-008 | Connection handling | > 10,000 concurrent connections |

### 5.2 Reliability

| ID | Requirement | Target |
|----|-------------|--------|
| NF-009 | No data races | Zero race conditions under concurrent load on 3.14t |
| NF-010 | Graceful degradation | Backpressure under overload, not crash |
| NF-011 | Worker recovery | Supervisor restarts crashed workers |
| NF-012 | Signal handling | Clean shutdown on SIGINT and SIGTERM |
| NF-013 | Connection cleanup | No leaked file descriptors or sockets |

### 5.3 Developer Experience

| ID | Requirement | Target |
|----|-------------|--------|
| NF-014 | Zero config startup | `pounce myapp:app` works with sane defaults |
| NF-015 | Type checker clean | Zero `type: ignore` in server code |
| NF-016 | Meaningful errors | Bind failures, import errors produce clear messages |
| NF-017 | Compatible with ecosystem | Serves chirp, Starlette, FastAPI, Litestar apps |

### 5.4 Compatibility

| ID | Requirement | Target |
|----|-------------|--------|
| NF-018 | Python version | >= 3.14 |
| NF-019 | Free-threading | Full support for 3.14t (no-GIL) |
| NF-020 | GIL fallback | Works correctly on GIL-enabled 3.14 |
| NF-021 | Platforms | Linux, macOS. Windows best-effort. |
| NF-022 | ASGI compliance | Full ASGI 3.0 spec (HTTP + lifespan) |

---

## 6. Dependency Budget

### Core (always installed)

| Dependency | Purpose | Size | Justification |
|------------|---------|------|---------------|
| h11 | HTTP/1.1 parser | ~15KB | Pure Python, well-tested, sans-I/O design |

### Stdlib (no extra deps — Python 3.14+)

| Module | Purpose | Notes |
|--------|---------|-------|
| `compression.zstd` | Zstd content-encoding (PEP 784) | Best ratio/speed, ~76% browser support |
| `zlib` | Gzip/deflate content-encoding | Universal fallback |
| `ssl` | TLS termination | Stdlib, no extras needed for basic TLS |

### Optional Extras

| Extra | Dependency | Purpose |
|-------|------------|---------|
| `pounce[h2]` | h2 | HTTP/2 protocol support |
| `pounce[ws]` | wsproto | WebSocket protocol support |
| `pounce[br]` | brotli or brotlicffi | Brotli content-encoding (`Accept-Encoding: br`) |
| `pounce[tls]` | truststore | System certificate store for TLS |
| `pounce[full]` | All of the above | Full protocol support |

### Excluded

| Dependency | Reason |
|------------|--------|
| uvloop | Replaces asyncio's event loop with C extension; pounce proves pure Python is enough |
| httptools | C binding to Node's http-parser; h11 is pure Python and debuggable |
| anyio | Server doesn't need backend-agnostic async; asyncio is sufficient |
| click | CLI uses argparse; no additional dependency needed |

---

## 7. Success Criteria

### 7.1 v0.1.0 (Phase 1: It Runs) — ✓ Implemented

- [x] `pounce myapp:app` serves HTTP/1.1 requests correctly
- [x] ASGI lifespan protocol works (startup, shutdown, failure, timeout, no-lifespan fallback)
- [x] Single-worker mode with full ASGI 3.0 scope construction
- [x] Graceful shutdown on SIGINT/SIGTERM
- [x] Zstd content-encoding via stdlib `compression.zstd` + gzip fallback
- [x] Content negotiation respects `Accept-Encoding` quality values (zstd > gzip > identity)
- [x] Streaming-first response pipeline (chunked writes, no buffering)
- [x] Server-Timing header injection (`parse`, `app` durations)
- [x] 188 tests passing (unit + integration)
- [ ] Chirp hello-world app runs without modification (needs manual verification)
- [ ] ASGI compliance suite (formal spec compliance test — to be run)

### 7.2 v0.2.0 (Phase 2: It Scales)

- Multi-worker mode (threads on nogil, processes on GIL)
- GIL detection and automatic mode selection
- Linear throughput scaling with worker count on 3.14t
- Memory advantage measurable vs. multi-process uvicorn
- Streaming SSE responses hold open without memory leak under sustained load

### 7.3 v0.3.0 (Phase 3: It's Complete)

- WebSocket support via wsproto
- HTTP/2 support via h2
- TLS termination
- Feature parity with uvicorn for common use cases

### 7.4 v0.4.0 (Phase 4: It's Fast)

- Benchmark suite with reproducible results
- Competitive with uvicorn on single-worker throughput
- Demonstrably faster on multi-worker nogil (threads vs processes)
- Optional httptools backend for users who want C-accelerated parsing

---

## 8. Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| h11 too slow for production throughput | Blocks performance targets | Medium | Benchmark early; httptools as optional accelerator |
| asyncio event loops not truly parallel on 3.14t | Blocks core value proposition | Low | Test early on 3.14t nightlies; asyncio is documented as nogil-ready |
| Subtle threading bugs without GIL safety net | Data corruption, crashes | Medium | Immutable by default; extensive stress testing under ThreadSanitizer |
| SO_REUSEPORT not available on macOS | Uneven load distribution | Low | Fallback to single-accept with round-robin dispatch |
| h2/wsproto libraries not 3.14t-safe | Blocks HTTP/2 and WebSocket | Low | Test optional deps on 3.14t; report upstream if needed |
| Scope creep toward "framework features" | Dilutes server focus | Medium | Strict boundary: pounce serves ASGI apps, period |
| `compression.zstd` not thread-safe under 3.14t | Data corruption in compressed responses | Low | Per-request compressor instances (stateless); test early on 3.14t |

---

## 9. Out of Scope

Pounce deliberately does not:

- **Include application logic.** No routing, no middleware, no templates. That's chirp.
- **Include a WSGI adapter.** ASGI only. Use Gunicorn for WSGI.
- **Include HTTP/3 in initial phases.** QUIC requires UDP socket handling that's
  architecturally different. WebTransport (HTTP/3 based) has ~82% browser coverage as of
  2026 and is growing — this is a Phase 5 exploration, not a hard exclusion.
- **Include static file serving.** The ASGI app handles static files (or use a CDN).
- **Include a process manager.** Pounce manages its own workers but doesn't replace systemd,
  supervisor, or container orchestration.
- **Vendor h11.** Dependencies are dependencies, not vendored copies.
- **Support Python < 3.14.** Free-threading is the reason pounce exists.

---

## 10. Open Questions

1. **Should pounce support uvloop as an optional event loop?** uvloop is faster than asyncio's
   default loop but is a C extension. Including it as an option contradicts the "pure Python"
   philosophy but acknowledges that some users prioritize raw speed. Leaning toward supporting
   it as an optional extra without making it the default.

2. **Should the CLI support app factory patterns?** `pounce "myapp:create_app()"` vs only
   `pounce "myapp:app"`. Factory patterns are common in larger applications. Uvicorn supports
   this via `--factory`. Worth supporting but not in phase 1.

3. **How should pounce handle the transition from dev server to production?** Chirp's
   `app.run()` currently starts a dev server. Should it detect pounce and use it automatically?
   Or should `app.run()` always be for development, with `pounce myapp:app` for production?

4. **What metrics should be built-in?** Granian includes Prometheus metrics. Pounce could
   expose connection count, request count, and latency histogram via an optional endpoint or
   callback. Or leave it entirely to middleware in the ASGI app.

5. **Should pounce support hot reload for production?** Graceful restart (new workers start,
   old workers drain) is useful for zero-downtime deploys. This is different from dev-mode
   file-watching reload. Worth considering for phase 2.

6. **Should pounce explore `concurrent.interpreters` as a third worker model?**
   Subinterpreters (PEP 734, new in 3.14) offer isolation without fork overhead — no memory
   duplication like processes, stronger boundaries than threads. CPU-bound benchmarks show
   subinterpreters outperform free-threading. Limitations: restricted shareable types
   (`str | bytes | int | float | bool | None | tuple | Queue | memoryview`), not all
   PyPI packages support it yet. Worth exploring as Phase 5 once the ecosystem matures.

7. **Should brotli be a core optional extra or left to middleware?** `Accept-Encoding: br`
   is universally supported in browsers, and brotli offers better compression ratios than
   gzip. But brotli requires a C extension (`brotli` or `brotlicffi`), which contradicts
   the pure-Python philosophy. Leaning toward offering it as `pounce[br]` — explicit opt-in,
   not forced on anyone.
