# Pounce Roadmap

**Current version:** 0.7.1
**Updated:** June 2026
**Active horizon:** June - September 2026

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
Older Phase 5b and vibe-readiness plans are historical implementation records.

## Current State Update — May 22, 2026

Several original Ironclad contract gaps are now covered by tests and ledgers.
Static config reaches the real server path, H1/H2/H3 request limits fail closed,
tenant-facing scope behavior has a cross-protocol matrix, lifespan state reaches
H1/H2/H3/WebSocket scope builders, and Bengal/Chirp-shaped workloads exist.

The remaining near-term work is narrower:

- H3 reload/drain parity under load.
- Signal-path reload/drain proof for production-shaped traffic.
- Repeated benchmark samples and variance aggregation around the new artifact
  metadata output.
- Public docs and release wording kept aligned to proof ledgers.

## Where We Are

| Surface | Current State | Confidence |
|---|---|---|
| HTTP/1.1 | Implemented, including fast sync parser | High |
| HTTP/2 | Implemented with scope, limit, and missing-extra proof; operator output parity remains | Medium-high |
| HTTP/3 | Implemented via zoomies with limit and lifespan-state proof; reload/drain and benchmark proof remain | Medium |
| WebSocket | Implemented with compression negotiation and missing-extra proof; H2 WebSocket remains optional-limited | Medium-high |
| Static files | Public config and TOML reach the real server path | High |
| Middleware | Implemented with real-server tests and Chirp workload coverage | Medium-high |
| Lifespan state | H1, H2, H3, and WebSocket scope paths covered | High |
| Reload/drain | Implemented; signal-path load tests are the next gate | Medium |
| Observability | Metrics, health, OTel, lifecycle logs, introspection present | Medium |
| Benchmarks | Bengal/Chirp workloads, repeated variance summaries, RSS samples, endpoint profiles, and local snapshot artifacts exist | Medium |

## Priority 0: Closed Contract Proof

These items were original blockers for Bengal and Chirp/LB Sonic confidence.
They now have current proof; keep them here as closed gates and use the active
plan plus ledgers for exact test references.

### 1. Public Static Config Must Serve Files

**Status:** Closed. Covered by real-server `ServerConfig.static_files` and TOML
tests in `tests/integration/test_static_config.py`.

`ServerConfig.static_files` and TOML static config now reach the public server
dispatch path through `Server._apply_static_files()`. Keep this gate closed
unless a later regression breaks real-server static serving.

Current proof:

- Real-worker test using only `ServerConfig(static_files={"/": tmpdir})`.
- TOML `[static_files]` test through public config loading.
- Bengal-shaped fixture: root index, nested indexes, CSS/JS, SVG/ICO, fonts,
  search index, `.well-known`, `.gz`, `.zst`, and missing-file behavior.
- Static-only and mixed app-plus-assets behavior covered by integration tests
  and public static configuration docs.

### 2. Protocol Limits Must Fail Closed

**Status:** Closed for H1/H2/H3 request-body limits. Covered by
`tests/integration/test_limits.py`, `tests/unit/test_h2_handler.py`, and
`tests/unit/test_h3_handler.py`.

Oversized request bodies must not be truncated and delivered to ASGI as if they
were valid. H1, H2, and H3 should reject or reset deterministically with
operator-visible `POUNCE_LIMIT_*` diagnostics.

Required proof:

- H1 content-length and chunked tests.
- H2 DATA and H3 DATA over-limit tests.
- App-not-called or explicit disconnect/reset assertions.
- Troubleshooting and limits documentation aligned with behavior.

### 3. Tenant Authority Must Be Validated Across Protocols

**Status:** Closed for the current H1/H2/H3/WebSocket scope matrix. Covered by
`tests/unit/test_tenant_scope_matrix.py` and protocol pseudo-header tests.

LB Sonic will likely derive tenant identity from host, authority, scheme, and
trusted proxy headers. Pounce cannot default malformed H2/H3 pseudo-headers into
valid-looking `GET /` requests or allow host/authority conflicts to cross tenant
boundaries.

Required proof:

- H1/H2/H3/WS scope matrix for `Host`, `:authority`, `X-Forwarded-Host`,
  `X-Forwarded-Proto`, trusted and untrusted proxy cases, and `root_path`.
- Malformed H2/H3 pseudo-header rejection tests.
- Chirp-style tenant fixture returning the resolved tenant from ASGI scope.

### 4. Lifespan State Parity

**Status:** Closed for H1/H2/H3/WebSocket scope construction and H3 handler
handoff. Covered by bridge, WebSocket, and H3 handler tests.

Chirp production apps need startup-created state for pools, caches, tenant
registries, and shared services. H1 must not be the only path that sees
`scope["state"]`.

Required proof:

- Scope state tests for H1, H2, H3, and WebSocket.
- Integration tests proving documented per-worker state behavior.
- ASGI bridge docs updated with parity or explicit exceptions.

## Priority 1: Use-Case Proof

### Bengal Ironclad Local Dev

Goal: `bengal build` output can be previewed with Pounce without guessing,
without Nginx, and without surprising browser behavior.

Work:

- Maintain and expand the Bengal-like fixture or generated output artifact.
- Extend the local-dev static benchmark profile: warm small files, nested indexes,
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

- Maintain and expand the representative Chirp/LB Sonic fixture: host-based tenants, middleware,
  lifespan state, forms, streamed HTML or SSE, static assets, and optional
  WebSocket route if the product needs it.
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
- Add repeat counts, variance aggregation, CPU/RSS capture, and competitor
  versions around the artifact metadata output.
- Separate product claims from local observations. README and site numbers must
  cite the exact command, environment, sample count, and caveats.

## Priority 3: Documentation Alignment

Work:

- Mark Phase 5b documents as historical implementation records.
- Collapse the HTTP/3 design doc into current zoomies state, remaining parity
  gaps, and release confidence gates.
- Align worker-mode docs with `ServerConfig`, CLI, schema, README, and tests.
- Fix middleware examples or implementation so docs match callable signatures.

## Shipped Since 0.6.0

Items that have landed in released versions. See `CHANGELOG.md` for full entries.

- **Railway deployment recipe.** Railway deployment guidance for platform TLS,
  `$PORT` binding, health checks, proxy trust, and drain-window alignment shipped
  in 0.7.0 (CHANGELOG 0.7.0; `site/content/docs/deployment/railway.md`).
- **Introspection same-listener clarification.** The `/_pounce/info` exposure
  model — same listener with allowlist redaction and public-bind warning — was
  reconciled across the ADR, config fields, docs, and tests in 0.7.0
  (CHANGELOG 0.7.0; `docs/design/introspection-auth.md`).

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

Phase 5b established the core production feature set. The older plan files remain
useful as implementation history, but current work is governed by the ironclad
Bengal/Chirp plan and by steward findings attached there.
