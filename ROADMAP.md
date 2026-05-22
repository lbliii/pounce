# Pounce Roadmap

**Current version:** 0.6.0
**Updated:** May 2026
**Active horizon:** May - September 2026

## Current Read

Pounce has moved past the original Phase 5b feature-build plan. Static files,
middleware, lifecycle logging, OpenTelemetry hooks, rich error pages, WebSocket
compression, HTTP/3, config files, schema export, init scaffolding, health,
metrics, and introspection are present in the codebase.

The next phase is not another feature grab. The next phase is proof and contract
hardening for two concrete use cases:

1. **Bengal static-site development:** a local dev server that is fast,
   cache-correct, reload-friendly, and boring to use from a generated site.
2. **Chirp/LB Sonic production:** HTML-over-the-wire apps behind a platform load
   balancer, with multi-tenant host routing, streamed responses, middleware,
   backpressure, observability, and graceful deploy behavior.

The active planning record is
[docs/plans/ironclad-bengal-chirp.md](docs/plans/ironclad-bengal-chirp.md).
Obsolete Phase 5b planning files were pruned after the shipped feature set moved
into contract proof. Vibe-readiness plans remain historical implementation
records.

## Where We Are

| Surface | Current State | Confidence |
|---|---|---|
| HTTP/1.1 | Implemented, including fast sync parser | High |
| HTTP/2 | Implemented; body-limit, tenant-authority, and state parity proof exists | High |
| HTTP/3 | Implemented via zoomies; body-limit, tenant-authority, and state parity proof exists; lifecycle/reload parity still needs proof | Medium |
| WebSocket | Implemented, compression negotiation needs review | Medium |
| Static files | Public config wiring has real-server H1/TOML proof; Bengal fixture and benchmark still missing | High |
| Middleware | Implemented; docs and real-server Chirp integration need proof | Medium |
| Lifespan state | H1/H2/H3/WS scope-state parity has proof; reload/lifecycle breadth still needs proof | High |
| Reload/drain | Implemented; signal-path load tests are the next gate | Medium |
| Observability | Metrics, health, OTel, lifecycle logs, introspection present | Medium |
| Benchmarks | Basic runner exists; canonical workload matrix is missing | Low |

## Priority 0: Closed Contract Gaps

Status: closed on 2026-05-22. These items were the blocking correctness gaps for
claiming Bengal and Chirp/LB Sonic are ironclad. The remaining work has moved to
Priority 1 use-case proof, especially Bengal fixture/benchmarks and Chirp/LB
Sonic representative workload/reload proof.

### 1. Public Static Config Must Serve Files

`ServerConfig.static_files` and TOML static config are documented. Earlier
steward review found the tests mostly wrapped apps manually with `StaticFiles` or
`create_static_handler`, so the public server path needed either wiring or
demotion.

Status: closed on 2026-05-22. `Server._apply_static_files()` now wraps the app
from `ServerConfig.static_files`, and `tests/integration/test_static_config.py`
proves the real-server `ServerConfig` path, TOML `[static_files]` loading, mixed
fallback behavior, cache-control propagation, and HTTP/1 content-length behavior.
Bengal-shaped fixture and benchmark work remains in Priority 1.

Required proof:

- Real-worker test using only `ServerConfig(static_files={"/": tmpdir})`. Done:
  `tests/integration/test_static_config.py`.
- TOML `[static_files]` test through public config loading. Done:
  `tests/integration/test_static_config.py`.
- Bengal-shaped fixture: root index, nested indexes, CSS/JS, SVG/ICO, fonts,
  search index, `.well-known`, `.gz`, `.zst`, and missing-file behavior. Moved
  to Priority 1 Bengal fixture and benchmark proof.
- Static-only Bengal mode and mixed Chirp app-plus-assets mode documented. Mixed
  app/static fallback is tested; broader Bengal docs remain Priority 1.

### 2. Protocol Limits Must Fail Closed

Oversized request bodies must not be truncated and delivered to ASGI as if they
were valid. H1, H2, and H3 should reject or reset deterministically with
operator-visible `POUNCE_LIMIT_*` diagnostics.

Status: closed on 2026-05-22. H1 integration tests prove content-length and
chunked oversized requests return 413 with `POUNCE_LIMIT_REQUEST_TOO_LARGE` and
do not call the app. H2 handler tests prove content-length and streaming DATA
over-limit requests return 413 before app body delivery. H3 handler tests prove
content-length and DATA over-limit requests return 413 and cancel/clean up the
stream before body delivery.

Required proof:

- H1 content-length and chunked tests. Done: `tests/integration/test_limits.py`.
- H2 DATA and H3 DATA over-limit tests. Done:
  `tests/unit/test_h2_handler.py` and `tests/unit/test_h3_handler.py`.
- App-not-called or explicit disconnect/reset assertions.
- Troubleshooting and limits documentation aligned with behavior. No docs change:
  existing diagnostics and error-code catalog already cover
  `POUNCE_LIMIT_REQUEST_TOO_LARGE`.

### 3. Tenant Authority Must Be Validated Across Protocols

LB Sonic will likely derive tenant identity from host, authority, scheme, and
trusted proxy headers. Pounce cannot default malformed H2/H3 pseudo-headers into
valid-looking `GET /` requests or allow host/authority conflicts to cross tenant
boundaries.

