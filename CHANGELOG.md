# Changelog

All notable changes to pounce will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

#### Connection Lifecycle Events

- Structured, immutable event types for every stage of a connection's lifecycle:
  `ConnectionOpened`, `RequestStarted`, `ResponseCompleted`, `RequestFailed`,
  `ConnectionClosed` — all frozen dataclasses with nanosecond monotonic timestamps
- `LifecycleCollector` protocol — any object with a `record(event)` method can receive
  lifecycle events. `NoopCollector` (default) discards events with zero overhead.
  `BufferedCollector` stores events in a thread-safe deque for inspection
- `Server` and `Supervisor` now accept an optional `lifecycle_collector` parameter and
  forward it to every `Worker` they spawn. This enables external systems (e.g. Purr's
  `StackCollector`) to receive connection-level telemetry from all workers through a
  single collector instance
- Events are designed for aggregation and observability, not logging — use them to build
  latency distributions, connection counts, error rate dashboards, or full-stack event
  traces

#### Per-Worker Lifecycle Scopes

- Worker sends `pounce.worker.startup` scope to the ASGI app before accepting connections,
  and `pounce.worker.shutdown` after closing — both run on the worker's own event loop so
  async resources (httpx clients, DB pools) bind to the correct loop
- Timeout protection: 30s startup, 10s shutdown — apps that don't recognise the scope type
  time out gracefully instead of hanging
- `_worker_lifecycle_receive` returns `http.disconnect` immediately so apps that route
  unknown scopes to their HTTP handler unblock quickly
- If startup hook fails, the worker does not accept connections (prevents serving with
  uninitialised state); shutdown hook failure is non-fatal
- `tests/unit/test_worker_lifecycle.py` — 6 tests covering startup/shutdown delivery,
  ordering, startup failure, shutdown failure, and unknown-scope handling

#### ASGI 3.0 Compliance Suite

- `tests/integration/test_asgi_compliance.py` — 41 tests validating pounce against the
  ASGI 3.0 HTTP Connection Scope and Lifespan specs: scope completeness, all HTTP methods,
  header lowercasing, path decoding, query strings, request body protocol, response
  streaming, keep-alive, Connection: close, error handling, lifespan lifecycle

---

**Phase 4: It's Fast** — performance optimization, correctness fixes, benchmark infrastructure.

#### POST Request Body Reading (Correctness Fix)

- Worker now reads POST/PUT/PATCH request bodies correctly. Restructured `_handle_request`
  to collect body events from the initial h11 parse batch and, for bodies spanning multiple
  socket reads, runs a concurrent body reader task alongside the ASGI app
- Removed xfail markers from `test_post_body_echo` and `test_large_body`
- Added tests for PUT body, streaming multi-chunk body

#### App Factory Support

- `pounce "myapp:create_app()"` works end-to-end — the importer already supported factory
  detection; CLI, integration tests, and example app now verify the full pipeline
- Added `examples/factory_app.py` demonstrating the factory pattern

#### Optional httptools Backend (`pounce[fast]`)

- `protocols/h1_httptools.py` — C-accelerated HTTP/1.1 parser implementing the same
  `ProtocolHandler` interface as `H1Protocol` (h11). Uses httptools callbacks for parsing
  and hand-crafted response serialization for speed
- Worker auto-detects httptools at import time; `pip install pounce[fast]` is the opt-in
- Full unit test suite for the httptools backend (skips when not installed)
- `pyproject.toml` adds `fast` optional extra: `httptools>=0.6`

#### Benchmark Suite

- `benchmarks/run_benchmark.py` — reproducible benchmark runner that starts pounce, drives
  load with wrk or hey, captures results as structured JSON, prints markdown summary table
- Comparison mode: `--compare` runs the same workload against uvicorn
- Workloads: hello-world (overhead), JSON (serialize), POST echo (body reading)
- Dedicated benchmark apps in `benchmarks/apps/`

