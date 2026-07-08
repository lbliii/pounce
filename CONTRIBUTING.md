# Contributing to Pounce

Short, recipe-oriented guide for contributors and agents working on pounce.
For the *why* behind the design rules, read [AGENTS.md](AGENTS.md).

## Setup

Pounce targets free-threaded CPython (`3.14t`). One-time venv creation, then
editable install with dev dependencies:

```bash
make setup    # uv venv --python 3.14t .venv
make install  # uv sync --active --group dev --frozen
```

`uv` is the package manager. If `make setup` fails, `uv python install 3.14t`
first.

## Feedback loops

```bash
make test     # full pytest suite (~15s)
make lint     # ruff check src/ tests/
make format   # ruff format src/ tests/  (writes)
make ty       # ty check src/pounce/
```

Faster while iterating: `uv run pytest tests/unit/test_<area>.py -x --timeout=10`.

A change is not done until `make lint` and `make ty` are clean with no new
`# type: ignore` or `# noqa: S110` suppressions.

## Recipes

### Add a test

Use the `@with_lifespan` decorator from `tests/conftest.py` to skip lifespan
boilerplate:

```python
from tests.conftest import with_lifespan

@with_lifespan
async def _echo_app(scope, receive, send):
    await receive()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})
```

Unit tests live in `tests/unit/`, integration tests in `tests/integration/`.
For tests that need a running worker, copy the `_start_supervisor(...)`
helper in `tests/integration/test_multi_worker.py`.

### Add a config field

Pounce's TOML loader and the `/info` redaction allowlist are both derived
from `dataclasses.fields(ServerConfig)` — fail-closed. Three steps:

1. Add the field to `ServerConfig` in `src/pounce/config.py` with a type
   annotation and a default.
2. Classify it in `INFO_ALLOWLIST` in `src/pounce/_config_schema.py` as
   `EXPOSE`, `REDACT_TO_BOOL`, or `OMIT`. Missing entries fail the
   `test_allowlist_covers_every_config_field` test.
3. If it needs a CLI flag, add it to `serve`/`check` in `src/pounce/_cli.py`.

TOML loading and `pounce config schema` pick the field up automatically.

### Add or expand a public feature

Start with the ownership gate in
[docs/design/core-contract.md](docs/design/core-contract.md). Classify the
feature before writing code:

- **Core:** ASGI, HTTP/1.1, worker lifecycle, config validation, or operator
  diagnostics.
- **Optional protocol:** HTTP/2, WebSocket, HTTP/3, TLS, or another install-gated
  protocol/transport path.
- **Helper:** static files, middleware, compression, rate limiting, request
  queueing, debug pages, or another convenience layer.
- **Tooling/integration:** testing helpers, benchmarks, OpenTelemetry, Sentry, or
  external observability systems.

Every public feature PR should answer:

1. Why should Pounce own this instead of app middleware, a reverse proxy, a
   process manager, or deployment tooling?
2. Which surfaces change: `ServerConfig`, `pounce.run`, CLI, TOML, schema,
   redaction/info, logs, metrics, error codes, docs, examples, tests,
   benchmarks, or changelog?
3. What proof is included, and what collateral moves with it?
4. What limitations or degraded behavior remain?

If a PR only narrows public claims or adds contributor-process docs, say
`no runtime behavior changed` and name the docs/site parity checks used.

For numeric performance claims, either reference a benchmark artifact that
follows `benchmarks/artifact-schema.json` or explicitly label the number as a
local snapshot, tuning example, or historical note.

### Add an error

Every `raise` of a `PounceError` subclass must pass a
`code="POUNCE_<CATEGORY>_<SPECIFIC>"` literal. The AST test
`tests/unit/test_error_codes.py` enforces the scheme at collection time.

```python
raise TLSError(
    f"TLS certificate not found: {path}",
    code="POUNCE_TLS_CERT_FILE_NOT_FOUND",
    hint="Check the --ssl-certfile path.",
)
```

Then add a matching entry in `docs/troubleshooting.md` so the coverage test
(`tests/unit/test_troubleshooting_catalog.py`) stays green. See
[docs/design/error-codes.md](docs/design/error-codes.md) for the naming scheme.

## Releasing

Releases publish to PyPI from a GitHub release (see
`.github/workflows/python-publish.yml`). The publish workflow asserts the
release tag equals `v<pyproject version>` and runs `twine check --strict` on
the built artifacts, so a mistagged release or a metadata-broken wheel fails
before anything is published.

Cut a release:

1. Add a Towncrier news fragment for each change in `changelog.d/`
   (`<ISSUE>.<added|changed|deprecated|removed|fixed|security>.md`).
2. Compile the changelog: `poe changelog` (runs `towncrier build --yes`).
3. Bump `version` in `pyproject.toml` (`[project]`).
4. Commit the changelog + version bump, then tag: `git tag vX.Y.Z` matching the
   new `pyproject.toml` version (the publish workflow rejects a mismatch).
5. Run `make gh-release` (derives `v$VERSION` from `pyproject.toml` and creates
   the GitHub release, which triggers the PyPI publish workflow).

`make gh-release` keeps the tag and `pyproject.toml` version in lockstep; a
hand-cut GitHub release works too, but you must tag `vX.Y.Z` exactly.

## PR expectations

- **Tight diff.** One concern per PR. Section headers in a diff mean it's two
  PRs. Exceptions: refactors that rename a concept across many files.
- **Executable issue acceptance.** A PR that says `Closes #123` carries a
  `@pytest.mark.issue(123)` acceptance test. For non-testable docs or
  positioning work, record `Acceptance: n/a (reason)` in the PR body. See
  [docs/backlog-automation.md](docs/backlog-automation.md).
- **No `type: ignore`.** Target is zero. Narrow the type or fix the code.
- **No silent excepts.** S110 is enforced in CI. If you must swallow, log
  what and why in one line.
- **Benchmarks for hot-path changes.** Touching `_fast_h1.py`, `sync_worker.py`,
  or the protocol parsers needs a before/after from `pounce bench`.
- **Error messages say what to do next.** `hint=` is as important as the
  message itself.
- **PR body explains *why*.** The diff explains *what*.

See [AGENTS.md](AGENTS.md) for the full list of stop-and-ask escape hatches.
