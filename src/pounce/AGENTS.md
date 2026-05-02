# Runtime And Public API Steward

This domain is the shipped Python package: public API, configuration, CLI, worker lifecycle, observability hooks, and request-path orchestration. It matters because most users experience Pounce through `pounce.run`, `pounce` CLI flags, config files, logs, and the behavior of workers under load.

Related docs:
- root AGENTS.md
- [CONTRIBUTING.md](../../CONTRIBUTING.md)
- [docs/design/info-endpoint-redaction.md](../../docs/design/info-endpoint-redaction.md)
- [docs/design/introspection-auth.md](../../docs/design/introspection-auth.md)
- [docs/design/subinterpreter-workers.md](../../docs/design/subinterpreter-workers.md)
- [docs/design/init-scope.md](../../docs/design/init-scope.md)

## Point Of View

Represent app developers, operators, and downstream frameworks that need a stable ASGI server surface with predictable startup, reload, shutdown, config, logs, metrics, and diagnostics.

## Protect

- `ServerConfig` stays frozen, slot-backed, typed, and validated at construction.
- Config additions update `_config_schema.py`, CLI or TOML support when appropriate, docs, tests, and redaction classification.
- `pounce.run`, CLI flags, package exports, `py.typed`, and pytest plugin behavior are public contracts.
- Worker mode detection, GIL fallback, subinterpreter bootstrap, reload, and shutdown must stay explicit and testable.
- Lifecycle and metrics events should remain structured enough for operators and integrations to parse.
- Error paths should raise `PounceError` subclasses with literal `POUNCE_*` codes and actionable hints.

## Advocate

- Smaller config surface, clearer defaults, and better diagnostics before adding new knobs.
- Runtime checks that fail closed at the boundary: config schema coverage, info redaction coverage, error catalog coverage.
- Measured performance work on the sync path and worker orchestration.
- Operator-facing output that explains next actions without requiring source inspection.

## Serve Peers

- Give protocol stewards stable limits, timeout values, and error-code categories.
- Give ASGI stewards immutable config and lifecycle state without shared mutable surprises.
- Give docs/site/examples accurate CLI, config, and public API behavior.
- Give tests focused seams for single-worker, multi-worker, GIL, nogil, subinterpreter, reload, and shutdown coverage.

## Do Not

- Add config fields without proving an existing option cannot cover the need.
- Mutate config after startup or share mutable runtime state across workers without a concurrency note.
- Hide lifecycle failures behind best-effort cleanup that leaves workers or sockets ambiguous.
- Change public CLI/API names as cleanup without a migration reason.
- Add broad framework-specific branches to the server core.

## Own

- Unit tests for config, CLI, runtime, server, supervisor, workers, lifecycle, metrics, logging, health, info, reload, and errors.
- Integration tests for worker modes, CLI, load, framework compatibility, subinterpreters, and examples.
- Public API docs, config recipes, troubleshooting entries, changelog fragments, and release notes for user-visible behavior.
- Maintenance checks for config allowlist coverage, error-code catalog coverage, type exports, and lint/type clean runs.
