# Changelog

All notable changes to pounce will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

---

## [0.7.0] — 2026-05-09

Release-readiness hardening for protocol correctness, operator UX, config discovery, and
production-shaped benchmark coverage.

### Added

- Added `pounce config schema`, `pounce config show`, and `pounce init` for discoverable configuration, redacted resolved-config inspection, and project scaffolding.
- Added the opt-in `/_pounce/info` introspection endpoint with allowlist redaction and public-bind warnings.
- Added Bengal-shaped static-site and Chirp/LB Sonic-shaped forum benchmark workloads for representative static, tenant, form POST, SSE, middleware-style header, and lifespan-state coverage.
- Added real-server middleware coverage for pre-request short-circuiting, post-response headers, exception handling, and non-HTTP scope bypass.
- Added Railway deployment guidance for platform TLS, `$PORT`, health checks, proxy trust, and drain-window alignment.

### Changed

- Re-enabled the ruff S110 lint gate and added CI coverage for unannotated broad exception suppression.
- Adopted modern Python 3.14+ patterns across leaf modules, including frozen handoff dataclasses, PEP 695 aliases, match/case conversions, and stable-shape TypedDicts.
- Updated the introspection auth ADR to match the shipped `/_pounce/info` implementation and warning policy.
- Fixed sync-worker graceful reload proof and clarified lifecycle docs around reload signaling.

### Fixed

- Reject oversized HTTP/2 and HTTP/3 request bodies with 413 behavior instead of delivering empty or truncated bodies to ASGI apps.
- Validate required HTTP/2 and HTTP/3 pseudo-headers, duplicate pseudo-headers, and Host/`:authority` conflicts before building tenant-facing scopes.
- Keep single-worker startup hook exceptions nonfatal, matching Worker-based paths for strict ASGI apps that reject unknown Pounce scopes.
- Avoid acquiring a process `fork` context for thread workers and remove invalid `worker_mode='thread'` remediation from troubleshooting.
- Skip response compression when HTTP/2, HTTP/3, sync ASGI, or sync-app responses already include `Content-Encoding`.
- Rewrite trusted proxy authority consistently across HTTP/1.1, HTTP/2, HTTP/3, and WebSocket ASGI scopes.
- Negotiate WebSocket `permessage-deflate` only when the client offers it.
- Harden free-threaded leaf-module behavior: per-worker request queues, RFC 9218 H2 priority scheduling, CRLF response-framer guard, rate-limiter snapshot cleanup, IPv6/UNIX socket support, subinterpreter timeout wiring, and lower-impact correctness/performance cleanups.

---

## [0.6.0] — 2026-04-13

Subinterpreter workers, RFC 9842 compression dictionaries, sendfile, framework compat tests, and 60+ fixes.

### Added

