# Changelog

All notable changes to pounce will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