#### Profiling Infrastructure

- `benchmarks/profile_hotpath.sh` — wraps py-spy for flame graph generation under load
- `benchmarks/profile_memory.py` — RSS tracking with optional tracemalloc integration

#### Hot-Path Optimizations

- Pre-computed ASGI spec dict constant (avoid per-request dict allocation)
- Bodyless fast-path receive: skip asyncio.Queue for GET/HEAD requests
- Write coalescing: head + first body chunk combined into single write for responses < 16KB
- Single-pass header lookup for compression negotiation
- Skip empty body writes (avoid zero-length syscalls)

#### CI

- `.github/workflows/ci.yml` — GitHub Actions pipeline: lint (ruff check + format), type
  check (ty), and tests on a 2x2 matrix (ubuntu/macos x Python 3.14/3.14t). Includes GIL
  status verification on free-threaded builds. 15-minute timeout per the py-free-threading
  CI guide

### Changed

- Removed `from __future__ import annotations` from all 43 source, test, example, and
  benchmark files — not needed on Python 3.14 (PEP 563 import is a no-op)
- Registered `timeout` pytest marker in `pyproject.toml` (silences 6 warnings)

---

**Phase 3: It's Complete** — full protocol support, TLS, WebSocket, HTTP/2, modern HTTP features.

#### TLS Termination

- `net/tls.py` — `create_tls_context()` for stdlib `ssl.SSLContext` with secure defaults
  (TLSv1.2+, no compression), ALPN protocol advertisement (`h2`, `http/1.1`), optional
  `truststore` integration for system certificate stores
- `is_tls_configured()` helper for conditional context creation
- CLI flags: `--ssl-certfile`, `--ssl-keyfile`
- `TLSError` added to error hierarchy
- Startup banner shows `tls: enabled` when active

#### WebSocket Protocol

- `protocols/ws.py` — `WSProtocol` sans-I/O wrapper around wsproto for server-side
  WebSocket framing. Manual `101 Switching Protocols` HTTP response construction
  (wsproto 1.x expects HTTP upgrade handled externally)
- `build_ws_accept_key()` for RFC 6455 `Sec-WebSocket-Accept` computation
- `build_101_response()` for raw HTTP upgrade response bytes
- `asgi/ws_bridge.py` — `build_ws_scope()`, `create_ws_receive()`, `create_ws_send()`
  for full ASGI WebSocket lifecycle (`websocket.connect`, `websocket.accept`,
  `websocket.send`, `websocket.close`)
- New event types: `WebSocketConnected`, `WebSocketDataReceived`, `WebSocketDisconnected`

#### HTTP/2 Protocol

- `protocols/h2.py` — `H2Connection` sans-I/O wrapper around the h2 library. Stream
  multiplexing, per-stream event types (`H2RequestReceived`, `H2BodyReceived`,
  `H2StreamReset`, `H2GoAway`, `H2WindowUpdated`, `H2WebSocketRequest`), flow control,
  GOAWAY handling
- `asgi/h2_bridge.py` — `build_h2_scope()`, `create_h2_receive()`, `create_h2_send()`
  for per-stream ASGI dispatch with concurrent stream tasks
- ALPN negotiation in worker: `selected_alpn_protocol() == "h2"` → H2 connection handler
- `SETTINGS_ENABLE_CONNECT_PROTOCOL` for RFC 8441 WebSocket over HTTP/2

#### Protocol Negotiation

- Worker dynamically branches connections based on ALPN result (H2) or HTTP/1.1 upgrade
  headers (WebSocket), falling through to standard HTTP/1.1 keep-alive loop
- `_is_websocket_upgrade()` helper: detects `Connection: Upgrade` + `Upgrade: websocket`

#### WebSocket over HTTP/2 (RFC 8441)

- Extended CONNECT detection in `H2Connection.receive_data()`: `:method = CONNECT` +
  `:protocol = websocket` emits `H2WebSocketRequest` event
