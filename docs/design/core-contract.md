# Pounce Core Contract

Status: implemented contract guide, not a roadmap.
Date: 2026-05-09.

This document defines what Pounce owns, what it supports as optional surface
area, and what proof is required before public docs describe a behavior as a
guarantee. It is a contributor and reviewer contract. It does not add runtime
behavior.

## Scope

Pounce owns the server behavior that protects ASGI applications from untrusted
network input and unstable runtime lifecycle events. Pounce may provide optional
helpers around that core, but helpers must not make the protocol, ASGI, worker,
or transport core harder to reason about.

Normative language:

- **Owned** means Pounce treats the behavior as a compatibility contract.
- **Optional** means the behavior is supported only when the relevant config,
  dependency extra, or integration is enabled.
- **Helper** means Pounce provides convenience, but the feature must remain
  removable from the request path when disabled.
- **Experimental** means docs must state limitations and required proof before
  production claims.

## Owned Core

| Area | Contract | Source Of Truth | Required Proof |
|---|---|---|---|
| ASGI serving | Build valid scopes, receive/send callables, streaming, disconnects, and lifespan state for supported protocols. | `src/pounce/asgi/`, `src/pounce/worker.py`, `src/pounce/sync_worker.py` | ASGI compliance, framework compatibility, streaming, disconnect, and lifespan tests. |
| HTTP/1.1 | Parse request heads and bodies safely, serialize responses, enforce limits, and report malformed input with `POUNCE_*` diagnostics. | `src/pounce/protocols/h1.py`, `src/pounce/_fast_h1.py`, `src/pounce/_response_frame.py` | Parser unit tests, malformed-input tests, fuzz/property tests, limits tests, and parser benchmarks for hot-path changes. |
| Runtime lifecycle | Start, supervise, drain, reload, and shut down workers without ambiguous socket or worker ownership. | `src/pounce/server.py`, `src/pounce/supervisor.py`, `src/pounce/worker.py`, `src/pounce/sync_worker.py` | Single-worker, multi-worker, reload, shutdown, signal, and lifecycle failure tests. |
| Configuration | Validate public inputs at the boundary and keep `ServerConfig` frozen after construction. | `src/pounce/config.py`, `src/pounce/_config_file.py`, `src/pounce/_config_schema.py`, `src/pounce/_cli.py` | Config validation, schema, TOML, redaction, CLI, and type/API tests. |
| Operator diagnostics | Give operators stable error codes, actionable hints, logs, health/info/metrics contracts, and safe redaction. | `src/pounce/_errors.py`, `src/pounce/_output.py`, `src/pounce/logging.py`, `src/pounce/_introspect.py`, `docs/troubleshooting.md` | Error-code catalog tests, troubleshooting coverage tests, redaction allowlist tests, and CLI/output tests. |

## Optional Surface

| Feature | Classification | Dependency Or Flag | Contract Boundary |
|---|---|---|---|
| HTTP/2 | Optional protocol extra | `bengal-pounce[h2]` | Supported only when `h2` is installed and enabled by listener negotiation/config. Missing extras must fail clearly. |
| WebSocket | Optional protocol extra | `bengal-pounce[ws]` | Supported only when `wsproto` is installed. HTTP/1 WebSocket and WebSocket-over-H2 claims need separate wire-to-ASGI proof. |
| HTTP/3 | Optional protocol extra, limited parity | `bengal-pounce[h3]`, TLS, UDP | Supported through `bengal-zoomies` when enabled. Docs must state lifecycle, reload, 0-RTT, limit, and benchmark caveats until parity is proven. |
| TLS | Optional transport support | `ssl_certfile`, `ssl_keyfile`, optional `truststore` | Owns listener TLS setup and ALPN; certificate management stays with operators. |
| Static files | Optional server helper | `static_files` | Convenience ASGI handler. Must not alter protocol or worker contracts when disabled. |
| Middleware | Optional server helper | `middleware` | ASGI middleware composition. Must not add framework-specific branches to the server core. |
| Compression | Optional response helper | `compression`, `compression_dictionaries` | Negotiation and response encoding must preserve HTTP semantics and HEAD/content-length safety. |
| Rate limiting | Optional backpressure helper | `rate_limit_enabled` | Per-IP token bucket, enforced PER WORKER (shared only in thread mode). Aggregate per-IP ceiling is `rate x workers` on process/subinterpreter builds. Not a security/auth boundary. |
| Request queueing | Optional backpressure helper | `request_queue_enabled` | Load-shedding helper, PER WORKER in ALL modes (per-event-loop semaphore). Effective shed depth is `max_depth x workers`. Must preserve clear 503 behavior and bounded resource use. |
| Prometheus metrics | Optional operator endpoint | `metrics_enabled` | Endpoint and metric names are operator contracts once documented. |
| OpenTelemetry | Optional integration | `otel_endpoint` | Integration wrapper only. Instrumentation must be removable when disabled. |
| Sentry | Optional integration | `sentry_dsn` | Integration wrapper only. Error handling must remain correct without Sentry. |
| Debug error pages | Optional development helper | `debug=True` | Development-only response rendering. Must not expose internals by default. |
| Testing helpers | Developer tooling | `pounce.testing`, pytest fixture | Public testing API. Must track ASGI/server behavior accurately. |
| Bench command | Developer tooling | `pounce bench` | Benchmark harness, not a universal performance guarantee. Numeric public claims need environment and command details. |

