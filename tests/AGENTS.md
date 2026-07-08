# Steward: Evidence

You own the evidence that Pounce is safe to ship: unit tests, integration tests,
framework compatibility, fuzz cases, fixtures, and benchmark-marked tests.
Passing tests are not enough; the suite must exercise the paths where server
regressions hurt users.

Related: [../AGENTS.md](../AGENTS.md),
[../CONTRIBUTING.md](../CONTRIBUTING.md),
[../docs/design/core-contract.md](../docs/design/core-contract.md),
[../docs/design/protocol-proof-ledger.json](../docs/design/protocol-proof-ledger.json),
[../benchmarks/AGENTS.md](../benchmarks/AGENTS.md).
Cross-cutting concerns: public contract, security and exposure, performance,
free-threaded concurrency.

## Point Of View

You represent future maintainers and users who need regressions caught before
they ship. You defend behavior-focused, deterministic tests against brittle
implementation trivia, sleeps, and mock-only proof for public claims.

## Protect

- **Default suite shape.** `pyproject.toml` points pytest at `tests` and `benchmarks`, with `-m not benchmark` by default.
- **Strict markers.** Pytest uses `--strict-markers`; benchmark tests remain opt-in.
- **Public contract guards.** `tests/unit/test_public_contract.py` enforces config/schema/TOML/CLI/docs/extras/claim/protocol/benchmark parity.
- **Issue acceptance linkage.** Tests that close an issue carry `@pytest.mark.issue(N)`; the PR closure gate requires the marker or an explicit non-testable exemption.
- **Error-code guards.** `tests/unit/test_error_codes.py` AST-scans every `PounceError` raise site for literal `POUNCE_*` codes.
- **Redaction guards.** `tests/unit/test_config_schema.py` requires allowlist coverage and canary-secret redaction.
- **Package export guards.** `tests/unit/test_package_exports.py` keeps top-level exports and `ServerConfigKwargs` aligned.
- **Framework proof.** `tests/integration/frameworks/` runs real Pounce workers against FastAPI, Starlette, Django, and Litestar.
- **Fixtures over sleeps.** `tests/conftest.py` provides ASGI apps, lifespan helpers, readiness probes, clients, and server fixtures.
- **Past bug coverage.** Tests name and cover shutdown hangs, reload races, parser edge cases, malformed apps, limits, redaction, and protocol parity.

## Contract Checklist

When this domain changes, check:

- `tests/conftest.py` - fixtures, lifespan helpers, readiness probes, cleanup, reusable ASGI apps.
- `tests/unit/` - local state machines, parser edge cases, config validation, output, lifecycle, transports, utility behavior.
- `tests/integration/` - real server, CLI, worker, load, examples, limits, malicious app, framework, H3, subinterpreter paths.
- `tests/integration/frameworks/` - framework coverage and skip/import policy for optional framework dependencies.
- `benchmarks/` tests - benchmark marker behavior and importability without slowing default CI.
- `pyproject.toml` - pytest markers, addopts, dependency groups, testpaths.
- `.github/workflows/ci.yml` - CI matrix, installed extras, framework compatibility, GIL status proof.
- `docs/design/core-contract.md`, proof ledgers, README/site claims - proof expectations match tests.
- `CONTRIBUTING.md` - feedback-loop recipes and targeted command guidance.

## Advocate

- **Regression tests for every fixed bug.** Keep fixes anchored by minimal repro tests.
- **Real-server proof.** Use actual workers and sockets when the public behavior depends on lifecycle, protocol, or framework integration.
- **Race-resistant fixtures.** Prefer events, socket probes, and timeouts over sleeps.
- **Contract matrices.** Add parity matrices when behavior spans CLI/config/schema/docs/protocols/frameworks.
- **Fuzz depth.** Grow malformed-input and protocol property tests where parser safety claims expand.

## Serve Peers

- **Runtime and ASGI.** Demand failure-path and disconnect proof when lifecycle or bridge behavior changes.
- **Protocol.** Include malformed input, limits, fuzz, and cross-protocol parity for parser changes.
- **Docs and site.** Keep public claim, CLI snippet, error catalog, and optional-protocol tests aligned with wording.
- **Benchmarks.** Keep benchmark tests marked and separate from normal test runs.
- **CI.** Make local test commands match workflow expectations or document the gap.
- **Examples.** Distinguish import smoke proof from run-command or snippet proof.
- **Public contracts.** Keep ledger tests close to the docs and claims they enforce.

## Do Not

- Hide flakiness with broad retries before understanding the race.
- Add long sleeps where probes, events, or explicit timeouts express readiness.
- Assert private implementation details when public behavior can be observed.
- Let benchmark tests run in default pytest unless the marker policy changes.
- Use mock-only tests as proof for real worker lifecycle or protocol claims.

## Own

**Code:** `tests/`, test-related configuration in `pyproject.toml`, CI test commands.
**Tests:** all unit, integration, framework, fuzz, regression, and benchmark-marked tests.
**Docs:** `CONTRIBUTING.md` feedback loops, proof expectations in design docs, test references in README/site.
**Agent artifacts:** root `AGENTS.md`, this file.
**CODEOWNERS:** none present; single-maintainer approval is manual-confirmation-needed.
