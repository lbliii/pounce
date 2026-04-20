# Epic: Vibe-Coding Readiness — Pounce as the Default ASGI for Agents

**Status**: In Progress — Sprints 0, 1, 2 complete
**Created**: 2026-04-20
**Target**: 0.7.0
**Estimated Effort**: 28–40 hours
**Dependencies**: None (all work is additive)
**Source**: Vibe-coding audit performed 2026-04-20 on branch `lbliii/vibe-audit`. Current baseline already scores ~85% on agent-readiness (strong README, AGENTS.md, typed APIs, `TestServer`, lifecycle events). This epic closes the remaining five gaps that prevent pounce from being a *reflexive* default for agent-driven ASGI work.

---

## Why This Matters

**Problem**: Pounce is agent-friendly but not *agent-optimal*. Five specific friction points force an agent (or the human pairing with one) back to reading source, guessing, or leaving the repo:

1. **Error messages are opaque**: `_errors.py` defines 9 exception types, but the class bodies carry only a status code and a string message. At the 24 raise sites in `src/pounce/`, hints and fix guidance live inline or not at all — an agent surfacing a pounce error to a user cannot cite a code, anchor, or canonical fix.
2. **Config surface is undiscoverable without source**: `ServerConfig` has 50+ fields. There is no machine-readable schema export — an agent scaffolding `pounce.toml` must read `config.py` directly and transcribe by hand, with no validation until runtime.
3. **Zero-to-running path is "write two files yourself"**: No `pounce init` exists. A user asking an agent to "set up a pounce project" triggers the agent to synthesize both `app.py` and `pounce.toml` from memory — error-prone and unverified.
4. **Docs live behind a site build**: `site/content/docs/` is comprehensive but invisible when working offline, at PR-review time, or from inside a fresh clone. No `CONTRIBUTING.md` or `TROUBLESHOOTING.md` at repo root. Agents default to CLAUDE.md + AGENTS.md, which are project-overview docs, not task-recipe docs.
5. **Live-server debugging is log-parsing**: Lifecycle events and Prometheus metrics exist for humans and dashboards, but there's no opt-in introspection endpoint an agent can hit to get `{version, workers, active_connections, config_summary}` in JSON. Runtime debugging requires `grep`.

**Fix**: Ship structured errors, a config schema command, an `init` scaffold, repo-root guidance docs, and an opt-in introspection endpoint. All five are *additive* — no existing API changes — and each ships independently.

### Evidence Table

| Source | Finding | Proposal Impact |
|---|---|---|
| `src/pounce/_errors.py:21-91` | 9 exception classes, no `code` or `hint` field | FIXES (Sprint 1) |
| 24 raise sites across 5 files (`h1.py`, `_fast_h1.py`, `supervisor.py`, `asgi/lifespan.py`, `net/tls.py`) | Error context lives at call sites, not centralized | FIXES (Sprint 1) |
| `src/pounce/config.py` — 50+ frozen fields | No JSON schema, no `--list-config`, no TOML autocomplete | FIXES (Sprint 2) |
| Audit §1 — no `pounce init` command | Users write app.py + pounce.toml from memory | FIXES (Sprint 3) |
| Repo root has no `CONTRIBUTING.md`, `TROUBLESHOOTING.md` | Agents have no task-recipe docs; site-content is offline-invisible | FIXES (Sprint 5) |
| `_health.py` returns liveness, not introspection | No `/{prefix}/info` with config/worker summary | FIXES (Sprint 4) |
| `src/pounce/_cli.py:171` — milo-CLI decorator pattern | Extension points for `init`/`config` subcommands already exist | ENABLES (Sprint 2, 3) |

### Invariants

These must remain true throughout or we stop and reassess:

