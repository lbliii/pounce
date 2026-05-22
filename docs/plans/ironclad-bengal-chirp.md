# Ironclad Bengal And Chirp Plan

**Status:** Active
**Created:** 2026-05-09
**Horizon:** May - September 2026
**Use cases:** Bengal static-site development; Chirp/LB Sonic multi-tenant
HTML-over-the-wire production
**Stewards consulted:** Runtime/Public API, Protocol, ASGI Bridge,
Transport/TLS, Performance Evidence, Tests/Compatibility. Planning, docs,
examples, site, and operator-output stewardship were synthesized locally from
their scoped `AGENTS.md` files.

## Executive Summary

The repo is feature-rich enough to support Bengal and Chirp. The first refresh
closed the P0 public-contract gaps in this plan; the next work should build
representative Bengal and Chirp/LB Sonic evidence.

Top convergence:

- `ServerConfig.static_files` is public, documented, wired into the real server
  path, and covered by H1/TOML integration tests; Bengal-shaped fixture and
  benchmark proof remain.
- Chirp/LB Sonic has code-level proof for multi-tenant scope correctness across
  host, authority, proxy, root path, lifespan state, and protocol variants; the
  remaining work is representative workload proof.
- Benchmarks must become reproducible workload evidence, not scattered claims.
- Railway-style deployment needs explicit `0.0.0.0:$PORT`, platform TLS
  termination, healthcheck, proxy, and observability guidance.
- Obsolete Phase 5b implementation plans were pruned so they cannot override the
  active roadmap.

## Contract Parity Matrix

| Contract | API/CLI | Programmatic | Protocol | Schema/Types | Docs | Examples | Tests | Benchmarks |
|---|---|---|---|---|---|---|---|---|
| Static files from config | TOML/schema exists; CLI unclear | `StaticFiles` works; `ServerConfig.static_files` wraps real server app | H1 proof exists; H2/H3 policy still belongs to optional protocol proof | Field exists | Claims exist | Manual static example exists | Real-server config and TOML tests exist | Missing Bengal profile |
| Bengal local dev | No first-class recipe | Public static config works; fuller Bengal fixture still needed | H1 static path has config proof | Static fields exist | Static docs exist | Synthetic example | Synthetic fixture | Missing |
| Chirp/LB Sonic production | Railway command missing | Chirp hello example only | Tenant scope, body-limit, and state parity proof exists; reload/workload proof still missing | Config fields exist | Generic deployment docs | Hello-world Chirp only | Generic tenant matrix exists; no representative Chirp fixture | Missing |
| Lifespan state | Public ASGI behavior | H1 integration covered | H2/H3/WS scope-state injection covered | State behavior implicit | Matches core contract | Lifespan examples exist | H1 integration plus H2/H3/WS unit proof | Missing workload breadth |
| Reload/drain | SIGHUP implementation; docs conflict with SIGUSR1 claim | Server API exists | H3 lifecycle gap | `reload_timeout` exists | Claims need measured proof | Production example generic | Load-bearing signal tests missing | Missing reload profile |
| Introspection | Config fields exist | Response builder exists | Same-listener behavior conflicts with ADR | Allowlist exists | ADR/docs conflict | None | Mostly unit tests | Not relevant |
| Middleware | Programmatic only | Stack exists | ASGI semantics matter | Callable fields not TOML-friendly | Examples conflict with code | Basic example | Unit-heavy | Missing real-server profile |

## Ranked Backlog

### P0. Static Config Contract

Status: closed on 2026-05-22. Keep this record for traceability; do not treat it
as the next open item unless the tests below regress.

- Steward: Runtime/Public API, Protocol, ASGI Bridge, Transport/TLS,
  Tests/Compatibility
- Area: Bengal static-site dev server
- Invariant: Documented `ServerConfig` and TOML behavior must reach the public
  request path.
- Original Evidence: `ServerConfig.static_files` existed and docs advertised it,
  while tests used `create_static_handler()` directly. Steward review did not
  find static wrapping in `Server._apply_integrations()`.
- User Impact Addressed: A Bengal user can now follow config docs without
  knowing to wrap the app manually.
- Required Fix: Wire `static_files` into server/app dispatch, or demote the
  config field and document `StaticFiles` as the only supported path. Accepted
  fix: `Server._apply_static_files()` wraps the app with `StaticFiles`.
- Required Proof: Real-worker H1 test using only
  `ServerConfig(static_files={"/": tmpdir})`; TOML `[static_files]`; root mount;
  mixed app/static fallthrough; 404 behavior; sendfile extension path; docs
  example test. Current proof: `tests/integration/test_static_config.py` covers
  the real-server config path, TOML loading, root mount, mixed fallback,
  cache-control propagation, and HTTP/1 content-length behavior. Bengal fixture
  breadth remains under P1.
