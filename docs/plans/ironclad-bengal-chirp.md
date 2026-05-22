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

The repo is feature-rich enough to support Bengal and Chirp, and several
original proof gaps have now closed. The next work should keep the current proof
map accurate, deepen load/reload evidence, and avoid promoting benchmark or H3
claims beyond the ledgers.

Original top convergence, now partly closed:

- `ServerConfig.static_files` is public and documented, but the real server path
  needs proof or wiring.
- Chirp/LB Sonic needs multi-tenant scope correctness across host, authority,
  proxy, root path, lifespan state, and protocol variants.
- Benchmarks must become reproducible workload evidence, not scattered claims.
- Railway-style deployment needs explicit `0.0.0.0:$PORT`, platform TLS
  termination, healthcheck, proxy, and observability guidance.
- Old Phase 5b plans should be treated as historical records, not active work.

## Implementation Update — 2026-05-22

Current `main` has closed several original contract gaps. Treat the ranked
backlog below as historical unless an item is explicitly marked open, and use
this update plus the proof ledgers for current status.

Completed or covered by current proof:

- Static config reaches the real server path:
  `tests/integration/test_static_config.py`.
- Request body limits fail closed across H1, H2, and H3:
  `tests/integration/test_limits.py`, `tests/unit/test_h2_handler.py`, and
  `tests/unit/test_h3_handler.py`.
- Tenant-facing authority/proxy scope behavior has a cross-protocol matrix:
  `tests/unit/test_tenant_scope_matrix.py`.
- Lifespan state reaches H1, H2, H3, and WebSocket scope builders, including
  H3 handler dispatch proof: `tests/unit/test_h3_handler.py`,
  `tests/unit/test_h2_bridge.py`, `tests/unit/test_h3_bridge.py`, and
  `tests/unit/test_ws_protocol.py`.
- Bengal and Chirp/LB Sonic-shaped workloads exist under `benchmarks/apps/`
  and `benchmarks/test_*.py`; the standalone runner drives each workload's
  configured path.
- Optional protocol missing-extra diagnostics are covered by
  `tests/unit/test_optional_protocol_diagnostics.py`.

Remaining high-value gaps:

- H3 reload/drain parity under load.
- Repeated benchmark sample orchestration and variance aggregation. The runner
  can emit artifact-schema-compatible metadata, but current artifacts still
  record a single runner sample unless callers repeat runs.
- Public release/readme/site claim updates should continue to cite current
  ledgers and avoid promoting benchmark snapshots.

## Steward Synthesis — 2026-05-22

Asked stewards: Design/Troubleshooting, Runtime/Public API, Protocol/ASGI, and
Performance Evidence.

Accepted findings:

- Closed P0s were still listed as active: static config, request limits, tenant
  scope matrix, and lifespan state parity. Mark them closed and keep proof links
  visible.
- Bengal and Chirp workloads exist. The remaining benchmark gap is repeated
  samples, variance, RSS/CPU capture, and release-quality artifacts, not fixture
  absence.
- Introspection same-listener behavior is no longer an unresolved ADR conflict;
  `docs/design/introspection-auth.md` records the accepted contract and
  `tests/unit/test_introspect.py` covers warning behavior.
- HTTP/3 current truth is optional-limited zoomies support. H3 lifespan-state
  handoff is proven; H3 reload/drain and benchmark artifacts remain open.
- Public docs should narrow zero-downtime, immutable-config, H3 parity, and
  optional-extra wording to match `core-contract.md` and
  `protocol-proof-ledger.json`.

## Contract Parity Matrix

| Contract | API/CLI | Programmatic | Protocol | Schema/Types | Docs | Examples | Tests | Benchmarks |
|---|---|---|---|---|---|---|---|---|
| Static files from config | TOML/schema exists | `ServerConfig.static_files` wraps the real app | H1 real-worker proof exists | Field exists | Claims exist | Static examples exist | `tests/integration/test_static_config.py` | Bengal workload exists |
| Bengal local dev | Config recipe exists | Works through configured static handler | H1 static path covered | Static fields exist | Static docs exist | Synthetic fixture exists | Static fixture coverage | Bengal profile and artifact output exist; repeated variance pending |
| Chirp/LB Sonic production | Railway recipe exists | Chirp forum fixture exists | H1/H2/H3/WS scope proof improved; H3 reload/drain remains | Config fields exist | Deployment docs exist | Forum-shaped workload exists | Tenant, limit, state, and smoke tests exist | Chirp profile and artifact output exist; repeated variance pending |
| Lifespan state | Public ASGI behavior | H1 path covered | H2/H3/WS scope proof exists | State behavior implicit | Needs parity note cleanup | Lifespan examples exist | Cross-protocol state tests exist | Not benchmark-sensitive |
| Reload/drain | SIGHUP implementation documented | Server API exists | H3 reload/drain proof still pending | `reload_timeout` exists | Claims need measured proof | Production example generic | Signal/load-bearing proof still pending | Missing reload profile |
| Introspection | Config fields exist | Response builder exists | Same-listener behavior accepted with warning contract | Allowlist exists | ADR updated | None | Unit tests cover warning/redaction | Not relevant |
| Middleware | Programmatic only | Stack exists | ASGI semantics matter | Callable fields not TOML-friendly | Examples mostly aligned | Basic example | Real-server tests exist | Chirp workload covers middleware header |

## Historical Ranked Backlog

