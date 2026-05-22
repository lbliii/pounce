# Agent Constitution

## North Star

We make free-threaded Python worth deploying: Pounce is a pure-Python ASGI
server for Python 3.14t that protects standard ASGI apps from untrusted network
input, unstable worker lifecycle events, and opaque operator failures.

We keep the core boring where users need guarantees and explicit where optional
features are still limited. Public claims must follow the proof in
[docs/design/core-contract.md](docs/design/core-contract.md), not the optimism of
a roadmap or a benchmark snapshot.

## Non-Negotiables

- Keep the runtime pure Python. `pyproject.toml` runtime dependencies are `h11`
  and `milo-cli`; optional protocol dependencies stay extras.
- Treat `ServerConfig` as frozen shared state. It is a frozen, slotted,
  keyword-only dataclass in `src/pounce/config.py`.
- Keep protocol handlers and state machines sans-I/O unless a scoped steward
  records why the boundary moved.
- No silent `except`, new `type: ignore`, new S110 suppressions, or vague
  diagnostics without a proof note.
- Sync worker and `_fast_h1.py` are latency-critical. Benchmark before/after if
  parser, sync-worker, scheduler, request-pipeline, or framing hot paths move.
- Validate at public boundaries: CLI, TOML, `ServerConfig`, protocol bytes, ASGI
  app messages, TLS files, and operator endpoints.
- Every `PounceError` raise site must carry a literal `POUNCE_*` code and the
  troubleshooting catalog must cover it.
- Public behavior changes move with collateral: docs, examples, tests, schema,
  redaction, changelog, and benchmark proof when relevant.

## Architecture Boundaries

| Path | Steward / Contract |
| --- | --- |
| `src/pounce/` | Runtime and public API: `pounce.run`, `ServerConfig`, CLI, workers, lifecycle, observability, exports. |
| `src/pounce/protocols/`, `_fast_h1.py`, `_h2_handler.py`, `_h3_handler.py`, `_ws_handler.py` | Protocol: wire bytes become typed protocol events and serialized bytes. |
| `src/pounce/asgi/` | ASGI bridge: scopes, receive/send, streaming, disconnects, lifespan state, framework compatibility. |
| `src/pounce/net/`, `h3_worker.py` | Transport and TLS: TCP, UDS, UDP/H3 sockets, TLS, ALPN, cleanup. |
| `src/pounce/templates/`, `_output.py`, `display.py`, `_state.py` | Operator output: kida templates, banners, help, logs, errors, lifecycle rendering. |
| `tests/` | Evidence: unit, integration, framework, fuzz, fixture, and regression proof. |
| `docs/` | Design and troubleshooting: ADRs, core contract, proof ledgers, error catalog, plans. |
| `site/` | Public docs site: generated docs, navigation, release pages, public wording. |
| `examples/` | Runnable examples and clearly labeled prototypes. |
| `benchmarks/` | Performance evidence: workloads, runners, artifact policy, public numeric proof. |
| `.github/`, `pyproject.toml`, `Makefile`, `changelog.d/` | CI, packaging, release, changelog, and dependency automation. |
| `plan/`, `ROADMAP.md`, `docs/design/*roadmap*.md` | Planning records, implemented-plan links, roadmap-adjacent material, and historical roadmap context. |

## Governance Alignment

- This repository has no `CODEOWNERS`, `OWNERS`, or `MAINTAINERS` file. Human
  approval is single-maintainer unless a future ownership file says otherwise.
- Stewards advise; the repository maintainer approves.
- Canonical product and contract knowledge lives in `docs/design/core-contract.md`.
- Error-code policy lives in `docs/design/error-codes.md`; operator guidance
  lives in `docs/troubleshooting.md`.
- Protocol proof lives in `docs/design/protocol-proof-ledger.json`.
- Public claim proof lives in `docs/design/public-claims.json`.
- Release truth spans `pyproject.toml`, `CHANGELOG.md`, `changelog.d/`,
  `site/content/releases/`, `.github/workflows/`, and `Makefile`.

