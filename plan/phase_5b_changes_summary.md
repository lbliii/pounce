# Phase 5b: File Changes Summary

Quick reference for all modules that need changes during Phase 5b implementation.

---

## New Modules (To Create)

### Core Features
- `src/pounce/_static.py` — Static file serving with sendfile, ETag, Range, precompressed
- `src/pounce/_middleware.py` — Middleware protocol and execution chain
- `src/pounce/_debug.py` — Development error pages with rich tracebacks
- `src/pounce/_otel.py` — OpenTelemetry span creation and trace propagation

### Tests
- `tests/unit/test_static.py` — Static file serving unit tests
- `tests/unit/test_middleware.py` — Middleware hooks and execution
- `tests/unit/test_debug.py` — Error page rendering and security
- `tests/unit/test_otel.py` — OpenTelemetry span creation
- `tests/integration/test_static_integration.py` — End-to-end static file tests
- `tests/integration/test_middleware_integration.py` — Middleware with real apps
- `tests/integration/test_graceful_reload.py` — Zero-downtime reload tests
- `tests/integration/test_bengal_site.py` — Full Bengal SSG site serving

---

## Modified Modules (Existing Files)

### Configuration
**`src/pounce/config.py`**
- Add `static_files: dict[str, str] = {}`
- Add `static_cache_control: str = "public, max-age=3600"`
- Add `static_precompressed: bool = True`
- Add `static_follow_symlinks: bool = False`
- Add `static_index_file: str | None = "index.html"`
- Add `middleware: list[MiddlewareProtocol] = []`
- Add `websocket_compression: bool = True`
- Add `websocket_max_message_size: int = 10_485_760`
- Add `reload_timeout: float = 30.0`
- Add `log_slow_requests_threshold: float = 5.0`
- Add `otel_endpoint: str | None = None`

### ASGI Lifespan
**`src/pounce/asgi/lifespan.py`**
- Change `run_lifespan()` to return `dict[str, Any]` (state dict) instead of `None`
- Create empty state dict before startup
- Pass state dict as context manager result

**`src/pounce/asgi/bridge.py`**
- Add `state: dict[str, Any]` parameter to scope construction
- Inject `scope["state"] = state` in `_build_scope()`

**`src/pounce/asgi/h2_bridge.py`**
- Add `state: dict[str, Any]` parameter to scope construction
- Inject `scope["state"] = state` in H2 stream scope

**`src/pounce/asgi/ws_bridge.py`**
- Add `state: dict[str, Any]` parameter to scope construction
- Inject `scope["state"] = state` in WebSocket scope

### Worker
**`src/pounce/worker.py`**
- Store `lifespan_state: dict[str, Any]` after startup
- Initialize static file handler if `config.static_files`
- Check static handler before ASGI app dispatch
- Wrap app calls with middleware chain if configured
- Pass state dict to all ASGI scope construction
- Add drain mode flag for graceful reload
- Track in-flight request count for draining

### Supervisor
**`src/pounce/supervisor.py`**
- Add SIGHUP signal handler for graceful reload
- Implement `_rolling_restart()` method (spawn new, drain old, wait, shutdown)
- Add SIGTERM drain mode (stop accepting, wait for in-flight, force after timeout)
- Track worker in-flight counts for drain monitoring
- Log drain progress

### WebSocket Protocol
**`src/pounce/protocols/ws.py`**
- Add permessage-deflate extension negotiation
- Parse `Sec-WebSocket-Extensions` header
- Compress outgoing frames, decompress incoming
- Use wsproto extensions API
- Respect `ServerConfig.websocket_compression`

### Lifecycle & Logging
**`src/pounce/lifecycle.py`**
- Add `WorkerStarted`, `WorkerStopped` event types (optional)
- Add `SlowRequest` event type for requests > threshold
- Create `LoggingCollector` that wraps another collector

**`src/pounce/logging.py`**
- Add structured event logging for lifecycle events (JSON format)
- Log slow requests when duration > threshold
- Correlate via request_id

### CLI
**`src/pounce/_cli.py`**
- Add `--static PATH:DIR` (repeatable)
- Add `--static-cache-control`
- Add `--websocket-compression / --no-websocket-compression`
- Add `--reload-timeout`
- Add `--otel-endpoint`

### Package Exports
**`src/pounce/__init__.py`**
- Add `StaticFiles` to exports (for manual middleware use)
- Add `MiddlewareProtocol` to exports
- Update `ServerConfigKwargs` with new fields

---

## Dependency Changes

### pyproject.toml

**New optional dependencies:**
```toml
[project.optional-dependencies]
# ... existing ...

# OpenTelemetry (optional)
otel = [
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
]
```

**Update full extra:**
```toml
full = [
    "h2>=4.0",
    "wsproto>=1.2",
    "truststore>=0.9",
    "opentelemetry-api>=1.20",  # Added
    "opentelemetry-sdk>=1.20",  # Added
]
```

---

## File Structure After Phase 5b

```
src/pounce/
├── __init__.py                  # Updated: new exports
├── py.typed
│
│   # Configuration & primitives
├── config.py                    # Modified: new fields
├── _types.py
├── _errors.py
├── _timing.py
├── _importer.py
├── _compression.py
├── _runtime.py
├── _priority.py
├── _reload.py
├── _request_id.py
├── _proxy.py
│
│   # New feature modules
├── _static.py                   # NEW: Static file serving
├── _middleware.py               # NEW: Middleware system
├── _debug.py                    # NEW: Dev error pages
├── _otel.py                     # NEW: OpenTelemetry
│
│   # Core modules
├── server.py
├── supervisor.py                # Modified: reload, drain
├── worker.py                    # Modified: static, middleware, state, drain
│
│   # Protocols
├── protocols/
│   ├── _base.py
│   ├── h1.py
│   ├── h1_httptools.py
│   ├── h2.py
│   └── ws.py                    # Modified: compression
│
│   # ASGI bridge
├── asgi/
│   ├── bridge.py                # Modified: state injection
│   ├── h2_bridge.py             # Modified: state injection
│   ├── ws_bridge.py             # Modified: state injection
│   └── lifespan.py              # Modified: return state dict
│
│   # Network & observability
├── net/
│   ├── listener.py
│   └── tls.py
├── lifecycle.py                 # Modified: new events, LoggingCollector
├── logging.py                   # Modified: lifecycle event logging
├── metrics.py
├── _health.py
│
│   # CLI
└── _cli.py                      # Modified: new flags
```

