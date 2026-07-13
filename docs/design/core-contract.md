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
| HTTP/2 | Optional protocol extra | `bengal-pounce[h2]`, `http2_enabled` | Supported only when `h2` is installed and enabled by listener negotiation/config. Operators may disable h2 ALPN to force HTTP/1.1 at a Pounce-owned TLS origin. Missing extras must fail clearly. |
| WebSocket | Optional protocol extra | `bengal-pounce[ws]` | Supported only when `wsproto` is installed. HTTP/1 WebSocket and WebSocket-over-H2 claims need separate wire-to-ASGI proof. |
| HTTP/3 | Optional protocol extra | `bengal-pounce[h3]`, TLS, UDP | Supported through `bengal-zoomies` when enabled. QUIC lifecycle exceptions, 0-RTT policy, limits, and benchmark snapshot caveats remain explicit. |
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
| WebSocket | Optional | `bengal-pounce[ws]` | Handshake, accept/send/receive/close, disconnect, subprotocol, compression, and missing-extra tests. `permessage-deflate` activates only after an explicit client offer; config permission alone never emits an extension response or compressed frame. WebSocket-over-H2 requires RFC 8441 integration proof before broad claims. |
| HTTP/3 | Optional | `bengal-pounce[h3]` plus TLS/UDP | Real QUIC request tests, TLS/Alt-Svc behavior, malformed/limit handling, 0-RTT policy, lifecycle/reload proof, benchmark evidence, and missing-extra diagnostics. Over-budget drain closes QUIC after `shutdown_timeout`; WebSocket-over-H3 is a separate unsupported protocol. |

Timeouts are state-specific rather than interchangeable:

- `header_timeout` bounds receipt of request headers;
- `request_timeout` bounds each wait for the next request-body event after
  headers are complete and emits `POUNCE_TIMEOUT_REQUEST_BODY` on expiry;
- `keep_alive_timeout` applies only between HTTP requests, or to an HTTP/2
  connection with no active streams;
- accepted WebSockets and active streaming/SSE responses are not idle
  keep-alive connections;
- `write_timeout` bounds blocked response delivery on HTTP/1.1, HTTP/2, and
  WebSocket transports and emits `POUNCE_TIMEOUT_WRITE` before closing the
  slow peer;
- HTTP/3 output is queued synchronously into QUIC, so it has no stream-writer
  drain to bound with `write_timeout`; QUIC connection liveness remains
  governed by `http3_idle_timeout`. HTTP/3 request-body waits still use
  `request_timeout`.

## Proxy And Edge Topology Contract

Each reverse-proxy hop negotiates its own transport and HTTP protocol. The
client-to-edge protocol does not determine the edge-to-Pounce protocol, and the
ASGI `scope["http_version"]` describes only the connection accepted by Pounce.
For example, production incident #231 observed a Railway edge use HTTP/2 to the
origin even when the external client used HTTP/1.1. Public docs must describe
that as observed deployment evidence, not a guarantee for every Railway
service or future platform version.

Pounce treats proxy-derived identity as untrusted unless the direct peer is in
`trusted_hosts`:

- an empty `trusted_hosts` strips `X-Forwarded-*` before app dispatch;
- a trusted peer may replace `scope["client"]` from `X-Forwarded-For`,
  `scope["scheme"]` from `X-Forwarded-Proto`, and both `scope["server"]` and
  the `Host` header from `X-Forwarded-Host`;
- `forwarded_for_trusted_hops` selects the client address from the right side
  of the forwarded chain, matching the number of trusted proxy hops;
- `X-Real-IP` is not a Pounce identity input. It remains an ordinary request
  header and must not be treated by an application as authenticated client
  identity without a separately verified proxy boundary.

TLS termination is also hop-specific. A platform edge may terminate public
TLS and speak plain HTTP to Pounce; Pounce-owned TLS and ALPN apply only when
`ssl_certfile` and `ssl_keyfile` terminate TLS at the Pounce listener.
The implementation source is `src/pounce/_proxy.py`; cross-protocol authority
proof lives in `tests/unit/test_tenant_scope_matrix.py` and Railway example
proof lives in `tests/integration/test_examples.py`.

## Framework Embedding Contract

A framework embedding Pounce owns an adapter boundary; it must not imply that
its configuration exposes Pounce behavior that it silently leaves at a
different default. The preferred integration accepts a complete frozen
`ServerConfig` and passes it directly to `Server`. An adapter that mirrors
individual settings instead must maintain an explicit parity table and tests.

At minimum, an embedding framework must make intentional decisions for:

- application identity: pass `app_path` when subinterpreter bootstrap or code
  reimport is supported, and otherwise reject those modes clearly;
- worker lifecycle: `workers`, `worker_mode`, `worker_startup_failure`,
  `startup_timeout`, `shutdown_timeout`, and `reload_timeout`;
- network safety: `header_timeout`, `keep_alive_timeout`, `request_timeout`,
  `write_timeout`,
  request/header/connection limits, and proxy trust/authority fields;
- execution capacity: `executor_threads_per_worker` when the framework sends
  blocking work through `asyncio.to_thread()`;
- operator ownership: whether health, readiness, metrics, introspection, TLS,
  and logging are provided by Pounce, the framework, or the deployment edge.

Framework-owned policy is valid, but it must be documented and tested rather
than emerging from omitted constructor arguments. The public adapter checklist
is `site/content/docs/deployment/embedding.md`.