- Collateral: `ROADMAP.md` status updated. No changelog: no runtime behavior
  changed in this cleanup.
- Confidence: High.

### P0. Request Body Limit Failure

Status: closed on 2026-05-22. Keep this record for traceability; do not treat it
as the next open item unless the tests below regress.

- Steward: Protocol
- Area: Chirp/LB Sonic production request bodies
- Invariant: Pounce must reject oversized input before ASGI sees partial data.
- Original Evidence: Steward review found H1/H2/H3 over-limit paths that could
  truncate or terminate the body stream instead of producing deterministic
  413/reset behavior.
- User Impact Addressed: Forum posts, forms, and uploads are rejected before app
  processing when the server can determine they exceed `max_request_size`.
- Required Fix: Convert over-limit bodies to deterministic `413` or protocol
  reset behavior with `POUNCE_LIMIT_*` diagnostics. Current behavior returns 413
  with `POUNCE_LIMIT_REQUEST_TOO_LARGE` for the tested H1/H2/H3 paths.
- Required Proof: H1 content-length, H1 chunked, H2 DATA, and H3 DATA tests
  proving the app is not called or receives a documented disconnect/reset.
  Current proof: `tests/integration/test_limits.py`,
  `tests/unit/test_h2_handler.py`, and `tests/unit/test_h3_handler.py`.
- Collateral: Existing troubleshooting catalog covers
  `POUNCE_LIMIT_REQUEST_TOO_LARGE`; no runtime or docs change was needed in this
  refresh.
- Confidence: High.

### P0. Tenant Authority Scope Matrix

Status: closed on 2026-05-22 for the server-owned scope contract. Keep the
Chirp-shaped fixture and workload under P1.

- Steward: Protocol, ASGI Bridge, Transport/TLS, Tests/Compatibility
- Area: Chirp/LB Sonic multi-tenant routing
- Invariant: Host, authority, scheme, client, server, and root path must be
  validated and consistent across protocols and proxy-trust states.
- Original Evidence: Steward review found H2/H3 pseudo-header defaulting and no
  tenant-shaped scope matrix. Multi-tenant apps commonly derive tenant identity
  from these fields.
- User Impact Addressed: Tested scope builders now keep trusted and untrusted
  authority inputs from crossing tenant boundaries.
- Required Fix: Enforce required H2/H3 pseudo-headers, define Host versus
  `:authority` behavior, and add trusted/untrusted proxy tests. Current proof
  covers H1/H2/H3/WS scope construction plus H2/H3 authority conflict rejection.
- Required Proof: H1/H2/H3/WS scope matrix plus one Chirp-style tenant fixture
  returning tenant identity from scope. Current proof:
  `tests/unit/test_tenant_scope_matrix.py`, `tests/unit/test_h2_protocol.py`,
  and `tests/unit/test_h3_bridge.py`. The broader Chirp representative fixture
  remains P1.
- Collateral: No docs change was needed in this refresh; deployment/security
  docs remain part of the P1 Railway and Chirp workload items.
- Confidence: Medium-high.

### P0. Lifespan State Protocol Parity

Status: closed on 2026-05-22 for scope-state injection parity. Broader
load/reload lifecycle proof remains under P1.

- Steward: Protocol, ASGI Bridge
- Area: Chirp/LB Sonic app state
- Invariant: `scope["state"]` must be protocol-independent unless explicitly
  documented otherwise.
- Original Evidence: H1 state injection existed; steward review found
  H2/H3/WebSocket scope builders without state parameters.
- User Impact Addressed: Protocol-specific scope builders now accept and inject
  the same lifespan state object.
- Required Fix: Thread worker lifespan state through H2, H3, and WebSocket scope
  construction or document hard exceptions. Current behavior injects state for
  H1, H2, H3, and WebSocket scopes.
- Required Proof: Scope state identity tests for H1, H2, H3, and WS.
  Current proof: `tests/integration/test_lifespan_state_integration.py`,
  `tests/unit/test_h2_bridge.py`, `tests/unit/test_h3_bridge.py`, and
  `tests/unit/test_ws_protocol.py`.
- Collateral: No docs change was needed in this refresh; behavior matches the
  core ASGI contract.
- Confidence: High.

### P1. Bengal Static Fixture And Benchmark

- Steward: Runtime/Public API, Performance Evidence, Tests/Compatibility,
  Examples, Site
- Area: Bengal local dev delight
- Invariant: Bengal performance must be measured on Bengal-shaped output.
- Required Fix: Add a checked-in miniature Bengal output fixture or generator and
  benchmark profile.