## Stop And Ask

- Public API, CLI, `ServerConfig`, config-file schema, pytest plugin, package
  exports, or documented behavior changes.
- New runtime dependency, optional protocol dependency, packaging metadata,
  build backend, release workflow, or publishing flow.
- Worker model, GIL detection, subinterpreter behavior, lifecycle state machine,
  reload, drain, or shutdown semantics.
- Sync-worker hot path, `_fast_h1.py`, parser safety behavior, response framing,
  sendfile/framing ownership, or performance targets.
- Security, TLS, proxy trust, public bind behavior, introspection exposure,
  redaction, secrets, request smuggling, or operator diagnostics.
- Data model or compatibility contract changes for metrics, lifecycle events,
  logs, error codes, config schema, protocol events, or ASGI extension messages.
- Test/code disagreement, unreproduced bugs, suspected dead code, benchmark
  gaps, or adjacent issues found mid-task.
- Any change where docs or examples would need to say "stable", "full support",
  "zero downtime", "production ready", or publish a number.

## Anti-Patterns

- Adding native speedups for the hot path; use pure-Python improvements with
  evidence.
- Async-ifying the sync worker because it looks cleaner.
- Adding config for future flexibility before a user-facing need exists.
- Hiding parser failures behind generic 400s when a `POUNCE_*` code and hint can
  guide the operator.
- Documenting optional protocols as core, or limited paths as full parity.
- Publishing benchmark numbers without workload, command, platform, Python build,
  comparison target, and caveats.
- Folding unrelated refactors into bug fixes; flag adjacent issues separately.
- Treating examples, plans, or release notes as proof when tests or ledgers
  disagree.
- Inventing abstractions for hypothetical protocols.

## Steward System

Read this root file plus the closest scoped `AGENTS.md` before changing code or
docs. Root carries cross-cutting rules; scoped files carry local invariants,
review hooks, tests, docs, examples, and refusal patterns.

Each steward uses this operating model:

- Point Of View: who you represent when working in that scope.
- Protect: invariants and failure modes backed by source, tests, docs, or
  release history.
- Contract Checklist: concrete files and surfaces to inspect when the domain
  changes.
- Advocate: specific near-term investments that would strengthen the domain.
- Own: code, tests, docs, agent artifacts, and governance source.
- Optional Do Not and Serve Peers only when they add non-obvious local guidance.

Cross-boundary PRs include `Steward Notes` naming consulted stewards, accepted
findings, deferred findings, proof, collateral, risks, and follow-ups.

### Contract Checklist

For any cross-surface change, identify every surface that should agree:

- API and exports: `src/pounce/__init__.py`, `pounce.run`, `py.typed`, pytest
  plugin, public classes, public helpers.
- CLI and config: `src/pounce/_cli.py`, `src/pounce/config.py`,
  `src/pounce/_config_file.py`, `src/pounce/_config_schema.py`, CLI help,
  TOML template, redaction allowlist.
- Runtime and protocol: workers, supervisor, lifecycle, parser events, response
  framing, socket ownership, protocol proof ledger.
- Operator surfaces: logs, templates, help, check/info output, metrics, health,
  introspection, troubleshooting anchors.
- Public collateral: README, site docs, examples, release notes, changelog
  fragments, migration notes.
- Evidence: unit tests, integration tests, framework compatibility, fuzz tests,
  benchmark artifacts or no-impact notes.

Every accepted finding must name required proof and collateral updates, or say
`no collateral: <reason>`.

### Steward Signal Format

Steward findings should be contract-oriented, evidence-backed, and
collateral-aware.

```text
Steward:
Area:
Severity: P0/P1/P2/P3
Invariant:
Evidence:
User Impact:
Required Fix:
Required Proof:
Collateral:
Confidence:
Verification Status: machine-verified / manual-confirmation-needed / not-machine-verifiable
```