The original backlog is kept for traceability. Items marked **Closed** should not
be treated as active work unless a later regression reopens them.

### P0. Static Config Contract — Closed

- Steward: Runtime/Public API, Protocol, ASGI Bridge, Transport/TLS,
  Tests/Compatibility
- Area: Bengal static-site dev server
- Invariant: Documented `ServerConfig` and TOML behavior must reach the public
  request path.
- Evidence: `Server._apply_static_files()` wraps configured static mounts into
  the public request path.
- Current Proof: `tests/integration/test_static_config.py` covers real-worker
  config-only serving, TOML static config, mixed fallback, cache headers, and
  sendfile/h11 accounting.
- Collateral: Static docs, config docs, examples, changelog, `ROADMAP.md`.
- Confidence: High.

### P0. Request Body Limit Failure — Closed

- Steward: Protocol
- Area: Chirp/LB Sonic production request bodies
- Invariant: Pounce must reject oversized input before ASGI sees partial data.
- Current Proof: H1 content-length and chunked limits are covered by
  `tests/integration/test_limits.py`; H2 and H3 oversized DATA behavior is
  covered by `tests/unit/test_h2_handler.py` and
  `tests/unit/test_h3_handler.py`.
- Collateral: Limits docs and troubleshooting catalog.
- Confidence: High.

### P0. Tenant Authority Scope Matrix — Closed

- Steward: Protocol, ASGI Bridge, Transport/TLS, Tests/Compatibility
- Area: Chirp/LB Sonic multi-tenant routing
- Invariant: Host, authority, scheme, client, server, and root path must be
  validated and consistent across protocols and proxy-trust states.
- Current Proof: `tests/unit/test_tenant_scope_matrix.py` covers trusted and
  untrusted forwarded authority across H1, H2, H3, and WebSocket. H2/H3
  pseudo-header rejection tests cover malformed authority behavior.
- Collateral: Deployment/security docs and protocol troubleshooting entries.
- Confidence: Medium-high.

### P0. Lifespan State Protocol Parity — Closed

- Steward: Protocol, ASGI Bridge
- Area: Chirp/LB Sonic app state
- Invariant: `scope["state"]` must be protocol-independent unless explicitly
  documented otherwise.
- Current Proof: H1 lifespan state is covered by integration tests; H2/H3/WS
  scope builders have state identity tests; H3 handler dispatch passes lifespan
  state into the ASGI scope.
- Collateral: ASGI docs and compatibility matrix.
- Confidence: High.

### P1. Bengal Static Fixture And Benchmark

- Steward: Runtime/Public API, Performance Evidence, Tests/Compatibility,
  Examples, Site
- Area: Bengal local dev delight
- Invariant: Bengal performance must be measured on Bengal-shaped output.
- Required Fix: Maintain and expand the checked-in Bengal output fixture and
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
- Required Fix: Maintain and expand the representative workload with host tenants, middleware,
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

### P1. Introspection Contract — Closed For ADR Decision

- Steward: Transport/TLS, Tests/Compatibility, Docs, Operator Output
- Area: Production diagnostics
- Invariant: Security-sensitive operator endpoints need implementation, ADR, and
  docs to agree.
- Evidence: `docs/design/introspection-auth.md` accepts same-listener
  interception with loopback/default warning behavior, and
  `tests/unit/test_introspect.py` covers warning/redaction behavior.
- Remaining Fix: Keep site/deployment docs aligned with the accepted
  same-listener contract.
- Required Proof: Existing introspection tests plus docs parity.
- Collateral: Deployment docs if wording drifts.
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
- Required Fix: Standardize benchmark profiles and repeated artifact runs.
- Required Proof: 5+ runs per profile, median/p95/p99/variance, error rate,
  CPU/RSS, Python `3.14t`, hardware, commands, config, raw JSON. The runner can
  emit artifact-schema-compatible metadata; repeat orchestration remains open.
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

- Repeated Bengal static samples before public static performance claims.
- Repeated Chirp/LB Sonic samples before production confidence claims.
- Deterministic subprocess/signal harness before reload/drain proof.
- Optional competitor installs and fixed hardware before public benchmarks.
- Railway deployment details rechecked near release because platform behavior can
  change.
- Protocol and ASGI agreement before body-limit, pseudo-header, and state changes.

## Risks

- The largest current risk is treating feature presence as a shipped contract.
- Static config, request limits, and host/authority behavior now have proof;
  keep ledgers current so those closed risks do not reopen silently.
- HTTP/3 is valuable, but lifecycle parity may lag public feature tables.
- Benchmark claims currently conflict across surfaces; publish only reproducible
  numbers.

## Convergence

All consulted stewards converged on the same sequence:

1. Keep public-contract proof current.
2. Expand Bengal and Chirp/LB Sonic representative fixtures only where new
   workflows require it.
3. Add repeated reproducible benchmarks and load/drain proof.
4. Align public docs, examples, and roadmap claims to the measured behavior.

## Minority Reports

- Transport/TLS view: Railway should be documented as platform TLS plus
  HTTP/HTTP2 public networking; Pounce TLS/H3 should stay prominent for other
  hosts but not leak into Railway examples.
- Protocol view: correctness gates should outrank benchmark and marketing work
  until H2/H3/WS match H1 for malformed input, limits, state, and reload.
- Tests view: if static serving is intentionally middleware-only, the P0 changes
  from implementation to docs/config cleanup, but it remains P0 because the
  public contract is inconsistent.

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