- Add 48 integration tests proving compatibility with FastAPI, Starlette, Django, and Litestar. All tests run through real Pounce workers — no mocks. Includes shared test infrastructure with proper ASGI lifespan handling.
- Added subinterpreter worker mode (`--worker-mode subinterpreter`) using Python 3.14's `concurrent.interpreters` (PEP 734). Each worker runs in a dedicated subinterpreter — thread-like performance with process-like isolation, all in one process.
- Adopt [Towncrier](https://towncrier.readthedocs.io/) for changelog management. Fragments in `changelog.d/` are compiled into `CHANGELOG.md` at release time. CI enforces a fragment for every PR that touches `src/pounce/`.
- Adopt bengal-zoomies 0.3.1: real QUIC client-mode integration tests, QPACK dynamic table compression (`http3_qpack_max_table_capacity`), and server-side 0-RTT policy control (`http3_zero_rtt_enabled`) with `ZeroRttAccepted`/`ZeroRttRejected` event handling.
- RFC 9842 Compression Dictionary Transport — shared zstd dictionaries for `dcz` content-encoding, `Available-Dictionary` / `Use-As-Dictionary` header negotiation, and built-in dictionary serving at `/.well-known/compression-dictionary/`.
- Zero-copy ``os.sendfile()`` for static file serving on non-TLS connections, RFC 7233 multipart range requests, and TOML config file support (``pounce.toml`` / ``[tool.pounce]`` in ``pyproject.toml``).

### Changed

- Bump milo-cli to 0.2.2 and kida-templates to 0.6.0. Picks up `get_env()` singleton cache fix (122 µs → 125 ns), kida for-loop variable binding correctness fix, faster template compilation, and cleaner command dispatch internals.
- Split `_apply_integrations()` god method into 7 focused private methods and polish hot paths: single-pass H3 header filtering, early-exit WebSocket upgrade detection, module-level debug constants.

### Fixed

- Fixed 28 Python 2 `except A, B:` handlers across 12 files that silently failed to catch the second exception type. Lifespan startup failures are now logged instead of silently swallowed. Worker crashes include full tracebacks. Startup/shutdown hook errors promoted from DEBUG to WARNING. ASGI protocol errors now include request method and path. `max_header_size` config now flows to the fast H1 parser (was hardcoded at 16KB). Config typos suggest similar valid keys. Port-in-use errors suggest diagnostic commands. Added `health_check_path` validation and CORS wildcard startup warning.
- Fixed 33 Python 2 exception syntax errors across 15 files that would crash on import in Python 3.14t. Fixed H3 bridge losing ``:authority`` header, crashing on SSE+compression, and encoding ``raw_path`` incorrectly. Static file serving now honors the ``cache_control`` config field and includes ``Vary: Accept-Encoding`` for precompressed responses. Aligned ``serve`` and ``check`` CLI defaults. Added ``startup_timeout`` validation, early mutual-exclusion checks, and branding params to config files. Improved error logging for parse errors, connection close reasons, and H3 TLS failures.
- Fixed CLI config precedence so explicit args always override TOML values even when matching the default. Added exponential backoff to worker restart to prevent tight crash-restart loops. CORS and security header middleware now skip headers already set by the app. Static file serving now allows ``.well-known/`` paths per RFC 8615. Fixed incorrect middleware docstring example.
- Fixed worker threads/processes hanging indefinitely on shutdown when keep-alive, WebSocket, or SSE connections were still open. The worker now applies `shutdown_timeout` to `server.wait_closed()` and calls `abort_clients()` to force-close lingering transports.
- Hardened subinterpreter workers: fixed socket FD leak on bootstrap failure, upgraded silent lifespan state drops to warnings, improved factory app error messages with chained exceptions. Added 16 new tests covering memory isolation, IIC protocol edge cases, race conditions (shutdown during reload, rapid reloads, crash during drain), and config round-trip validation.
- Safe HSTS default (opt-in instead of always-on), middleware signature validation with clear errors, post-header exception logging, ASGI bridge rejection of invalid message types, distinct ETags for compressed variants per RFC 7232, deprecated config alias support (``reload_dir`` → ``reload_dirs``), CLI ``request_timeout``/``startup_timeout`` passthrough, ``check`` command signage validation, and fair ``max_connections`` remainder distribution across workers.

### Security

- Fix 12 security issues: broken exception syntax in 3 files, CRLF injection in proxy headers and request IDs, unenforced `max_headers` and `websocket_max_message_size` limits, weak TLS cipher suite, world-writable UDS socket, and incomplete security middleware headers.

---

## [0.5.1] — 2026-04-06

Patch release: fork-context fix for process workers and dependency updates.

### Changed

- **Dependency updates** — Bump all runtime and dev dependencies to latest versions. Adopt milo-cli 0.1.1 with built-in `--completions`, `--mcp`, `--verbose`, `--quiet`, `--no-color`, `--dry-run` flags and PyPI version checking. (#28)
- **Startup banner redesign** — Flatten banner layout, suppress per-worker lines in pretty mode, collapse shutdown output, simplify Ready line. (#28)
- **Version notice template** — New `version_notice.kida` template for branded PyPI update notices; demote redundant TLS/H3/ALPN log lines to debug. (#28)

### Fixed

- **Fork context for process workers** — Explicitly use the `"fork"` multiprocessing context so ASGI apps containing closures (from middleware wrappers or framework decorators) are inherited via the forked address space instead of being pickled. Fixes startup crashes on platforms where `"fork"` is available (including macOS) when the default `"spawn"` method cannot serialize the app callable. On Windows, where `"fork"` is unavailable, use thread workers instead. (#29)

---

## [0.5.0] — 2026-04-03

Elm Architecture lifecycle, milo-cli adoption, bench command, and modern Python 3.14t patterns.

### Added

- **`pounce bench` CLI command** — Standardized benchmarking with wrk integration and formatted result tables. (#26)
- **Lifecycle events API** — Public API for lifecycle events (`ConnectionOpened`, `ResponseCompleted`, etc.) enabling external observability and metrics hooks. (#26)
- **Hypothesis fuzzing** — 27 property-based tests across all protocol parsers for deeper correctness coverage. (#26)
- **`DisplayConfig`** — Configurable signage modes and startup display resolution for branded server output. (#25)
- **`pounce info` command** — System diagnostic panel showing Python version, GIL state, installed dependencies, and detected frameworks. (#21)
- **`pounce check` command** — Pre-flight validator for app import, port availability, TLS config, and server configuration. (#21)
- **Branded tracebacks** — Crash reports rendered through kida templates instead of raw Python stack traces. (#21)
- **milo-cli integration** — Replace argparse with milo's CLI class for subcommands, MCP server, `llms.txt`, and type-driven parsing. Branded kida templates for all server lifecycle output — startup banner, ready/shutdown/reload phases, worker events, access logs, and error display. (#20)

### Changed

- **Elm Architecture lifecycle** — Replace 16 procedural lifecycle output functions with a centralized Store + Reducer + Render Middleware pattern. Server, supervisor, and reload dispatch typed actions instead of calling render functions directly. (#22)
- **Modern Python 3.14t patterns** — `Final` annotations on module-level constants, `StrEnum` for `Phase`/`WorkerMode`/`WorkerExecutionMode`, `kw_only=True` on `ServerConfig` and lifecycle dataclasses, `TCPWorker` Protocol for the supervisor contract. (#23)
- **Stricter linting** — Expanded ruff rules (`S`, `A`, `T20`, `DTZ`, `FBT`) and stricter ty type checker configuration. (#23)
- **Single-pass `_classify_request()`** — Replaces 4–5 separate header scans per sync-worker request, fuses content-length tracking, removes redundant `.lower()` on pre-lowered headers. (#24)
- **Reduced static file syscalls** — Static file stat calls reduced from ~7 to ~3 per request. (#24)
- **CI improvements** — Added `cancel-in-progress` and `--maxfail` to CI pipeline for faster feedback. (#18)

### Fixed

- **Content-Length preservation** — Only strip Content-Length from response headers when compressing; preserve app-provided value otherwise. (#24)
- **HTTP/1.0 keep-alive** — Track `Connection` header presence so HTTP/1.0 keep-alive is honoured correctly. (#24)
- **Deployment docs** — Replace broken symlinks with proper site pages containing YAML frontmatter. (#19)

### Docs

- Deep audit of all site docs, internal docs, and roadmap — removed false claims (brotli, sendfile, phantom CLI flags), fixed types and defaults, corrected thread-safety advice, rewrote roadmap with competitive positioning. (#17)
- Narrative docs leading with the performance story: 3 µs parser, rolling reload, AcceptDistributor, competitive comparison tables. (#26)
- nogil-patterns.md with 10 reusable free-threading patterns for Python 3.14t. (#26)

---

## [0.4.0] — 2026-03-25

First-class testing API, graceful shutdown overhaul, thread-safety fixes, and documentation sync.

### Added

- **Testing API** — `pounce.testing.TestServer` runs a real pounce server in a background thread for tests. Supports context manager (`with TestServer(app) as server:`), async context manager, and a `serve()` async helper. Exposes `.url`, `.host`, `.port`, `.is_running`. Auto-registered `pounce_server` pytest fixture via `pytest11` entry point — install pounce and the fixture is available automatically. (#15)
- **`Server.bound_addr`** — Public property exposing the server's bound `(host, port)` tuple after startup, used by `TestServer` for ephemeral port discovery. (#15)
- **Server startup readiness signal** — Internal `threading.Event` set when the server is ready to accept connections, enabling reliable startup synchronization in `TestServer`. (#15)

### Changed

- **Graceful shutdown** — `shutdown_timeout` is applied per worker (TCP and H3 worker threads/processes join in parallel) instead of a single monotonic deadline shared across all joins. AcceptDistributor and AsyncPool each use up to `shutdown_timeout` independently. Full shutdown calls `start_draining()` on thread-mode workers so new connections receive 503 while draining. Thread workers that outlive the join are logged accurately (cannot SIGTERM a thread); process workers still get SIGTERM/SIGKILL. (#12)
- **Worker executor teardown** — Per-worker `ThreadPoolExecutor.shutdown()` runs via `run_in_executor` on a dedicated one-thread pool (not the loop default executor being torn down), wrapped in `asyncio.wait_for` so the event loop is not blocked indefinitely by stuck sync handlers. (#12)
- **Logging TTY detection** — Log formatting respects `sys.stderr.isatty()` instead of unconditionally applying TTY-style output. (#12)

### Fixed

- **Thread-safe connection counter** — `Worker._active_connections` now uses `threading.Lock` for atomic increment/decrement, fixing a race condition under concurrent access on free-threaded Python. (#14)
- **Single-pass header filter** — ASGI bridge response send path filters hop-by-hop headers in a single pass instead of multiple iterations. (#14)

### Docs

- Synced all docs (README, ARD, FEATURES, PRD, site pages) with actual codebase — removed stale claims, corrected protocol descriptions, updated architecture diagrams. (#13)

---

## [0.3.1] — 2026-03-19

Public error type exports for downstream consumers.

### Added

- Re-export `PounceError`, `LifespanError`, `TLSError`, `SupervisorError`, and `ReloadError` from `pounce` top-level package so downstream packages (e.g. chirp) can `from pounce import PounceError` instead of reaching into private `pounce._errors`

---

## [0.3.0] — 2026-03-17

Multi-worker sync performance — matching uvicorn at 30k req/s, pure Python.

### Added

- **Fast HTTP/1.1 parser** — `_fast_h1.py` replaces h11 on the sync worker hot path. Direct bytes parsing (~3 µs/req vs ~22 µs for h11) with full safety checks: method validation, header size limits (16 KiB), null byte/control character injection rejection, duplicate Content-Length detection, Content-Length + Transfer-Encoding conflict detection (RFC 7230 §3.3.3 request smuggling prevention)
- **Shared header utility** — `_headers.py` consolidates 7 copies of `_get_header` scattered across worker, sync_worker, async_pool, and handler modules into a single `get_header()` function
- **Shared request pipeline** — `_request_pipeline.py` provides `prepare_request()`, `negotiate_compressor()`, `log_request()`, and `is_trusted_peer()` — shared between Worker and SyncWorker for feature parity and code deduplication
- **TCP_NODELAY** — Set on accepted connections in `accept_distributor.py` for lower latency

### Changed

- **Middleware classification cached** — `MiddlewareStack.__init__` now classifies middleware once via `inspect.signature` instead of per-request, eliminating repeated reflection overhead
- **`ConnectionClosed` → `ConnectionCompleted`** — Lifecycle event renamed for clarity (`lifecycle.py`, `metrics.py`). The protocol-level `ConnectionClosed` in `protocols/_base.py` is unchanged
- **`trusted_hosts` type** — Changed from `tuple[str, ...]` to `frozenset[str]` for O(1) lookup. Added `trusted_hosts_wildcard: bool` flag computed in `__post_init__` to avoid per-request `"*" in trusted_hosts` checks
- **Single-pass header scanning** — `asgi/bridge.py` response send path now detects Content-Length and Transfer-Encoding in a single pass instead of separate `any()` calls
- **OpenTelemetry optimizations** — `_otel.py` pre-instantiates `TraceContextTextMapPropagator` at module level and filters to only trace headers (`traceparent`, `tracestate`) before conversion
- **Static file header extraction** — `_static.py` extracts `if-none-match`, `range`, and `accept-encoding` in a single pass over request headers
- **Shared socket for thread workers** — `net/listener.py` `create_listeners()` gains `shared=True` parameter; thread workers share one socket fd instead of using SO_REUSEPORT (avoids macOS distribution issues)
- **Server orchestrator refactored** — `server.py` simplified lifecycle state machine
- **Supervisor simplified** — `supervisor.py` streamlined worker spawning and health monitoring
- **Sync worker performance** — `sync_worker.py` major refactor for throughput parity with uvicorn

---

## [0.2.2] — 2026-03-12

### Added

- **Sync worker mode** — `SyncWorker` for blocking I/O request-response workloads. On Python 3.14t, runs in threads with true parallelism. One request at a time per thread, no asyncio. Streaming and WebSocket requests hand off to an async pool. CLI: `--worker-mode auto|sync|async` (default: auto — sync on 3.14t, async on GIL)
- **CPU affinity** — Pin each worker to a dedicated CPU core (Linux only). Reduces cache thrashing. CLI: `--cpu-affinity`
- **Per-worker ThreadPoolExecutor** — `executor_threads_per_worker` config prevents executor contention when multiple workers share one process (3.14t thread mode). 0 = auto-size
- **Response frame templates** — Fused sync path with `recv_into` buffer and `sendmsg` scatter-gather for lower overhead
- **Sync ASGI bridge** — `call_asgi_sync()` and `SyncApp` protocol for sync-style ASGI dispatch without asyncio
- **Async pool** — `AsyncPool` for streaming/WebSocket handoff from sync workers
- **Accept distributor** — Kernel-level connection distribution for multi-worker sync mode
- **Documentation** — Performance guide (`docs/about/performance.md`), thread-safety guide (`docs/about/thread-safety.md`)

### Changed

- **Scope building** — Optimized: cached ASGI version, tuple-based structures, deduplicated target split
- **Project metadata** — Updated description and keywords; Homepage/Documentation URLs point to lbliii.github.io

### Removed

- **httptools backend** — `pounce[fast]` extra removed. HTTP/1.1 parsing is h11-only (pure Python, free-threading compatible). httptools used Limited API C extensions incompatible with free-threaded Python.

---

## [0.2.1] — 2026-03-06

### Changed

- **HTTP/3 backend: aioquic → zoomies** — Replace aioquic with zoomies for HTTP/3 support. zoomies is sans-I/O, free-threading-native, and compatible with Python 3.14t. aioquic uses Limited API C extensions that do not work with free-threaded Python. `pounce[h3]` now installs `bengal-zoomies>=0.1.1` instead of `aioquic>=1.3.0`. 0-RTT is disabled until zoomies exposes it.

---

## [0.2.0] — 2026-02-13

Security hardening, production features, observability, and developer experience.

### Added

#### Security Hardening

- **Proxy header validation** — `_proxy.py` validates and applies `X-Forwarded-For`,
  `X-Forwarded-Proto`, and `X-Forwarded-Host` headers only from trusted peers
  (`ServerConfig.trusted_hosts`). Untrusted proxy headers are silently stripped to
  prevent IP spoofing. Supports H1 and H2 bridges
- **CRLF response header sanitization** — `_sanitize_headers()` in the ASGI bridge
  strips `\r` and `\n` characters from all response header names and values before
  serialization. Prevents header injection attacks from ASGI apps. Active on both
  HTTP/1.1 and HTTP/2
- **Slowloris protection** — `header_timeout` (default: 10s) limits the time to receive
  complete request headers. Uses a separate timeout from `keep_alive_timeout` for the
  initial header read vs inter-request idle period. CLI: `--header-timeout`
- **Narrowed exception handling** — Replaced broad `except Exception` and
  `contextlib.suppress(Exception)` blocks in worker with specific exception types
  (`OSError`, `ConnectionError`, `h11.LocalProtocolError`). Prevents silent swallowing
  of unexpected errors
- **HEAD compression guard** — Compression is disabled for HEAD responses to preserve
  the `Content-Length` header (compressor would mismatch sizes)
- **Bodyless response guard** — Compression is disabled for 204 and 304 responses
  (RFC 9110 §6.4.1) to prevent compressor flush bytes from producing a body

#### Network Completeness

- **Unix domain socket support** — `ServerConfig.uds` for UDS binding, with stale
  socket cleanup on startup and shutdown. All workers share a single UDS fd.
  CLI: `--uds /run/pounce.sock`. `net/listener.py` implements `_bind_unix_socket()`
  and `cleanup_unix_socket()`
- **Streaming body size enforcement** — `max_request_size` is now enforced for chunked
  and streaming request bodies (not just Content-Length). Applies to both H1 (via
  `_run_with_body_reader`) and H2 (per-stream byte tracking)
- **UDS peername handling** — Worker correctly handles Unix socket peername (string path
  or empty) instead of assuming a `(host, port)` tuple
- **503 backpressure response** — When `max_connections` is reached, new connections
  receive `503 Service Unavailable` with `Retry-After: 5` instead of silent close

#### Observability

- **Request ID generation** — `_request_id.py` generates UUID4 hex IDs for every
  request. Trusted proxies' `X-Request-ID` headers are honoured. IDs are injected into
  the ASGI scope (`scope["extensions"]["request_id"]`), response headers (`X-Request-ID`),
  and access logs (text and JSON). Works across H1 and H2
- **Built-in health endpoint** — `_health.py` responds to `GET` at
  `ServerConfig.health_check_path` (e.g. `/health`) before ASGI dispatch. Returns JSON
  with status, uptime, worker ID, and active connections. Excluded from access logs.
  CLI: `--health-check-path /health`
- **Prometheus metrics** — `metrics.py` provides `PrometheusCollector` implementing
  `LifecycleCollector`. Tracks `http_requests_total`, `http_request_duration_seconds`
  (histogram), `http_connections_active`, `http_requests_in_flight`, and
  `http_bytes_sent_total`. Thread-safe via `threading.Lock`. Export in Prometheus text
  exposition format via `collector.export()`
- **Built-in `/metrics` endpoint** — Configurable Prometheus scrape endpoint
  (`ServerConfig.metrics_path`, default `/metrics`) with zero external dependencies
- **Access log request IDs** — Text format appends `[<12-char-id>]`; JSON format
  includes full `request_id` field

#### Static File Serving

- **`_static.py`** — Pre-compressed files (`.gz`, `.zst`),
  ETags, and range requests. Configurable via `ServerConfig.static_files`,
  `static_precompressed`, `static_cache_control`

#### Middleware & Extensibility

- **Server-level middleware** — `ServerConfig.middleware` accepts a list of ASGI3
  middleware callables applied before the app
- **ASGI lifespan state sharing** — Lifespan state propagated to worker scopes for
  spec-compliant shared app state

#### Graceful Operations

- **Zero-downtime graceful reload** — SIGHUP triggers rolling worker restart with
  connection draining. `reload_timeout` configurable
- **Connection draining** — Enhanced graceful shutdown with `shutdown_timeout` for
  Kubernetes and orchestration platforms

#### WebSocket & Protocol

- **WebSocket permessage-deflate** — RFC 7692 compression for WebSocket connections.
  `ServerConfig.websocket_compression` (default: True)

#### Developer Experience

- **Development error pages** — `_debug.py` provides rich HTML tracebacks with syntax
  highlighting (Rosettes), local variables, and request context. Production-safe
  (`debug=False` returns plain 500)
- **Hot reload utilities** — `_hot_reload.py` for in-process module reimport without
  full process restart. `ServerConfig.reload_include`, `reload_dirs` for configurable
  file watching

#### Production Integrations

- **OpenTelemetry** — `_otel.py` native distributed tracing with OTLP export.
  `ServerConfig.otel_endpoint`, `otel_service_name`
- **Sentry** — `_sentry.py` optional error tracking. `sentry_dsn`, `sentry_environment`,
  `sentry_release`
- **Per-IP rate limiting** — `_rate_limiter.py` token bucket algorithm.
  `rate_limit_enabled`, `rate_limit_requests_per_second`, `rate_limit_burst`
- **Request queueing** — `_request_queue.py` bounded queue with load shedding (503).
  `request_queue_enabled`, `request_queue_max_depth`

#### Lifecycle & Logging

- **Structured lifecycle logging** — `lifecycle_logging` config for connection/request
  events with correlation IDs. `log_slow_requests_threshold` for slow request detection

#### H1/H2 Feature Parity

- All security and observability features wired for both HTTP/1.1 and HTTP/2 handlers

#### Tests

- New test modules: `test_request_id`, `test_health`, `test_proxy`, `test_security`,
  `test_metrics`, `test_metrics_endpoint`, `test_h2_bridge`, `test_listener_uds`,
  `test_bridge`, `test_static`, `test_middleware`, `test_graceful_reload`, `test_hot_reload`,
  `test_connection_draining`, `test_debug_error_pages`, `test_lifecycle_logging`,
  `test_lifespan_state`, `test_otel`, `test_rate_limiter`, `test_request_queue`,
  `test_sentry`, `test_websocket_compression`
- Integration tests for static files, WebSocket compression, lifespan state

---

## [0.1.0] — 2026-02-09

Initial release of Pounce — a free-threading-native ASGI server for Python 3.14t.

### Added

#### Configurable Reload Watch

- `ServerConfig.reload_include` — extra file extensions to watch beyond the built-in set
  (`.py`, `.yaml`, `.toml`, etc.). Pass a tuple of extensions like `(".html", ".css", ".md")`
  to trigger reloads on non-Python file changes
- `ServerConfig.reload_dirs` — extra directories to watch alongside the current working
  directory. Useful when templates or static assets live outside the project root
- CLI flags: `--reload-include ".html,.css,.md"` and `--reload-dir ./templates` (repeatable)
- Extensions without a leading dot are auto-prefixed (e.g. `"html"` becomes `".html"`)
- `_reload.py` functions (`_should_watch`, `_snapshot`, `detect_changes`, `watch_for_changes`)
  accept an `extensions` / `extra_extensions` parameter for runtime customization
- `parse_extensions()` and `parse_dirs()` helpers extracted in `_cli.py` for testability

#### Hot Reload with Module Reimport

- `reimport_app()` in `_importer.py` clears project-local modules from `sys.modules`,
  deletes stale `.pyc` bytecode caches, and calls `importlib.invalidate_caches()` before
  reimporting — code changes on disk take effect without a full process restart
- Single-worker and multi-worker reload paths both reimport when `app_path` is provided
- `Server` and `Supervisor` accept `app_path: str | None` to enable reimport on reload
- `_clear_local_modules()` resolves paths with `os.path.realpath()` for macOS symlink safety

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

[0.5.1]: https://github.com/lbliii/pounce/releases/tag/v0.5.1
[0.5.0]: https://github.com/lbliii/pounce/releases/tag/v0.5.0
[0.4.0]: https://github.com/lbliii/pounce/releases/tag/v0.4.0
[0.3.1]: https://github.com/lbliii/pounce/releases/tag/v0.3.1
[0.3.0]: https://github.com/lbliii/pounce/releases/tag/v0.3.0
[0.2.2]: https://github.com/lbliii/pounce/releases/tag/v0.2.2
[0.2.1]: https://github.com/lbliii/pounce/releases/tag/v0.2.1
[0.2.0]: https://github.com/lbliii/pounce/releases/tag/v0.2.0
[0.1.0]: https://github.com/lbliii/pounce/releases/tag/v0.1.0