1. **No breaking changes to existing public API.** Every change is additive: new exception fields default to `None`, new CLI subcommands, new optional endpoint. Existing `pounce.run()`, `ServerConfig`, and `pounce serve` behavior is byte-identical.
2. **Zero new runtime dependencies.** Schema export, init, introspection all use stdlib. No `pydantic`, `jsonschema`, `typer`. Aligns with pounce's "pure Python, no C extensions" positioning.
3. **Performance hot-paths untouched.** The sync worker (`_fast_h1.py`, `sync_worker.py`) gets no new work. Error-object construction cost stays within noise (<1% regression on `pounce bench`).
4. **Secrets never leak from introspection.** The `/info` endpoint returns a *redacted* config view — no TLS cert contents, no file paths that could hint at filesystem layout beyond what the user already configured. Opt-in, off by default.

---

## Target Architecture

### Before (today)

```
Error surface:     raise TLSError("cert file missing")    # opaque string
Config discovery:  read config.py (human)                 # no schema
Project setup:     write app.py + pounce.toml by hand     # no scaffold
Guidance docs:     CLAUDE.md + AGENTS.md + site build     # no repo-root recipes
Live debugging:    tail -f logs | grep                    # no /info
```

### After (this epic)

```
Error surface:     raise TLSError(
                       "cert file missing",
                       code="POUNCE_TLS_CERT_MISSING",
                       hint="Pass --ssl-certfile=PATH or set [tls].certfile in pounce.toml",
                       doc="docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING",
                   )
Config discovery:  pounce config schema --format json      # JSON Schema
                   pounce config show                       # active merged config
Project setup:     pounce init [--framework fastapi]        # app.py + pounce.toml
Guidance docs:     ./CONTRIBUTING.md                        # add-a-test, run-checks
                   ./docs/troubleshooting.md                # error-code catalog
Live debugging:    curl localhost:8000/_pounce/info         # JSON runtime state
```

### Error schema (new)

```python
class PounceError(Exception):
    status_code: int = 500
    code: str = "POUNCE_E_UNKNOWN"   # semantic, greppable

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        hint: str | None = None,
        doc: str | None = None,
    ) -> None: ...
```

### Introspection contract (new)

```json
GET /_pounce/info  →  {
  "version": "0.7.0",
  "python": "3.14.1t",
  "gil_enabled": false,
  "worker_mode": "thread",
  "workers": {"configured": 4, "alive": 4, "generation": 3},
  "uptime_seconds": 412.5,
  "active_connections": 17,
  "config": { /* redacted ServerConfig */ }
}
```

---

## Sprint Structure

| Sprint | Focus | Effort | Risk | Ships Independently? |
|---|---|---|---|---|
| 0 | Design: error-code scheme, schema serializer, redaction policy, `init` scope | 4h | Low | Yes (RFC only) |
| 1 | Structured errors (`code`/`hint`/`doc` fields) + migrate 24 raise sites | 8h | Medium | Yes |
| 2 | `pounce config schema` + `pounce config show` subcommands | 5h | Low | Yes |
| 3 | `pounce init` scaffolding (vanilla ASGI + pounce.toml) | 4h | Low | Yes |
| 4 | Opt-in `/_pounce/info` introspection endpoint | 6h | Medium | Yes |
| 5 | `CONTRIBUTING.md` + `docs/troubleshooting.md` (error-code catalog) | 4h | Low | Yes |

Sprints 1–5 have no inter-dependencies except that Sprint 5's troubleshooting doc is richer after Sprint 1 (more codes to catalog). Any sprint can ship before or after any other.

---

## Sprint 0: Design & Validate ✅

**Status**: Complete (2026-04-20). Outputs:

- `docs/design/error-codes.md` — semantic `POUNCE_<CATEGORY>_<SPECIFIC>` scheme, 10 categories, AST-based anti-collision test
- `.context/schema_prototype.py` — working serializer, 67 `ServerConfig` fields emitted, self-check passes (`python .context/schema_prototype.py --check`)
- `docs/design/info-endpoint-redaction.md` — fail-closed allowlist classifying every field as EXPOSE / REDACT_TO_BOOL / REDACT
- `docs/design/init-scope.md` — vanilla-only, framework flavors deferred
- `docs/design/introspection-auth.md` — off-by-default, loopback-bind, warn-on-public, no token

**Goal**: Lock the five decisions that would otherwise force rework mid-implementation.

### Task 0.1 — Error-code scheme