- `_handle_h2_websocket_stream()` in worker manages WS framing within H2 streams

#### Priority Signals (RFC 9218)

- `_priority.py` — `parse_priority()` for `Priority` header parsing (urgency 0-7,
  incremental boolean), `StreamPriority` dataclass, `PriorityScheduler` min-heap for
  urgency-based DATA frame scheduling

#### 103 Early Hints

- H2 ASGI bridge: `status == 103` in `http.response.start` sends informational headers
  without marking response as started (allows multiple early hints before final response)
- H1 ASGI bridge: silently skips `status == 103` (browser support inconsistent over H1)

#### Dev Reload

- `_reload.py` — file watcher with polling: `_snapshot()`, `detect_changes()`,
  `watch_for_changes()` with configurable interval and stop event
- Excludes `__pycache__`, `.git`, `.venv`, `node_modules`, etc.
- Watches `.py`, `.yaml`, `.toml`, `.json`, `.cfg`, `.ini` extensions
- Single-worker mode: restart loop (shutdown → recreate socket → restart asyncio)
- Multi-worker mode: `Supervisor.restart_workers()` drains all workers, clears shutdown
  event, respawns fresh workers
- CLI flag: `--reload`
- `ReloadError` added to error hierarchy
- Startup banner shows `reload: enabled` when active

#### Keep-Alive Tuning

- `max_requests_per_connection` config field (0 = unlimited): enforced in the HTTP/1.1
  keep-alive loop — closes connection after N requests
- CLI flags: `--keep-alive-timeout`, `--max-requests-per-connection`
- Config validation: `keep_alive_timeout > 0`, `max_requests_per_connection >= 0`
- Startup banner shows non-default keep-alive and max-requests values

#### Package Wiring

- `protocols/__init__.py` — re-exports `WSProtocol`, `H2Connection`, all H2 event types
- `asgi/__init__.py` — re-exports WS and H2 bridge functions
- `net/__init__.py` — re-exports `create_tls_context`, `is_tls_configured`

#### Tests (408 passing — unit + integration + compliance)

- TLS: context creation, secure defaults, ALPN, missing cert handling, truststore
- WebSocket: `WSProtocol` framing, `build_ws_accept_key`, `build_101_response`,
  `build_ws_scope`, `_is_websocket_upgrade` header detection
- HTTP/2: `H2Connection` init, request/response lifecycle, multiplexed streams,
  stream reset, GOAWAY
- Priority Signals: `parse_priority`, `PriorityScheduler` urgency ordering
- Dev Reload: `_snapshot`, `detect_changes`, file creation/modification/deletion,
  exclude patterns
- Compression: updated for Brotli exclusion (GIL-incompatible on 3.14t)
- Config: validation for `keep_alive_timeout` and `max_requests_per_connection`
- Supervisor: `restart_workers()` event clearing and worker joining
- CLI: Phase 3 flag parsing (TLS, reload, keep-alive, max-requests)
- Package exports: Phase 3 protocol, ASGI, net, and error exports
- Error hierarchy: `TLSError` and `ReloadError`

---

**Phase 2: It Scales** — multi-worker mode with automatic GIL detection.

#### Runtime Detection

- `_runtime.py` — `is_gil_enabled()` wrapping `sys._is_gil_enabled()` with safe fallback
  for Python < 3.13; `detect_worker_mode()` returning `"thread"` (nogil) or `"process"`
  (GIL); `default_worker_count()` from `os.cpu_count()`

#### Supervisor

- `supervisor.py` — `Supervisor` class that spawns N workers as `threading.Thread` (on
  nogil / 3.14t) or `multiprocessing.Process` (on GIL builds). Health monitoring via
  watchdog loop (1s interval), crash detection and automatic restart with budget (max 5
  restarts per 60s window), graceful shutdown coordination via `threading.Event`, per-worker
  connection limit calculation, SIGINT/SIGTERM signal forwarding