## Protocol Support Matrix

| Protocol | Status | Install | Public Claim Requirements |
|---|---|---|---|
| HTTP/1.1 | Core | built in | Parser safety tests, response framing tests, limits tests, ASGI integration, and hot-path benchmark notes when changed. |
| HTTP/2 | Optional | `bengal-pounce[h2]` | Installed-extra tests, missing-extra diagnostics, stream/reset tests, scope parity, and docs for unsupported cases. |
| WebSocket | Optional | `bengal-pounce[ws]` | Handshake, accept/send/receive/close, disconnect, subprotocol, compression, and missing-extra tests. WebSocket-over-H2 requires RFC 8441 integration proof before broad claims. |
| HTTP/3 | Optional, limited parity | `bengal-pounce[h3]` plus TLS/UDP | Real QUIC request tests, TLS/Alt-Svc behavior, malformed/limit handling, 0-RTT policy, lifecycle/reload notes, and missing-extra diagnostics. |

For HTTP/2, `keep_alive_timeout` applies only when the connection has no
active streams. A peer may remain quiet while it receives a response, so the
idle timer must not cancel an in-flight response stream.

## Worker And Lifecycle Matrix

| Mode | Startup Contract | Reload Contract | Shutdown Contract | Known Limits |
|---|---|---|---|---|
| `workers=1` | One app instance in the main process. | Development reload may restart the single worker path; SIGHUP-style rolling generation swap is not the primary contract. | Graceful shutdown via server shutdown event and lifespan shutdown. | No multi-worker drain generation. |
| Thread workers on Python 3.14t | Multiple worker threads share one interpreter, app object, and frozen config. | Rolling generation swap can drain old thread workers while new workers start. | Per-worker drain and join behavior is supervised. | Shared mutable app state remains the app's responsibility. |
| Process workers on GIL builds | Workers run in forked processes when available. | Process-mode reload may have different availability and downtime characteristics than thread-mode rolling reload. | Supervisor coordinates process shutdown and joins. | App import/fork constraints apply; no shared app object. |
| Subinterpreter mode (beta) | Explicit `worker_mode="subinterpreter"` path. Beta: surfaced as `x-stability: beta` in `pounce config schema` and marked beta in the config docs. | Treat as limited/beta unless the specific lifecycle path has tests. | Requires explicit proof for state transfer and shutdown behavior. | Compatibility depends on subinterpreter-safe app/dependencies. See `docs/design/subinterpreter-workers.md` (status: beta). |

Subprocess signal proof currently covers CLI SIGTERM clean exit and SIGHUP
recovery to serving traffic in `tests/integration/test_signal_lifecycle.py`.
Load-bearing reload/drain claims still require mixed-traffic proof with bounded
503/disconnect behavior and orphan-worker checks.

## Observability Name Contract

The following identifier names are stable operator contracts: renaming or
removing one is a breaking change and requires a deprecation note. The set of
names is the contract; the data shape and label values are documented at each
source of truth. These are enabled only when the relevant feature is turned on
(see Optional Surface).

### Prometheus metric names

Source of truth: `src/pounce/metrics.py` (`PrometheusCollector.render`), served
at `metrics_path` when `metrics_enabled`.

| Metric | Type |
|---|---|
| `http_requests_total` | counter (labels: `method`, `status`) |
| `http_request_duration_seconds` | histogram (`_bucket`/`_sum`/`_count`) |
| `http_connections_active` | gauge |
| `http_requests_in_flight` | gauge |
| `http_bytes_sent_total` | counter |

### Lifecycle event names

Source of truth: `src/pounce/lifecycle.py`. These are the values of the `event`
field emitted by `LoggingCollector` and the event class names consumed by any
`LifecycleCollector`.

| Event | Meaning |
|---|---|
| `ConnectionOpened` | TCP connection accepted |
| `RequestStarted` | HTTP request head fully parsed |
| `ResponseCompleted` | HTTP response fully sent |
| `ClientDisconnected` | client closed the connection unexpectedly |
| `ConnectionCompleted` | TCP connection closed |