---

## Test Coverage After Phase 5b

**Current:** ~426 tests
**Target:** 500+ tests

### New Test Files (8)
- `test_static.py` — 15+ tests
- `test_middleware.py` — 12+ tests
- `test_debug.py` — 10+ tests
- `test_otel.py` — 12+ tests
- `test_static_integration.py` — 6+ tests
- `test_middleware_integration.py` — 8+ tests
- `test_graceful_reload.py` — 15+ tests
- `test_bengal_site.py` — 5+ tests

**Total New Tests:** ~83 tests

### Modified Test Files (6)
- `test_config.py` — Add 8 tests for new config fields
- `test_worker_lifecycle.py` — Add 5 tests for drain mode
- `test_supervisor.py` — Add 10 tests for reload/drain
- `test_lifespan.py` — Add 3 tests for state dict
- `test_bridge.py` — Add 3 tests for state injection
- `test_ws_protocol.py` — Add 5 tests for compression

**Total Modified Tests:** ~34 tests

**Final Count:** 426 + 83 + 34 = **543 tests** ✓

---

## Documentation Structure After Phase 5b

```
docs/
├── get-started/
│   └── quickstart.md            # Updated: static files example
│
├── features/                    # NEW SECTION
│   ├── static-files.md          # NEW: Configuration, caching, precompressed
│   ├── middleware.md            # NEW: Writing middleware, built-ins
│   └── websocket.md             # Updated: Add compression section
│
├── deployment/
│   ├── workers.md
│   ├── compression.md
│   ├── reload.md                # NEW: Dev vs graceful reload
│   ├── observability.md         # NEW: OTel, structured logs
│   └── production.md            # Updated: Kubernetes, drain, reload
│
├── development/
│   └── error-pages.md           # NEW: Rich tracebacks, debugging
│
├── configuration/
│   ├── server.md                # Updated: New config fields
│   └── cli.md                   # Updated: New CLI flags
│
└── about/
    └── architecture.md          # Updated: Middleware flow, static files
```

---

## Roadmap Summary

| Task | Priority | Complexity | Est. Days | Status |
|------|----------|------------|-----------|--------|
| **Wave 1** | | | **7-10 days** | |
| Static Files | P0 | Medium | 3-5 | 🔲 Pending |
| Lifespan State | P0 | Low | 1-2 | 🔲 Pending |
| Dev Error Pages | P0 | Medium | 2-3 | 🔲 Pending |
| **Wave 2** | | | **5-7 days** | |
| Middleware System | P0 | Medium | 3-4 | 🔲 Pending |
| WebSocket Compression | P1 | Medium | 2-3 | 🔲 Pending |
| **Wave 3** | | | **7-10 days** | |
| Graceful Reload | P0 | High | 4-6 | 🔲 Pending |
| Connection Draining | P1 | Medium | 3-4 | 🔲 Pending |
| **Wave 4** | | | **5-7 days** | |
| Structured Logging | P1 | Low | 2-3 | 🔲 Pending |
| OpenTelemetry | P1 | High | 3-4 | 🔲 Pending |
| **Wave 5** | | | **3-5 days** | |
| HTTP/3 Spike | P1 | High | 3-5 | 🔲 Pending |
| **Total** | | | **27-39 days** | |

**Parallel Work Possible:**
- Wave 1: All 3 tasks can be parallelized (no dependencies)
- Wave 2: Both tasks can be parallelized
- Wave 4: Both tasks can be parallelized

**With 2 developers:** ~3-4 weeks (15-20 working days)
**With 1 developer:** ~5-8 weeks (27-39 working days)

---

## Release Checkpoints

### v0.2.0-alpha.1 (After Wave 1)
- Static files working
- Lifespan state compliant
- Rich dev error pages
- **Goal:** Bengal dogfooding

### v0.2.0-beta.1 (After Wave 3)
- Add middleware, reload, WS compression
- **Goal:** Production testing

### v0.2.0 (After Wave 4)
- Add OTel, structured logging
- **Goal:** Public release

### v0.3.0 or later (After Wave 5)
- HTTP/3 decision implemented or deferred
- **Goal:** Next-gen protocol support

---

## Quick Start Guide

**To begin Phase 5b:**

1. **Review this summary** and main plan
2. **Create a branch:** `git checkout -b phase-5b`
3. **Start with Task #1** (Static Files) — see detailed plan in `phase_5b_1_static_files.md`
4. **Run tests frequently:** `pytest -x -q`
5. **Update this checklist** as tasks complete
6. **Commit often** with descriptive messages referencing task numbers

**Completion markers:**
- [ ] Wave 1 Complete → Tag `v0.2.0-alpha.1`
- [ ] Wave 2 Complete → Update docs
- [ ] Wave 3 Complete → Tag `v0.2.0-beta.1`
- [ ] Wave 4 Complete → Tag `v0.2.0`
- [ ] Wave 5 Complete → Decision on `v0.3.0`

---

**Next:** Begin implementing `src/pounce/_static.py` (see detailed plan)
