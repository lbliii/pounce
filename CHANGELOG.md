# Changelog

All notable changes to pounce will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