### Access/structured log field names

Source of truth: `src/pounce/lifecycle.py` (`LoggingCollector.record`,
`enabled` via `lifecycle_logging`). Each event serializes its dataclass fields
plus the injected `event` and `timestamp` fields; `ResponseCompleted` adds
`slow` when over the slow-request threshold.

| Field | Present on |
|---|---|
| `event` | all events (the lifecycle event name above) |
| `timestamp` | all events (replaces the internal `timestamp_ns`) |
| `connection_id`, `worker_id` | all events |
| `client_addr`, `client_port`, `server_addr`, `server_port`, `protocol` | `ConnectionOpened` |
| `method`, `path`, `http_version` | `RequestStarted` |
| `status`, `bytes_sent`, `duration_ms`, `method`, `streaming` | `ResponseCompleted` |
| `slow` | `ResponseCompleted` (only when over the slow-request threshold and not streaming) |
| `during_streaming` | `ClientDisconnected` |
| `requests_served`, `total_bytes_sent`, `duration_ms`, `reason` | `ConnectionCompleted` |

## Claim Ledger

Public claims must be phrased at the strongest level the proof supports.

| Claim Type | Acceptable Public Wording | Required Proof |
|---|---|---|
| Parser speed | "Measured at X on workload Y with command Z" or "designed for a low-overhead sync fast path." | Benchmark command, workload, hardware, Python build, worker mode, comparison target, duration, concurrency, and variance or explicit caveat. |
| Parser safety | "Rejects these tested ambiguity classes" rather than "full protection." | Named tests for duplicate/conflicting `Content-Length`, `Transfer-Encoding` conflicts, malformed methods/targets/headers, limits, chunked edge cases, and fuzz coverage. |
| Reload reliability | "Rolling reload in thread-worker mode drains old workers" rather than unqualified lossless-reload language. | Mode-specific lifecycle tests and explicit notes for single-worker, process, and subinterpreter behavior. |
| Protocol support | "Optional HTTP/2/WebSocket/HTTP/3 support via extras" rather than "four protocols" without install context. | Installed-extra and missing-extra tests, protocol integration tests, docs for degraded behavior. |
| Observability | "Optional metrics/tracing/logging endpoints and hooks" rather than implying all integrations are core. | Config, redaction, endpoint, and integration-disabled tests. |
| Production readiness | "Beta" plus supported paths and limitations. | CI coverage summary, known limitations, and docs matching implementation. |

## Feature Admission

A new or expanded public feature is admitted only when the PR answers these
questions in its description or linked design note:

1. What production failure, operator workflow, or compatibility gap does this
   solve?
2. Why should the server own it instead of app middleware, a reverse proxy, a
   process manager, or deployment tooling?
3. Is it core, optional protocol, helper, developer tooling, or external
   integration?
4. Which public surfaces change: `pounce.run`, `ServerConfig`, CLI, TOML,
   schema, redaction/info, logs, metrics, error codes, docs, examples, tests,
   benchmarks, or changelog?
5. What proof is required, and where is that proof in the PR?
6. What are the non-goals, limitations, degraded behavior, and rollback path?
7. Which stewards were consulted, and what findings were accepted or deferred?

## Non-Goals

Pounce does not own:

- Application-level auth, sessions, database pooling, or business middleware.
- Reverse-proxy responsibilities such as global traffic shaping, certificate
  issuance, CDN caching, or WAF policy.
- Framework-specific branches in the server core.
- Native hot-path dependencies.
- Performance claims that are not reproducible from a documented command.

## Collateral Rules

When a public contract changes, the same PR should update all affected surfaces:

| Contract | API/CLI | Programmatic | Protocol | Schema/Types | Docs | Examples | Tests |
|---|---|---|---|---|---|---|---|
| New config | CLI/TOML decision | `ServerConfig`, `pounce.run` policy | If protocol-facing | Schema and redaction | README/site/reference | If user-facing | Validation and parity |
| New protocol claim | CLI/info/check if applicable | Config flags | Parser/handler/bridge | Optional deps | README/site/protocol docs | Install snippets | Missing and installed extras |
| New performance claim | CLI bench if applicable | N/A | Hot path if relevant | N/A | README/site/bench docs | Benchmark command | Benchmark artifact or no-impact note |
| New operator endpoint | CLI/config if applicable | Config field | N/A | Redaction/schema | Troubleshooting/site | Deployment snippet | Endpoint and redaction tests |

Docs-only narrowing does not require runtime tests or a changelog when behavior
does not change, but it must state the source of truth and preserve README/site
parity.