- Required Proof: Root/nested indexes, `.well-known`, SVG/ICO/fonts, CSS/JS,
  search index, `.gz`, `.zst`, HEAD, ETag/304, Range, malformed traversal, and
  RSS static soak.
- Initial Targets: Warm p50 <= 2 ms and p99 <= 10 ms for files under 10 KB;
  304 p99 <= 5 ms; RSS growth <= 10 MB over 100k requests.
- Collateral: `benchmarks/README.md`, static docs, examples.
- Confidence: High.

### P1. Chirp/LB Sonic Representative Workload

- Steward: Runtime/Public API, ASGI Bridge, Tests/Compatibility, Performance
  Evidence, Docs/Site
- Area: Multi-tenant HTML-over-the-wire production
- Invariant: Compatibility claims need an app-shaped integration fixture.
- Required Fix: Add a representative workload with host tenants, middleware,
  lifespan state, forms, static assets, streaming HTML or SSE, keep-alive, and
  optional WebSocket route if needed.
- Required Proof: Real-server integration under concurrency, negative proxy
  spoofing case, sustained and burst benchmark profiles, active connection and
  memory reporting.
- Initial Targets: Pounce >= 0.9x Uvicorn throughput on agreed H1 Chirp
  workload; p99 <= 25 ms at target RPS; error rate < 0.1%; SSE first-event p99
  <= 100 ms if SSE is in scope.
- Collateral: Compatibility matrix, production docs, benchmark docs.
- Confidence: High for Chirp, medium for LB Sonic specifics until the fixture is
  provided or modeled.

### P1. Railway Deployment Contract

- Steward: Transport/TLS, Runtime/Public API, Docs/Site, Operator Output
- Area: LB Sonic on Railway
- Invariant: Deployment docs must match platform networking and Pounce security
  assumptions.
- Evidence: Railway public networking docs say apps should listen on
  `0.0.0.0:$PORT` when using Railway-provided public networking. Railway
  healthchecks use the injected `PORT` and switch traffic after a 200 response.
- Required Fix: Add Railway recipe: bind `0.0.0.0`, read `PORT`, use platform TLS
  termination, do not enable Pounce HTTP/3 for Railway public HTTP, configure
  health path, choose JSON logs, and document proxy header trust.
- Required Proof: Example smoke test with injected `PORT`; docs/snippet test;
  trusted/untrusted `X-Forwarded-*` integration test.
- Collateral: Deployment docs, production example, troubleshooting.
- Confidence: High for `PORT` and healthchecks; medium for exact proxy header
  trust until Railway ingress details are confirmed for this deployment.