### Convergence Rule

Two or more independent stewards flagging the same finding promotes that finding
to P0 for synthesis. The implementing agent may still defer it, but the deferral
must name the risk and owner.

### Steward Swarms

Trigger phrases: `ask stewards`, `bugbash`, `review swarm`, or
`steward synthesis`.

- For implementation review, consult affected scoped stewards and synthesize
  accepted/deferred findings, merged duplicates, minority reports, required
  proof, collateral, and not-now items.
- For content audit, trigger on `audit docs`, `content audit`, or
  `accuracy pass`; include source-to-doc evidence and public-claim risk.
- For backlog, roadmap, or prioritization, consult all scoped stewards and
  return raw steward signals, confidence, dependencies, risks, convergence,
  minority reports, ranked backlog, and not-now items.
- Stewards advise and create useful tension; the implementing agent owns
  integrated decisions and final proof.

### Global Sweep On Accepted P0s

Before closing any accepted P0, grep the relevant code, docs, examples, tests,
site pages, release notes, and ledgers for the same wrong claim or bug shape.
Record the command or state why the sweep is not machine-verifiable.

## Public Contract And Claims

Use [docs/design/core-contract.md](docs/design/core-contract.md) before adding or
expanding public features. Classify the feature as core, optional protocol,
helper, developer tooling, or external integration before implementation.

Public wording must match proof:

- Core claims need tests that exercise the owned behavior.
- Optional protocol claims need installed-extra and missing-extra proof.
- Helper claims must remain removable when disabled.
- Numeric performance claims need benchmark artifacts or an explicit snapshot
  caveat.
- Roadmap and prototype language must not imply shipped behavior.

## Performance

This concern activates for `_fast_h1.py`, `sync_worker.py`, protocol parsers,
response framing, sendfile, compression, request queues, worker scheduling,
accept distribution, ASGI bridge write paths, and benchmark docs.

Required evidence:

- Before/after benchmark or explicit no-impact rationale for hot-path changes.
- Workload, command, Python build/GIL mode, OS/hardware, workers, duration,
  concurrency, load tool, comparison target, sample count, variance, and raw
  output for public numbers.
- Public docs cite benchmark artifacts or label numbers as snapshots.

## Security And Exposure

This concern activates for parser safety, TLS, proxy headers, CRLF injection,
request IDs, debug pages, introspection, metrics, Sentry, OpenTelemetry, static
files, middleware, UDS permissions, public bind warnings, and redaction.

Required evidence:

- Malformed-input tests for parser/security changes.
- Redaction allowlist tests for config/introspection changes.
- Troubleshooting entries for new `POUNCE_*` diagnostics.
- Explicit production/default posture for debug, introspection, metrics, TLS,
  public binds, and examples.

## Free-Threaded Concurrency

This concern activates for shared app state, shared config, lifecycle collectors,
connection counters, worker state, queues, rate limiters, reload generations,
subinterpreters, and process/thread mode selection.

Required evidence:

- Identify shared mutable state and the lock, ownership rule, or immutability
  guarantee.
- Note Python 3.14t implications for thread workers.
- Exercise shutdown, reload, drain, crash, timeout, and subinterpreter paths when
  lifecycle behavior moves.

## Operator Diagnostics

This concern activates for CLI output, templates, logs, health/info/check,
metrics, error pages, troubleshooting docs, and release notes.

Required evidence:

- Messages tell the operator what to do next.
- Pretty, JSON, plain, quiet, and non-TTY modes stay aligned when relevant.
- `POUNCE_*` codes and docs anchors survive formatting changes.
- Error paths do not crash because optional template fields are absent.

## Known Regression Patterns

- **Fabricated CLI / config fields.** Verification: every flag traces to
  `src/pounce/_cli.py`; every config field traces to `ServerConfig`,
  `_config_file.py`, `_config_schema.py`, docs, and tests.