Decide: semantic (`POUNCE_TLS_CERT_MISSING`) vs numeric (`POUNCE_E042`).

**Output**: ADR at `docs/design/error-codes.md`. Recommend semantic: greppable across codebase + docs, self-documenting in stack traces, no registry file to keep in sync.

**Acceptance**: ADR includes naming rules (`POUNCE_<CATEGORY>_<SPECIFIC>`), categories enumerated (`PARSE`, `TIMEOUT`, `LIMIT`, `APP`, `LIFESPAN`, `SUPERVISOR`, `WORKER`, `TLS`, `RELOAD`, `CONFIG`), and an anti-collision rule (codes live on the error class, one per raise site or shared intentionally).

### Task 0.2 — Config schema serializer

Decide how `dataclasses.fields(ServerConfig)` maps to JSON Schema. Specifically: `Literal[...]` → `enum`, `Path` → `string` with `format: path`, `timedelta` → `number` with `x-unit: seconds`, nested dataclasses → `$ref`.

**Output**: Prototype serializer in `.context/schema_prototype.py` that emits valid JSON Schema Draft 2020-12 for current `ServerConfig`.

**Acceptance**: `python .context/schema_prototype.py | jsonschema-cli validate` passes (or equivalent stdlib validation). All 50+ fields represented. Prototype is <100 LOC.

### Task 0.3 — Redaction policy for `/info`

Decide which `ServerConfig` fields are safe to expose.

**Output**: Allowlist (not denylist — fail-closed) in ADR. At minimum: `host`, `port`, `workers`, `worker_mode`, `http3`, `compression_enabled`, timeouts. **Exclude**: any path-like field, `ssl_*`, `uds`, custom hook references.

**Acceptance**: ADR lists every `ServerConfig` field with `EXPOSE` / `REDACT` / `REDACT_TO_BOOL` (path exists yes/no, not the path itself).

### Task 0.4 — `pounce init` scope

Decide: vanilla ASGI only, or framework-aware (`--framework fastapi|starlette|django`)?

**Output**: Recommendation. Recommend **vanilla-only in Sprint 3**, framework flavors deferred. Rationale: framework scaffolds duplicate each framework's own CLI (e.g. `fastapi dev`), adds maintenance burden, and the point of `init` is to prove pounce works — not to compete with framework scaffolds.

**Acceptance**: ADR notes the decision + deferred scope (framework flavors as a separate epic if demand emerges).

### Task 0.5 — Introspection auth model

Decide: how do we prevent `/_pounce/info` from being internet-exposed by accident?

**Output**: ADR. Recommend: disabled by default, enable via `introspection_enabled=True`. When enabled, bind to loopback-only by default (`introspection_bind="127.0.0.1"`); user must explicitly set `introspection_bind="0.0.0.0"` to expose publicly, with a startup warning when they do.

**Acceptance**: ADR covers off-by-default, loopback-default, warning-on-public-bind, and whether we ever need a token (recommend: no — if you want auth, put it behind your reverse proxy).

---

## Sprint 1: Structured Errors

**Goal**: Every pounce error carries a semantic code and optional hint, reachable from stack traces and serialized responses.

### Task 1.1 — Extend `PounceError` base class

Add `code: str`, `hint: str | None = None`, `doc: str | None = None` to `PounceError.__init__`. Class attribute `code` defaults to `"POUNCE_E_UNKNOWN"`; subclasses override (`ParseError.code = "POUNCE_PARSE_E"` as fallback category code).

**Files**: `src/pounce/_errors.py`.
**Acceptance**:
- `rg 'class \w+Error\(PounceError\)' src/pounce/_errors.py` shows all 9 subclasses unchanged in signature.
- `PounceError("x").code == "POUNCE_E_UNKNOWN"` (backward-compat default).
- `PounceError("x", code="POUNCE_TLS_CERT_MISSING").code == "POUNCE_TLS_CERT_MISSING"`.
- New test `tests/unit/test_errors.py` covers default + override + pickle round-trip (errors must remain pickleable for process-worker mode).

### Task 1.2 — Migrate 24 raise sites