Status: closed on 2026-05-22 for the scope-contract gate. The tenant scope
matrix covers H1/H2/H3/WS trusted and untrusted proxy authority behavior,
`root_path`, scheme, client, server, and host header outcomes. H2/H3
host-authority conflict tests prove malformed authority is rejected/reset before
tenant scope construction.

Required proof:

- H1/H2/H3/WS scope matrix for `Host`, `:authority`, `X-Forwarded-Host`,
  `X-Forwarded-Proto`, trusted and untrusted proxy cases, and `root_path`. Done:
  `tests/unit/test_tenant_scope_matrix.py`.
- Malformed H2/H3 pseudo-header rejection tests. Done:
  `tests/unit/test_h2_protocol.py` and `tests/unit/test_h3_bridge.py`.
- Chirp-style tenant fixture returning the resolved tenant from ASGI scope. The
  generic scope matrix now covers the server-owned tenant inputs; the broader
  Chirp representative fixture remains Priority 1.

### 4. Lifespan State Parity

Chirp production apps need startup-created state for pools, caches, tenant
registries, and shared services. H1 must not be the only path that sees
`scope["state"]`.

Status: closed on 2026-05-22 for scope injection parity. H1 integration tests
prove request scopes receive worker lifespan state, and H2/H3/WebSocket unit
tests prove each protocol-specific scope builder injects the same state object.

Required proof:

- Scope state tests for H1, H2, H3, and WebSocket. Done:
  `tests/integration/test_lifespan_state_integration.py`,
  `tests/unit/test_h2_bridge.py`, `tests/unit/test_h3_bridge.py`, and
  `tests/unit/test_ws_protocol.py`.
- Integration tests proving documented per-worker state behavior. Done for H1
  worker state; optional-protocol runtime breadth remains covered by the scope
  builder tests and by Priority 1 workload proof.
- ASGI bridge docs updated with parity or explicit exceptions. No docs change:
  current behavior matches the core ASGI contract.

## Priority 1: Use-Case Proof

### Bengal Ironclad Local Dev

Goal: `bengal build` output can be previewed with Pounce without guessing,
without Nginx, and without surprising browser behavior.

Work:

- Add a Bengal-like fixture or generated output artifact.
- Add local-dev static benchmark profile: warm small files, nested indexes,
  304s, range requests, precompressed assets, and cold first-hit numbers.
- Validate reload includes for `.md`, `.html`, `.css`, `.js`, templates, and
  static assets.
- Document the exact `pounce.toml`, `pounce check`, and run command.

Initial target envelope:

- Warm p50 <= 2 ms and p99 <= 10 ms for files under 10 KB on a local dev
  machine.
- 304 p99 <= 5 ms.
- No RSS growth above 10 MB over a 100k-request static soak.

### Chirp/LB Sonic Production

Goal: confidently deploy a multi-tenant HTML-over-the-wire forum stack on
Pounce behind a managed platform load balancer.

Work:

- Add a representative Chirp/LB Sonic fixture: host-based tenants, middleware,
  lifespan state, forms, streamed HTML or SSE, static assets, and optional
  WebSocket route if the product needs it.
- Add Railway-style deployment recipe: bind `0.0.0.0:$PORT`, rely on platform
  TLS termination, configure health path, use JSON logs, and set proxy trust
  deliberately.
- Add load profiles for sustained browsing, burst posting, streamed updates,
  queue saturation, rate limiting, and SIGTERM drain.
- Add operator workflow: health for deploy readiness, metrics for dashboards,
  introspection for live debugging, lifecycle logs for deploy analysis.

Initial target envelope:

- Pounce >= 0.9x Uvicorn throughput on agreed H1 Chirp workload.
- p99 <= 25 ms at the agreed target request rate.
- Error rate < 0.1% under sustained load.
- SSE first-event p99 <= 100 ms, if SSE is in scope.
- No unexpected drops during reload/drain tests.

## Priority 2: Evidence Tooling

The benchmark system needs to produce attachable evidence instead of one-off
numbers.

Work:

- Add profiles: `local-dev`, `bengal-static`, `chirp-prod`, `streaming`,
  `reload`, and `worker-modes`.
- Add repeat counts, variance, raw JSON artifacts, CPU/RSS capture, Python build
  metadata, GIL/free-threaded status, and competitor versions.
- Separate product claims from local observations. README and site numbers must
  cite the exact command, environment, sample count, and caveats.

## Priority 3: Documentation Alignment

Work:

- Prune obsolete Phase 5b implementation plans so they cannot be mistaken for
  the active queue.
- Collapse the HTTP/3 design doc into current zoomies state, remaining parity
  gaps, and release confidence gates.
- Align worker-mode docs with `ServerConfig`, CLI, schema, README, and tests.
- Fix middleware examples or implementation so docs match callable signatures.
- Clarify introspection exposure: same listener with warning versus separate
  loopback listener. The ADR, config fields, docs, and tests must agree.

## Not Now

These should not outrank the contract and use-case gates above:

- io_uring backend.
- WebTransport.
- Custom static transforms or image resizing.
- More framework scaffolds for `pounce init`.
- HTTP/3 performance marketing.
- Rust-throughput parity as a product claim.
- Tenant-aware routing or auth inside Pounce core.

## Historical Notes

Phase 5b established the core production feature set. The detailed Phase 5b
implementation-plan files have been removed to keep the active queue clear.
Current work is governed by the ironclad Bengal/Chirp plan and by steward
findings attached there.