#### Worker Enhancements

- External `threading.Event` shutdown bridge — supervisor sets a threading event, the
  worker's `_bridge_shutdown` task polls it every 250ms and bridges to asyncio via
  `loop.call_soon_threadsafe`
- Per-worker connection backpressure — rejects connections when at capacity
- Worker ID for log differentiation (`pounce.worker.0`, `pounce.worker.1`, etc.)
- Thread-safe `shutdown()` method using `call_soon_threadsafe`

#### Network

- `create_listeners(config, count)` — multi-socket creation strategy: per-worker
  independent sockets with `SO_REUSEPORT` on Linux (kernel-level distribution), shared
  socket fallback on macOS (single fd, all workers accept)

#### Server Orchestration

- Single-worker fast path (`workers=1`) — skips supervisor entirely, no overhead
- Multi-worker path delegates to `Supervisor` for lifecycle management
- ASGI lifespan runs once in main thread before workers spawn
- Startup banner now shows GIL status (`nogil` / `GIL`) and worker mode
- Socket deduplication on cleanup for shared-fd safety

#### Configuration

- `workers=0` auto-detect semantics via `resolve_workers()` (defaults to `os.cpu_count()`)
- `__post_init__` validation for workers (>= 0) and port (0-65535)
- CLI `--workers 0` for auto-detect with updated help text

#### Error Hierarchy

- `SupervisorError` — worker spawn failures, crash-restart exhaustion
- `WorkerError` — worker-level failures reported to supervisor

#### Benchmarks

- `benchmarks/hello_app.py` — minimal ASGI app for throughput benchmarking
- `benchmarks/sse_app.py` — SSE streaming app for stress testing
- `benchmarks/test_throughput.py` — automated throughput scaling benchmark (single-worker
  baseline ~6-7k req/s, multi-worker validated via shared-socket workers)
- `benchmarks/test_memory.py` — thread vs process RSS comparison (thread workers use
  shared interpreter, ~3MB delta for 4 workers)
- `benchmarks/test_sse_stress.py` — SSE stress test: 100 concurrent streams held 10s,
  ~20k events delivered, RSS growth < 3MB (no memory leak)
- `benchmarks/test_chirp_compat.py` — chirp App compatibility verification (chirp hello-world
  served through pounce Worker without modification)
- `benchmarks/README.md` — instructions for wrk/hey benchmarking

#### Tests (253 + 7 benchmark tests, all passing)

- Unit tests for runtime detection: GIL state, worker mode, CPU count fallback
- Unit tests for supervisor: init, mode detection, socket validation, shutdown, spawn/stop,
  respawn budget, restart window pruning, per-worker connection limits
- Unit tests for listener multi-socket: create_listeners, strategy detection, SO_REUSEPORT
  vs shared, count validation
- Unit tests for worker: external shutdown bridge, internal shutdown, worker ID, backpressure
- Integration tests for multi-worker: concurrent requests across workers, graceful shutdown,
  worker liveness, supervisor mode reporting
- Integration tests for server: _close_sockets deduplication, shared-fd handling
- Updated conftest and test_server to use explicit `worker_id=0`
- Updated package export tests for Phase 2 modules

---

**Phase 1: It Runs** — the minimal viable ASGI server.

#### Primitives

- `_errors.py` — `PounceError` hierarchy with HTTP status code mapping: `ParseError`
  (400), `TimeoutError` (408), `LimitError` (413/431), `AppError` (500), `LifespanError`
  (500)
- `_timing.py` — `monotonic_ns()`, `elapsed_ms()` clock utilities; `ServerTiming` builder
  for the `Server-Timing` HTTP header
- `_importer.py` — resolve `"module:attribute"` and `"module:factory()"` strings to ASGI
  callables with clear error messages
- `_compression.py` — `Accept-Encoding` negotiation (zstd > gzip > identity, respects
  q-values), per-request `ZstdCompressor` (stdlib `compression.zstd`) and `GzipCompressor`
  (stdlib `zlib`) instances