For each of the 24 raise sites (h1.py:1, _fast_h1.py:13, supervisor.py:5, asgi/lifespan.py:1, net/tls.py:4), add a semantic `code=` argument and a `hint=` where an actionable fix exists (not every error has a hint — that's fine).

**Files**: `src/pounce/protocols/h1.py`, `src/pounce/_fast_h1.py`, `src/pounce/supervisor.py`, `src/pounce/asgi/lifespan.py`, `src/pounce/net/tls.py`.
**Acceptance**:
- `rg 'raise (Pounce|Parse|Limit|App|Lifespan|Supervisor|Worker|TLS|Reload|RequestTimeout)Error' src/pounce/ | wc -l` → 24 (unchanged).
- `rg 'raise \w+Error\([^)]*code=' src/pounce/ | wc -l` → 24 (every raise now carries a code).
- No duplicate codes across distinct error-semantic sites (enforced by new test `tests/unit/test_error_codes.py` that collects every `code` and asserts uniqueness per subclass, or documents intentional sharing).
- `make lint` + `make ty` pass.

### Task 1.3 — Include `code` in error-response bodies

When pounce renders an error response (see existing error-response path, likely in `_request_pipeline.py` or `asgi/bridge.py`), include `{"code": err.code, "hint": err.hint}` in the JSON body when `error_debug=True` in config, and always in the `X-Pounce-Error-Code` response header (cheap, machine-readable, no secret risk).

**Files**: Wherever pounce converts `PounceError` → HTTP response.
**Acceptance**:
- New integration test: induce a `LimitError`, assert response has `X-Pounce-Error-Code: POUNCE_LIMIT_...` header.
- Debug mode: body contains `code` and `hint` fields.

---

## Sprint 2: Config Schema & Inspection

**Goal**: An agent (or a human) can ask pounce what config fields exist and what the active config is, without reading source.

### Task 2.1 — `pounce config schema` subcommand

New subcommand emitting JSON Schema Draft 2020-12 for `ServerConfig`. Uses the Sprint 0.2 prototype, productionized.

**Files**: new `src/pounce/_config_schema.py`, register in `src/pounce/_cli.py`.
**Acceptance**:
- `pounce config schema --format json | jq .type` → `"object"`.
- `pounce config schema --format toml-template` → commented `pounce.toml` template with every field + default + one-line doc derived from dataclass field docstrings.
- Output is deterministic (sorted keys) — tested via `tests/unit/test_config_schema.py` with a golden snapshot.

### Task 2.2 — `pounce config show` subcommand

Print the merged config (TOML file + CLI args + defaults) as the Sprint 0.3 redacted view. Great for "why is my setting not taking effect" debugging.

**Files**: `src/pounce/_cli.py`.
**Acceptance**:
- `pounce config show --app myapp:app` prints TOML of the resolved config, no secret fields.
- `--format json` supported for agent consumption.
- Unit test asserts a redacted field (e.g. `ssl_certfile`) appears as `<redacted>` or is omitted per the Sprint 0.3 allowlist.

---

## Sprint 3: Project Scaffolding

**Goal**: `pounce init` produces a working minimal project in one command.

### Task 3.1 — `pounce init` subcommand

Drops three files in the CWD (refuse if any exist without `--force`):
- `app.py` — vanilla ASGI `async def app(scope, receive, send)` returning "hello from pounce"
- `pounce.toml` — commented template from Sprint 2.1 (heavily commented defaults)
- `.gitignore` entries for `__pycache__/`, `.pounce/`

**Files**: new `src/pounce/_init.py`, register in `src/pounce/_cli.py`, new `src/pounce/_templates/` (or use `importlib.resources` with existing template directory if one exists).
**Acceptance**:
- In a fresh tempdir: `pounce init && pounce serve --app app:app --port 0` — server starts, `GET /` returns 200 "hello from pounce". Integration test covers this end-to-end.
- `pounce init` in a non-empty dir without `--force` exits non-zero with an actionable message ("files would be overwritten: app.py — pass --force to proceed").
- Generated `pounce.toml` passes `pounce check` (the existing validator).

---

## Sprint 4: Introspection Endpoint

**Goal**: A running pounce instance can be introspected via an opt-in JSON endpoint.

### Task 4.1 — `/_pounce/info` endpoint

Wire into the same path-dispatch layer as `_health.py`. Gated by new config `introspection_enabled: bool = False`, bind-scoped by `introspection_bind` (default `"127.0.0.1"`). Returns the Sprint 0.3 redacted view plus live counters from lifecycle state.

**Files**: new `src/pounce/_introspect.py`, add two fields to `src/pounce/config.py`, wire into request dispatch.
**Acceptance**:
- With `introspection_enabled=True`: `curl http://127.0.0.1:8000/_pounce/info` returns JSON with the contract above.
- With default config: endpoint returns 404 (or, better, is not registered at all).
- Binding to `0.0.0.0` with introspection enabled emits a `WARNING` log at startup.
- Integration test covers enabled/disabled cases and verifies no secret fields appear.
- `make ty` passes — the response shape is a typed `TypedDict` or `dataclass`.

### Task 4.2 — Endpoint is disable-path-configurable

The default path `/_pounce/info` may collide with a user's own routing. Expose `introspection_path: str = "/_pounce/info"` so users can move it.

**Files**: `src/pounce/config.py`.
**Acceptance**: Unit test shows custom path works; integration test verifies no regression of default.

---

## Sprint 5: Repo-Root Guidance Docs

**Goal**: An agent cloning the repo has task-recipe docs at root without needing the site build.

### Task 5.1 — `CONTRIBUTING.md`

Covers: setup (`make setup`, `make install`), feedback loops (`make test`, `make lint`, `make ty`), "how to add a test" recipe (the `@with_lifespan` fixture pattern), "how to add a config field" recipe, "how to add an error" recipe (post-Sprint 1), and PR expectations (no `type: ignore`, benchmark sensitive paths).

**Files**: new `CONTRIBUTING.md` at repo root.
**Acceptance**:
- All code snippets copy-paste-runnable against current HEAD.
- Linked from `README.md` and `AGENTS.md`.
- Each recipe under 15 lines — this is not a book.

### Task 5.2 — `docs/troubleshooting.md` (error-code catalog)

For each error code introduced in Sprint 1, one entry: what it means, what typically causes it, what to do. Generated (optionally) from a dedicated docstring on each error subclass so the catalog cannot drift from code.

**Files**: new `docs/troubleshooting.md`, optional generator `scripts/gen_troubleshooting.py`.
**Acceptance**:
- Every code from `rg 'code="POUNCE_' src/pounce/` appears in the catalog (enforced by a test).
- The anchor links used in `TLSError.doc="docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING"` resolve (link checker in CI or a simple unit test that parses the markdown).

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Error-class signature change breaks downstream code that pickles/subclasses | Medium | High | Invariant #1 (additive only). Sprint 1.1 keeps constructor back-compat: all new fields kwarg-only with defaults. Test covers pickle round-trip. |
| Schema serializer drifts from `ServerConfig` | Medium | Medium | Sprint 2.1 acceptance includes a golden snapshot; `make test` fails on drift, forcing the schema to track the dataclass. |
| `/info` endpoint leaks secrets | Low | Critical | Sprint 0.3 fail-closed allowlist + Sprint 4.1 integration test asserts no secret field names appear. Disabled by default. Loopback-default when enabled. |
| `pounce init` overwrites user files | Low | High | Refuse without `--force`. Acceptance test covers non-empty-dir case. |
| Performance regression from error-object overhead | Low | Medium | Sprint 1 acceptance includes `pounce bench` delta <1%. Error construction is already an exceptional path — extra kwargs are negligible. |
| CONTRIBUTING.md rots | Medium | Low | Sprint 5.1 acceptance requires snippets runnable against HEAD. A post-merge CI check that executes the scripted recipes keeps them honest (follow-up, not blocking). |
| Troubleshooting catalog drifts from actual error codes | Medium | Medium | Sprint 5.2 acceptance includes a test that enumerates raised codes and asserts catalog coverage. |

---

## Success Metrics

| Metric | Current | After Sprint 1 | After Sprint 3 | After Final Sprint |
|---|---|---|---|---|
| Errors carrying a semantic code | 0 / 24 | 24 / 24 | 24 / 24 | 24 / 24 |
| Config fields discoverable without reading source | 0 / 50+ | 0 / 50+ | 50+ / 50+ | 50+ / 50+ |
| Zero-to-running commands (fresh dir → served response) | 3–5 (write two files, then `serve`) | 3–5 | 2 (`init` + `serve`) | 2 |
| Task-recipe docs at repo root | 0 | 0 | 0 | 2 (CONTRIBUTING, troubleshooting) |
| Runtime introspection without log parsing | No | No | No | Yes (opt-in) |
| Agent-readiness audit score (self-assessed) | 85% | 90% | 93% | 97% |

The 97% ceiling (not 100%) reserves room for post-epic follow-ups: framework-aware `init`, MCP pounce-introspection server, executable CONTRIBUTING.md recipes in CI.

---

## Relationship to Existing Work

- **`AGENTS.md` (shipped in #56)** — this epic extends the agent-friendliness thesis AGENTS.md stakes out. No changes to AGENTS.md required beyond a link to `CONTRIBUTING.md` from Sprint 5.1.
- **`_health.py`** — parallel to Sprint 4's `/info` endpoint; Sprint 4 should reuse the same dispatch path `_health.py` uses. Do not merge them — liveness and introspection have different auth models.
- **`milo-cli`** — all new subcommands (`config`, `init`) use the existing `@cli.command` decorator pattern in `_cli.py:180`. No framework change.
- **Merge/release freeze** — none known. Target 0.7.0 release.

---

## Changelog

| Date | Change | Reason |
|---|---|---|
| 2026-04-20 | Initial draft | Audit of agent-readiness identified 5 gaps; epic drafted to close them. |
| 2026-04-20 | Sprint 0 complete | All 5 design tasks landed: error-code ADR, schema prototype (67 fields, self-check passes), redaction allowlist ADR, init-scope ADR, introspection-auth ADR. Ready for Sprint 1. |
| 2026-04-20 | Sprint 1 complete | `PounceError` extended with `code`/`hint`/`doc` (+ pickle preservation). 24 raise sites migrated; AST enforcement test guards naming + uniqueness. Unified `_fast_h1.ParseError` with canonical class. `_send_error` emits `X-Pounce-Error-Code` header on every pounce-generated 4xx/5xx; debug mode appends code+hint to body. |
| 2026-04-20 | Sprint 2 complete | `pounce config schema --output-format json\|toml-template` emits JSON Schema Draft 2020-12 (67 fields) or commented TOML template; `pounce config show` prints the resolved merged config through the Sprint 0.3 fail-closed redaction allowlist. `src/pounce/_config_schema.py` centralizes the serializer, TOML renderer, and `INFO_ALLOWLIST`. Unit + CLI integration tests cover canary-secret regression and deterministic output. |
| 2026-04-20 | Sprint 3 complete | `pounce init` scaffolds a fresh project (`app.py` + `pounce.toml` + `.gitignore`) in one command, refusing collisions without `--force`. `src/pounce/_init.py` owns the vanilla ASGI template; `pounce.toml` is generated from the Sprint 2.1 TOML template (top-level keys, no `[pounce]` header). Integration test runs the generated `app.py` through a real supervisor + socket and verifies `GET /` returns `200 hello from pounce\n`. |
| 2026-04-20 | Sprint 5 complete | `CONTRIBUTING.md` at repo root (setup, feedback loops, recipes for tests/config fields/errors, PR expectations), linked from `README.md` and `AGENTS.md`. `docs/troubleshooting.md` catalogs all emitted `POUNCE_*` codes (22 raise sites + 7 `_send_error` sites + 10 category fallbacks), grouped by category with cause/remediation prose. Coverage enforced by `tests/unit/test_troubleshooting_catalog.py`, which AST-walks raise sites, `_send_error` calls, and `default_code` attributes and fails if any code lacks a catalog entry (or vice versa). |