- **Unverified finding regression.** Verification: every factual P0/P1 carries
  `machine-verified`, `manual-confirmation-needed`, or
  `not-machine-verifiable`.
- **Narrow-fix regression.** Verification: every accepted P0 closure runs the
  Global Sweep above.
- **Public-claim drift.** Shape: docs say "full support", "zero downtime",
  "production ready", "always", or publish numbers beyond proof. Verification:
  check `docs/design/public-claims.json`,
  `docs/design/protocol-proof-ledger.json`, README, site docs, and release notes.
- **Silent exception regression.** Shape: broad `except` or
  `contextlib.suppress(Exception)` hides failures. Verification: `make lint` and
  `scripts/check_silent_exceptions.sh src/pounce`.
- **Python 2 exception syntax regression.** Shape: `except A, B:` catches only
  `A` and binds it to `B`. Verification: `tests/unit/test_code_quality.py`.
- **Error-code catalog drift.** Shape: new `PounceError` raise lacks a literal
  code or docs anchor. Verification: `tests/unit/test_error_codes.py` and
  `tests/unit/test_troubleshooting_catalog.py`.
- **Config redaction gap.** Shape: new config field leaks through info/check
  output or schema without classification. Verification:
  `tests/unit/test_config_schema.py`.
- **CLI/config parity drift.** Shape: `serve` and `check` expose different
  flags, or TOML/schema/docs disagree. Verification:
  `tests/unit/test_public_contract.py`.
- **Protocol limit truncation.** Shape: H2/H3 oversized bodies reach ASGI as
  empty or truncated data. Verification: protocol handler and integration tests
  for 413 behavior.
- **Scope authority drift.** Shape: HTTP/1, HTTP/2, HTTP/3, or WebSocket build
  different tenant-facing host/scheme/client values under proxy trust.
  Verification: tenant scope matrix and framework compatibility tests.
- **Shutdown hang.** Shape: keep-alive, WebSocket, SSE, async-pool, or H3
  workers outlive `shutdown_timeout`. Verification: worker shutdown,
  connection-draining, H3 integration, and reload tests.
- **Shared mutable race.** Shape: connection counters, queues, lifecycle
  buffers, rate buckets, or reload generation state mutate without ownership.
  Verification: lock/immutability inspection and concurrency tests.
- **Hot-path benchmark promotion.** Shape: optimization claim lands without
  before/after evidence or caveat. Verification: benchmark artifact policy in
  `benchmarks/README.md` and `benchmarks/artifact-schema.json`.

## Done Criteria

- `make lint` and `make ty` clean; no new `type: ignore` or S110 suppressions.
- Tests exercise the interesting path: both values for config flags, failure
  paths for lifecycle changes, malformed input for protocols, and framework
  compatibility when ASGI behavior moves.
- Hot-path changes include a benchmark artifact, or explicitly say why no
  benchmark was run.
- GIL-sensitive changes name shared mutable state and Python 3.14t implications.
- Public API/config/doc behavior changes include changelog and migration notes
  when needed.
- Examples, templates, scaffolds, and site docs move with user-facing changes
  when relevant.
- Every accepted steward finding has test/docs/example/benchmark proof or an
  explicit no-impact note.
- Error messages tell the reader what to do next, not only what went wrong.
- PR description explains why; diff explains what.

## Review Notes

- Commit style follows `git log`: `fix:`, `refactor:`, `deps:`, `release:`,
  or similarly scoped imperative subjects.
- One concern per PR unless the refactor is the change.
- Flag surprises: weird tests, unused public names, suppressions, unreachable
  paths, dead-looking code, benchmark gaps or variance, free-threading
  assumptions, steward disagreement, deferred findings, or docs that contradict
  implementation.
- When this constitution is wrong, update it in a short focused PR.