- `_types.py` — ASGI 3.0 type aliases: `Scope`, `Receive`, `Send`, `ASGIApp`
- `config.py` — `ServerConfig` frozen dataclass with bind address, timeouts, limits,
  compression, `root_path`, `server_timing`, access log, and h11 tuning fields

#### Protocol Layer

- `protocols/_base.py` — `ProtocolHandler` runtime-checkable Protocol; typed event
  dataclasses: `RequestReceived`, `BodyReceived`, `ConnectionClosed`, `Upgraded`;
  `ProtocolEvent` union type
- `protocols/h1.py` — sans-I/O HTTP/1.1 handler wrapping h11: request parsing, response
  serialization, keep-alive cycling, malformed-input detection

#### ASGI Bridge

- `asgi/bridge.py` — `build_scope()` (HTTP scope from protocol events + config),
  `create_receive()` (async body stream from queue), `create_send()` (streaming-first
  writes with optional compression and Server-Timing injection)
- `asgi/lifespan.py` — `run_lifespan()` async context manager: startup/shutdown events,
  failure handling, timeout, graceful no-lifespan fallback

#### Network and Worker

- `net/listener.py` — socket creation with `SO_REUSEADDR`/`SO_REUSEPORT`, non-blocking
  bind, clear error messages for EADDRINUSE/EACCES
- `logging.py` — stdlib logging configuration; structured access log format:
  `{client} - "{method} {path} HTTP/1.1" {status} {bytes} {duration}ms`
- `worker.py` — asyncio event loop accepting connections through the full pipeline:
  parse → scope → negotiate compression → ASGI app → response → access log. Keep-alive
  cycling, error responses (400/500), configurable timeouts

#### Server and CLI

- `server.py` — full lifecycle orchestration: CONFIG → BIND → LIFESPAN → SERVE → SHUTDOWN.
  Signal handling (SIGINT/SIGTERM), startup banner with version/URL/workers/features
- `_cli.py` — `pounce myapp:app` CLI via argparse: `--host`, `--port`, `--workers`,
  `--log-level`, `--root-path`, `--no-compression`, `--server-timing`, `--no-access-log`
- `__init__.py` — public API: `pounce.run()`, `ServerConfig`, ASGI type re-exports

#### Package Wiring

- `protocols/__init__.py` — re-exports `H1Protocol`, `ProtocolHandler`, all event types
- `asgi/__init__.py` — re-exports `build_scope`, `create_receive`, `create_send`,
  `run_lifespan`
- `net/__init__.py` — re-exports `create_listener`
- Top-level `__init__.py` — re-exports `ASGIApp`, `Scope`, `Receive`, `Send`

#### Tests (188 passing)

- Unit tests for all primitives: errors, timing, importer, protocol events, config
- Unit tests for H1 protocol: parsing, serialization, keep-alive, malformed input
- Unit tests for compression: negotiation, roundtrip, browser Accept-Encoding strings
- Unit tests for ASGI bridge: scope construction, streaming send, compression/timing injection
- Unit tests for lifespan: happy path, failure, no-lifespan apps, shutdown timeout
- Unit tests for listener: socket properties, non-blocking, reuseaddr
- Unit tests for logging: format correctness
- Unit tests for package exports: all `__init__.py` re-exports verified
- Integration tests for worker: hello world, echo, streaming, error handling, malformed input
- Integration tests for server: start/respond lifecycle, lifespan events
- Integration tests for CLI: parser defaults/overrides, invalid app handling, public API imports
- Shared `conftest.py` with lifespan-aware test apps and `start_worker`/`send_raw_request` helpers

#### Infrastructure

- Project scaffolding: `pyproject.toml` with ruff, ty, pytest, poe task runner
- `py.typed` PEP 561 marker
- `_Py_mod_gil = 0` free-threading declaration
