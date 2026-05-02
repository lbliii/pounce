# Pounce Agent Constitution

## North Star

Pounce exists to make free-threaded Python worth deploying: a pure-Python ASGI
server for Python 3.14t with measurable performance, boring compatibility, and
correctness under true parallelism. Every change should protect the request path
for users who cannot see or audit the server beneath their app.

## Non-Negotiables

- Keep the runtime pure Python. No C extensions, Cython, or native hot-path dependencies.
- Treat `ServerConfig` as frozen shared state. Runtime changes are lifecycle events, not mutation.
- Keep parsers and state machines sans-I/O unless a scoped steward says otherwise.
- No silent `except`, new `type: ignore`, speculative config, or vague diagnostics.
- Sync worker and `_fast_h1.py` are latency-critical. Benchmark before/after if touched.
- Validate at public boundaries; internal code should trust typed, normalized inputs.

## Architecture Boundaries

- Public API: `pounce.run`, `ServerConfig`, CLI flags, package exports, pytest plugin, and documented config files.
- Runtime lifecycle: `server.py`, `supervisor.py`, workers, reload, GIL detection, subinterpreters, shutdown, and connection draining.
- Protocol boundary: wire bytes become typed protocol events before ASGI sees them.
- ASGI boundary: bridges own scope construction, receive/send semantics, streaming, disconnects, and lifespan state.
- Transport boundary: listeners, UDS, TLS, ALPN, UDP/H3 sockets, and socket cleanup.
- Operator boundary: error codes, troubleshooting docs, logs, metrics, health, info, and CLI output.

## Stakes

- Protocol regressions can cause request smuggling, wire corruption, crossed sessions, or silent data loss.
- Worker lifecycle bugs drop requests mid-reload, leak processes or threads, and break shutdown guarantees.
- Free-threaded races make Python 3.14t look unsafe for production.
- Performance regressions erase the reason to choose Pounce over existing ASGI servers.
- Bad config, CLI, docs, or errors waste operator time during incidents and migrations.

## Stop And Ask

- Public API, CLI, `ServerConfig`, config-file schema, pytest plugin, or documented behavior changes.
- New runtime dependency, optional protocol dependency, build/release pipeline change, or packaging metadata change.
- Worker model, GIL detection, subinterpreter behavior, lifecycle state machine, reload, or shutdown semantics.
- Sync worker hot path, `_fast_h1.py`, parser safety behavior, or performance target changes.
- Security/auth, TLS, proxy trust, introspection exposure, redaction, or operator-facing diagnostics.
- Data model or compatibility contract changes for metrics, lifecycle events, logs, error codes, or config schema.
- Test/code disagreement, unreproduced bugs, suspected dead code, or adjacent issues found mid-task.

## Anti-Patterns

- Adding native speedups "just for the hot path."
- Async-ifying the sync worker because it looks cleaner.
- Adding config for future flexibility before a user need exists.
- Catching broad exceptions without one-line logging that says what and why.
- Hiding parser failures behind generic 400s when a `POUNCE_*` code and hint can guide the operator.
- Folding unrelated refactors into a bug fix; flag adjacent issues in the PR instead.
- Inventing abstractions for hypothetical protocols. H3 is real; H4 is not.

## Steward System

Read this root file plus the closest scoped `AGENTS.md` before changing code or docs. Root is the constitution and routing guide; scoped files are domain stewards. Scoped stewards own local invariants, refusal patterns, docs, tests, examples, fixtures, and checks. Cross-boundary work needs `Steward Notes` in the PR description naming consulted stewards, decisions, risks, and follow-ups.

Each steward uses this operating model:

- Point of View: who or what the domain represents.
- Protect: invariants, contracts, quality bars, and failure modes.
- Advocate: features, fixes, and investments the domain should push for.
- Serve Peers: upstream/downstream domains that need clearer contracts, diagnostics, docs, tests, or ergonomics.
- Do Not: local anti-patterns.
- Own: tests, docs, examples, fixtures, and maintenance checks.

## When To Consult

- Proactively consult stewards for cross-boundary, public-facing, hard-to-reverse, performance-sensitive, concurrency-sensitive, security-sensitive, or contract-affecting work.
- Use the nearest steward for local work.
- Use multiple stewards when ownership lines cross.
- Parallelize steward consultation only when questions are independent.
- Keep final synthesis with the implementing agent.

## Ask Stewards

Trigger phrase: `ask stewards`.

For implementation work, consult affected scoped stewards and synthesize the result before editing. For backlog, roadmap, or prioritization work, consult all scoped stewards and produce a rollup with raw steward signals, confidence, dependencies, risks, convergence, minority reports, ranked backlog, and not-now items.

## Extension Routing

- HTTP protocol logic: `src/pounce/protocols/`, `_fast_h1.py`, `_h2_handler.py`, `_h3_handler.py`, `_ws_handler.py`.
- ASGI adaptation: `src/pounce/asgi/`.
- Transports and TLS: `src/pounce/net/`, `h3_worker.py`.
- Public config and CLI: `src/pounce/config.py`, `_config_file.py`, `_config_schema.py`, `_cli.py`.
- Observability and operator endpoints: `_errors.py`, `logging.py`, `metrics.py`, `_health.py`, `_introspect.py`, `_metrics_handler.py`.
- Public docs and release notes: `docs/`, `site/`, `README.md`, `CHANGELOG.md`, `changelog.d/`.

## Done Criteria

- `make lint` and `make ty` clean; no new `type: ignore` or S110 suppressions.
- Tests exercise the interesting path: both values for config flags, failure paths for lifecycle changes, malformed input for protocols, and framework compatibility when ASGI behavior moves.
- Hot-path changes include a benchmark in the PR, or explicitly say why no benchmark was run.
- GIL-sensitive changes note shared mutable state and Python 3.14t implications.
- Public API/config/doc behavior changes include changelog and migration notes when needed.
- Error messages tell the reader what to do next, not only what went wrong.
- PR description explains why; diff explains what.

## Review Notes

- Commit style follows `git log`: `fix:`, `refactor:`, `deps:`, `release:` prefixes, imperative subject, body for motivation.
- One concern per PR unless the refactor is the change.
- Flag surprises: weird tests, unused config, unreachable paths, dead-looking code, benchmark variance, or docs that contradict implementation.
- When this constitution is wrong, update it in a short focused PR.

## See Also

- [CONTRIBUTING.md](CONTRIBUTING.md) - setup, feedback loops, and recipes.
- [docs/troubleshooting.md](docs/troubleshooting.md) - `POUNCE_*` error catalog.
- [docs/design/](docs/design/) - ADRs for load-bearing decisions.