## Worker And Lifecycle Matrix

| Mode | Startup Contract | Reload Contract | Shutdown Contract | Known Limits |
|---|---|---|---|---|
| `workers=1` | One app instance on the direct async path when mode is `auto`, `sync`, or `async`. | Development reload may restart the single worker path; SIGHUP-style rolling generation swap is not the primary contract. | Graceful shutdown via server shutdown event and lifespan shutdown. | No multi-worker drain generation; explicit subinterpreter mode is the exception and uses the supervisor. |
| Thread workers on Python 3.14t | With multiple workers, `worker_mode="auto"` resolves to sync threads sharing one interpreter, app object, and frozen config. | Rolling generation swap can drain old thread workers while new workers start. | Per-worker drain and join behavior is supervised. | Shared mutable app state remains the app's responsibility. |
| Process workers on GIL builds | With multiple workers, `worker_mode="auto"` resolves to async forked processes when available. | Process-mode reload may have different availability and downtime characteristics than thread-mode rolling reload. | Supervisor coordinates process shutdown and joins. | App import/fork constraints apply; no shared app object. |
| Subinterpreter ASGI web workers (stable) | Explicit `worker_mode="subinterpreter"` path, including `workers=1`; embedded callers must provide `app_path`. The main interpreter owns lifespan and copies JSON-safe state into each isolated worker. | Replacements must report serving before old acceptors retire; old connections drain within `reload_timeout`. Tests exercise concurrent requests and exact state after reload. | Supervisor drain/shutdown is bounded; health-monitor respawn receives the original JSON-safe lifespan state. | Async workers only; app and extensions must support subinterpreters; process-local resources use worker hooks. Job/hybrid roles from #230 are out of scope. See `docs/design/subinterpreter-workers.md`. |

Startup output and the opt-in `/_pounce/info` endpoint expose the resolved
worker model as `single (async)`, `thread (sync)`, `process (async)`, or
`subinterpreter (async)` rather than requiring operators to infer it from the
configured value.

When enabled, `/_pounce/info` also reports the Pounce version, Python version
and build fingerprint, whether the interpreter supports free-threading,
runtime GIL state, and the optional operator-supplied `POUNCE_BUILD_ID`. That
named environment value is explicitly public and returned verbatim; other
environment variables are not inspected or exposed.

Subprocess signal proof in `tests/integration/test_signal_lifecycle.py` covers
CLI SIGTERM clean exit, SIGHUP recovery to serving traffic, and mixed-traffic
SIGTERM drain. The mixed-traffic matrix asserts complete in-flight slow and
streaming responses, bounded clean outcomes for new connections, bounded exit,
zero orphan child processes, and listener release across async/process,
subinterpreter, and free-threaded sync execution. Reproducible artifact-shaped
profiling for the same contract lives in `benchmarks/drain_profile.py`.

SIGTERM shutdown ordering is a runtime contract: emit the bounded
`pounce.worker.draining` hook, stop accepting new work, drain in-flight
requests up to `shutdown_timeout`, force-close work that exceeds that bound,
run per-worker `pounce.worker.shutdown` hooks, complete
ASGI `lifespan.shutdown`, then release listeners and exit. The ordering between
in-flight completion and final lifespan shutdown is enforced across worker
modes by `test_sigterm_runs_lifespan_shutdown_after_inflight_completion`.

The built-in `health_check_path` is a readiness endpoint. Deployments should
configure it as `/readyz`: it returns 200 while the worker accepts traffic and
503 with `{"status":"draining"}` once drain begins. `GET` and `HEAD` have the
same status and headers; `HEAD` has no body. A separate `/healthz` liveness
endpoint, when required by the platform, is application-owned and should remain
successful until the process can no longer make forward progress. During final
connection rejection, late HTTP/1 `GET` and `HEAD` requests that match the
configured readiness path preserve that structured JSON 503 contract. Other
paths and methods receive the generic drain 503, while requests arriving after
the listener closes may receive a refused connection; all outcomes mean not
ready.

Every serving mode emits `pounce.worker.startup` before accepting requests,
`pounce.worker.draining` at drain start, and `pounce.worker.shutdown` after
draining. Each scope carries numeric `worker_id` and `generation`; the draining
scope also carries `reason` (`reload` or `shutdown`) and the timeout budget.
HTTP scopes expose the same identity as `extensions["pounce.worker"]`, so an
app can close only streams pinned to the retiring generation. The drain hook
is limited to one second and runs within `shutdown_timeout`; apps should use it
to signal existing stream registries, not perform long cleanup. The hooks run
on the same per-worker event loop used for that worker's
inline ASGI requests; sync workers use their private runner loop. Requests
handed to the async streaming pool follow that pool's existing loop ownership.
The default `worker_startup_failure="ignore"` policy preserves compatibility
with apps that reject unknown scopes. With `"shutdown"`, a hook failure aborts
boot before readiness, exits non-zero, and reports
`POUNCE_WORKER_STARTUP_FAILED`.

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
| `http_streams_active` | gauge |
| `http_stream_duration_seconds` | histogram (`_bucket`/`_sum`/`_count`) |
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
| `StreamOpened` | streaming HTTP response started |
| `StreamClosed` | streaming HTTP response ended, with completion reason and duration |
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
| Production suitability | Use the declared contract tier and name supported paths and limitations; reserve "stable" for machine-tested behavior. | CI coverage summary, known limitations, and docs matching implementation. |

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