References checked 2026-05-09:
[Railway Public Networking](https://docs.railway.com/public-networking) and
[Railway Healthchecks](https://docs.railway.com/reference/healthchecks).

### P1. Reload And Drain Under Load

- Steward: Runtime/Public API, Protocol, Tests/Compatibility, Performance
  Evidence
- Area: Chirp/LB Sonic deploy safety
- Invariant: Lifecycle guarantees need signal-path proof under real traffic.
- Required Fix: Align documented signal names with implementation, then test
  real `SIGHUP` and `SIGTERM` subprocess paths under mixed short, slow,
  streaming, and keep-alive requests.
- Required Proof: Zero unexpected drops, bounded 503 behavior during drain, no
  orphan threads/processes, parity across thread/process/subinterpreter or
  documented exceptions, and H3 limitation or support clearly stated.
- Collateral: Deployment lifecycle docs and production example.
- Confidence: High.

### P1. Introspection Contract

- Steward: Transport/TLS, Tests/Compatibility, Docs, Operator Output
- Area: Production diagnostics
- Invariant: Security-sensitive operator endpoints need implementation, ADR, and
  docs to agree.
- Evidence: The ADR describes a separate loopback listener, while implementation
  dispatches the endpoint on the main worker listener and warns for public
  exposure.
- Required Fix: Choose the contract. Either implement a separate loopback
  listener, or revise `introspection_bind` semantics and the ADR to reflect
  same-listener behavior.
- Required Proof: Live endpoint tests for disabled fallthrough, enabled
  intercept, custom path, user-route collision, redaction canaries, and
  public-bind warning.
- Collateral: ADR, config schema, troubleshooting, deployment docs.
- Confidence: High.

### P1. WebSocket Compression Negotiation

- Steward: Protocol
- Area: Chirp real-time paths
- Invariant: Extension negotiation must reflect the client offer.
- Evidence: Steward review found `permessage-deflate` response behavior tied to
  config rather than the request offer.
- Required Fix: Parse `Sec-WebSocket-Extensions` and enable compression only
  after negotiation.
- Required Proof: No-offer, unsupported-offer, accepted-offer, and compressed
  roundtrip tests.
- Collateral: WebSocket compression docs.
- Confidence: High.

### P1. Middleware Contract And Real-Server Tests

- Steward: ASGI Bridge, Tests/Compatibility, Docs/Site
- Area: Chirp auth/CORS/rate-limit hooks
- Invariant: Docs examples must match callable classification exactly.
- Evidence: Steward review found middleware docs that do not match current
  callable signatures and `CORSMiddleware` constructor.
- Required Fix: Update docs to current contract or broaden implementation to
  support the documented signatures. Add real-server middleware tests.
- Required Proof: Pre-request short-circuit, post-response mutation, exception
  handling, ordering with built-ins, streaming behavior, and no cross-request
  scope leakage.
- Collateral: Middleware docs and API reference.
- Confidence: High.

### P2. Benchmark Evidence System

- Steward: Performance Evidence
- Area: Public claims and regression proof
- Invariant: Published numbers must include workload, workers, duration,
  concurrency, platform, Python build, comparison target, variance, and caveats.
- Required Fix: Standardize benchmark profiles and artifact output.
- Required Proof: 5+ runs per profile, median/p95/p99/variance, error rate,
  CPU/RSS, Python `3.14t`, hardware, commands, config, raw JSON.
- Collateral: README, site performance/comparison pages, releases.
- Confidence: High.

### P2. HTTP/3 Current-State Cleanup

- Steward: Protocol, Transport/TLS, Docs/Site
- Area: Protocol positioning
- Invariant: Roadmaps must not contradict implemented protocol support or
  dependency choices.
- Required Fix: Update the HTTP/3 design doc so February aioquic deferral text is
  clearly historical and current zoomies support is described with remaining
  lifecycle/reload gates.
- Required Proof: Updated design doc and feature matrix.
- Collateral: README, roadmap, protocol docs.
- Confidence: High.

### P2. OpenTelemetry Semantic Proof

- Steward: Tests/Compatibility, Runtime/Public API
- Area: Production tracing
- Invariant: Observability claims should verify semantic behavior, not only
  import/no-crash behavior.
- Required Fix: Add optional-extra CI lane or in-memory exporter tests.
- Required Proof: Parent propagation, span naming, status, exception recording,
  response size, and worker-mode coverage.
- Collateral: Observability docs.
- Confidence: Medium.

## Dependencies

- Bengal fixture or generated artifact before meaningful static benchmarks.
- Chirp or LB Sonic representative fixture before production confidence claims.
- Deterministic subprocess/signal harness before reload/drain proof.
- Optional competitor installs and fixed hardware before public benchmarks.
- Railway deployment details rechecked near release because platform behavior can
  change.
- Protocol and ASGI agreement before body-limit, pseudo-header, and state changes.

## Risks

- The largest current risk is treating feature presence as a shipped contract.
- Static config public wiring is closed; Bengal fixture and benchmark proof
  remain before wider Bengal messaging.
- Body limits, host/authority scope behavior, and lifespan state parity have
  code-level proof; production confidence still depends on representative Chirp
  workload and reload/drain proof.
- HTTP/3 is valuable, but lifecycle parity may lag public feature tables.
- Benchmark claims currently conflict across surfaces; publish only reproducible
  numbers.

## Convergence

All consulted stewards converged on the same sequence. The first step is now
closed.

1. Close public-contract gaps. Done for the P0 items in this plan.
2. Build Bengal and Chirp/LB Sonic representative fixtures.
3. Add reproducible benchmarks and load/drain proof.
4. Align public docs, examples, and roadmap claims to the measured behavior.

## Minority Reports

- Transport/TLS view: Railway should be documented as platform TLS plus
  HTTP/HTTP2 public networking; Pounce TLS/H3 should stay prominent for other
  hosts but not leak into Railway examples.
- Protocol view: correctness gates should outrank benchmark and marketing work
  until H2/H3/WS match H1 for malformed input, limits, state, and reload.
- Tests view: static serving is no longer middleware-only; public config wiring
  and proof are closed. Keep fixture breadth and benchmark proof under P1.

## Not Now

- io_uring.
- WebTransport.
- Custom static transforms, image resizing, or tenant-specific static roots.
- Built-in tenant routing, auth, or tenant labels in core metrics.
- New protocol abstractions beyond H3.
- More `pounce init` framework scaffolds.
- HTTP/3 or Rust-throughput performance marketing before reproducible evidence.

## Collateral Checklist

Every accepted finding above needs one of these before it is marked done:

- Code or docs fix.
- Targeted tests or benchmark proof.
- Docs/example/site update when public behavior changes.
- Changelog fragment for user-visible contract changes.
- Explicit no-collateral note with reason.
