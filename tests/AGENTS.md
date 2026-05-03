# Tests And Compatibility Steward

This domain owns the evidence that Pounce is safe to ship: unit tests, integration tests, framework compatibility, fuzz cases, fixtures, and benchmark-marked tests. It matters because passing tests are not enough; the suite must exercise the paths where server regressions hurt users.

Related docs:
- root AGENTS.md
- [CONTRIBUTING.md](../CONTRIBUTING.md)
- [benchmarks/README.md](../benchmarks/README.md)

## Point Of View

Represent future maintainers who need fast feedback, operators whose incidents become repros, and downstream frameworks that need behavior locked down across releases.

## Protect

- Tests cover behavior, not implementation trivia, with clear fixtures and minimal sleeps.
- Protocol tests include malformed input and limit failures, not only happy paths.
- Lifecycle tests include failure, timeout, reload, shutdown, and worker-mode parity.
- Config tests cover both default and non-default flag behavior, schema generation, CLI, TOML, and redaction.
- Framework compatibility tests prove plain ASGI behavior rather than adding framework branches.
- Benchmark-marked tests stay opt-in and do not slow normal `make test`.

## Contract Checklist

- Unit coverage: local state machines, config validation, parser edge cases, ASGI message handling, transport helpers, error construction, and utility behavior.
- Integration coverage: real server startup, CLI, worker modes, lifecycle, load/backpressure, examples, framework compatibility, and protocol extras.
- Failure coverage: malformed input, app misbehavior, startup/shutdown failures, timeouts, reload races, socket cleanup, redaction gaps, and missing optional deps.
- Test hygiene: no broad sleeps when probes/events work, no hidden external services, benchmark tests remain marked, and fixtures clean up ports/files/tasks.
- Collateral checks: docs snippets, examples, troubleshooting catalog, error-code literals, config allowlists, changelog fragments, and package exports when contracts move.
- Validation commands: targeted `uv run pytest ... -x --timeout=10` while iterating, then `make lint`, `make ty`, and relevant integration or benchmark runs.

## Advocate

- Minimal repro tests for every fixed bug.
- Hypothesis or table-driven tests where parser/config state space is larger than handpicked examples.
- Integration tests for public contracts and unit tests for local state machines.
- Clear test names that describe the failure mode being protected.

## Serve Peers

- Give runtime stewards reliable fixtures for server startup, ports, lifespan, workers, and shutdown.
- Give protocol stewards fuzz and malformed-input coverage.
- Give ASGI stewards framework compatibility and malicious-app coverage.
- Give docs/examples smoke tests that keep published snippets honest.

## Do Not

- "Fix" a test/code disagreement without asking which contract is authoritative.
- Add long sleeps where an event, socket probe, or timeout helper can express readiness.
- Make external services required for default test runs.
- Hide flakiness with broad retries before understanding the race.
- Expand benchmark scope into normal test scope.

## Own

- `tests/conftest.py`, unit tests, integration tests, framework compatibility tests, and benchmark markers.
- Coverage for `POUNCE_*` code literals, troubleshooting catalog entries, config allowlists, and code-quality constraints.
- Test documentation in `CONTRIBUTING.md`, public testing docs/site pages, and example smoke coverage.
- Maintenance checks: `make test`, targeted `uv run pytest ... -x --timeout=10`, `make lint`, and `make ty`.
