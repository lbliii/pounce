# Epic: Vibe-Readiness Polish — Wire the Last Mile

**Status**: Implemented / Historical — see changelog below for shipped sprints.
Current roadmap work is tracked in
[ironclad-bengal-chirp.md](ironclad-bengal-chirp.md).
**Created**: 2026-04-20
**Target**: 0.7.0
**Estimated Effort**: 14–20 hours
**Dependencies**: Builds on `docs/plans/vibe-coding-epic.md` (Sprints 0–3 & 5 shipped; Sprint 4 deferred and rolled into this epic as Sprint 5)
**Source**: Hands-on agent-perspective evaluation performed 2026-04-20 on branch `lbliii/vibe-readiness`. The original epic landed the architecture (structured errors, schema serializer, init scaffold, troubleshooting catalog, `--llms-txt`, `--mcp`). This epic closes the last-mile UX gaps that surfaced when an agent actually exercised the toolchain.

---

## Why This Matters

**Problem**: pounce has built every layer an agent needs (POUNCE_ codes, doc anchors, schema export, init scaffold, troubleshooting catalog), but the layers aren't wired through to the surfaces an agent actually touches. The result: a fresh agent gets confirmation of brilliance in the architecture and friction at every interaction.

Seven specific frictions verified by hands-on use:

1. **CLI errors hide their POUNCE_ code.** `_cli.py:278` converts `PounceError` to `_die(str(exc), hint=...)` and discards `exc.code` / `exc.doc`. The catalog at `docs/troubleshooting.md` is unreachable from the moment of failure unless the user already knows to grep — defeating the purpose of the codes.
2. **`pounce config --help` is broken.** Renders positional `_command_config` instead of subcommands `schema` / `show`. Discoverability cliff: `--llms-txt` lists them; the human/agent help renderer does not.
3. **`pounce check --config FILE` is unrecognized.** `_cli.py:601` declares no `config` parameter and `_cli.py:699` calls `load_config_with_overrides(cli_overrides)` without a path. The just-scaffolded `pounce.toml` cannot be validated by the just-scaffolded `check` command.
4. **`pounce check` is silent on success.** Exits 0 with zero output despite `_output.check_results(...)` being called — agents cannot distinguish "validated" from "did nothing." Either the renderer skips the all-pass case or stdout is being suppressed.
5. **`pounce init` template is sparse.** `_init.APP_TEMPLATE` points at `pounce serve` and stops. A fresh agent has a working app but no map to `pounce config schema`, `pounce check`, `pounce info`, troubleshooting, examples, or `--mcp`.
6. **`/_pounce/info` introspection endpoint never shipped.** Sprint 4 of the original epic was deferred. No `_introspect.py` exists. Live debugging is still log-parsing — exactly the gap the original epic flagged as a top-5 friction.
7. **`pounce init` prints a literal `None`** at the end of success. Milo's CLI dispatcher is printing the command function's return value. First impression bug.

**Fix**: Plumb codes through the CLI error surface, make the discovery commands actually discover, and ship the deferred introspection endpoint. All seven changes are *additive* — no existing public API changes. Each ships independently.

### Evidence Table

| Source | Finding | Proposal Impact |
|---|---|---|
| `src/pounce/_cli.py:273-283` | `PounceError` caught but `code`/`hint`/`doc` collapsed to `str(exc)` + computed hint | FIXES (Sprint 1) |
| `pounce config --help` runtime output | Shows positional `_command_config`; no subcommand list | FIXES (Sprint 2.1) |
| `pounce --llms-txt` runtime output | Same subcommands listed correctly there → discovery is broken at one renderer, not at the registry | FIXES (Sprint 2.1) |
| `src/pounce/_cli.py:601-699` | `check` has no `config` parameter; passes only `cli_overrides` to loader | FIXES (Sprint 3) |
| `src/pounce/_cli.py:723` runtime: `pounce check --app app:app` exits 0 with empty stdout | `_output.check_results(all_passed=True, ...)` does not render the success case | FIXES (Sprint 2.2) |
| `src/pounce/_init.py:23-47` `APP_TEMPLATE` | Docstring lists only `pounce serve`; no signposts to the rest of the surface | FIXES (Sprint 4) |
| `src/pounce/_cli.py:948-975` `init`; runtime trailing `None` after success | Function returns implicit `None`; milo prints it | FIXES (Sprint 2.3) |
| `rg "introspect" src/pounce/` returns only `_config_schema.py` | No `_introspect.py`; no `/_pounce/info` route registered | FIXES (Sprint 5) |
| `docs/plans/vibe-coding-epic.md` lines 262–284 | Sprint 4 (introspection endpoint) designed but not delivered | FIXES (Sprint 5) |

### Invariants

These must remain true throughout or we stop and reassess:

1. **No new runtime dependencies.** Same constraint as the parent epic. Schema, init, introspection, and error rendering all use stdlib + `milo` (already in tree).
2. **No breaking changes to public API.** `pounce.run`, `ServerConfig` field names/types, existing CLI flags stay byte-identical. New flags are additive with safe defaults.
3. **Every emitted `POUNCE_` code stays in the troubleshooting catalog.** `tests/unit/test_troubleshooting_catalog.py` already enforces this — Sprint 1 must not introduce a new code without a catalog entry.
4. **Sync-worker hot path untouched.** `_fast_h1.py` and `sync_worker.py` get no new work. Error-rendering changes happen at the CLI boundary, not the request path.
5. **Introspection endpoint is fail-closed for secrets.** Sprint 5 reuses the existing `INFO_ALLOWLIST` from `_config_schema.py` — disabled by default, loopback-bound when enabled, allowlist-driven exposure.

---

## Target Architecture

### Before (today)

```
CLI error:              Error: Could not import module 'foo'.  Hint: Check sys.path.
                        ^^ no code, no doc anchor — catalog unreachable

pounce config --help:   positional argument: _command_config
                        ^^ broken; subcommands invisible

pounce check --config:  unrecognized arguments: --config /path/pounce.toml
                        ^^ asymmetry with serve

pounce check (success): (empty stdout, exit 0)
                        ^^ agents cannot tell pass from no-op

pounce init:            Scaffolded 3 files...
                        Next: pounce serve --app app:app
                        None
                        ^^ stray return-value print

init app.py:            "Run: pounce serve --app app:app"
                        ^^ no map to the rest of the surface

/_pounce/info:          (does not exist)
                        ^^ live debugging = tail -f | grep
```

### After (this epic)

```
CLI error:              Error: Could not import module 'foo'.
                          Code: POUNCE_APP_IMPORT_FAILED
                          Hint: Check sys.path.
                          See:  docs/troubleshooting.md#POUNCE_APP_IMPORT_FAILED

pounce config --help:   subcommands:
                          schema  Emit the ServerConfig schema
                          show    Print the resolved merged config

pounce check --config:  ✓ accepts a TOML config path, parity with serve

pounce check (success): ✓ Config valid (12 fields resolved: 9 defaults, 3 overrides)
                        ✓ App importable: app:app
                        ✓ Port 8000 available on 127.0.0.1
                        All checks passed.

pounce init:            Scaffolded 3 files in ./:
                          app.py
                          pounce.toml
                          .gitignore
                        Next: pounce serve --app app:app
                        (clean exit, no None)

init app.py:            "Next steps:
                           pounce serve --app app:app    # run it
                           pounce check  --app app:app   # validate before serving
                           pounce config schema           # discover all 67 settings
                           pounce config show             # see resolved config
                           pounce info                    # diagnose your environment
                           pounce --mcp                   # talk to pounce as an MCP server
                         Troubleshooting: docs/troubleshooting.md (POUNCE_ error codes)"

/_pounce/info:          GET → {version, python, gil_enabled, worker_mode,
                                workers: {configured, alive, generation},
                                uptime_seconds, active_connections,
                                config: <redacted via INFO_ALLOWLIST>}
```

### The `_die` contract (new shape)

```python
# src/pounce/_cli.py — extend the existing helper
def _die(
    message: str,
    *,
    hint: str | None = None,
    code: str | None = None,        # NEW: POUNCE_<CATEGORY>_<SPECIFIC>
    doc: str | None = None,         # NEW: docs/troubleshooting.md#<anchor>
    diagnostics: list[str] | None = None,
) -> NoReturn: ...
```

Existing callers without code/doc continue to work (kwargs default to `None`). The `PounceError` catch site at `_cli.py:278` becomes:

```python
if isinstance(exc, PounceError):
    _die(str(exc), hint=exc.hint or _hint_for_pounce_error(exc),
         code=exc.code, doc=exc.doc)
```

---

## Sprint Structure

| Sprint | Focus | Effort | Risk | Ships Independently? |
|---|---|---|---|---|
| 0 | Design: `_die` contract, milo subcommand-help fix path, check-success rendering | 2h | Low | Yes (RFC only) |
| 1 | Plumb `code`/`doc` through every CLI error printout | 4h | Low | Yes |
| 2 | Three UX bug-fixes: `config --help` subcommand list, `check` success line, `init` `None` print | 3h | Low | Yes |
| 3 | `pounce check --config FILE` parity with `serve` | 2h | Low | Yes |
| 4 | `pounce init` template becomes a tour guide | 1h | Low | Yes |
| 5 | `/_pounce/info` introspection endpoint (deferred Sprint 4 from parent epic) | 6h | Medium | Yes |

Sprints 1–5 have no inter-dependencies. Sprint 1 is highest-ROI per hour. Sprint 5 is largest scope.

---

## Sprint 0: Design & Validate

**Goal**: Lock the three decisions that would otherwise force rework mid-implementation.

### Task 0.1 — `_die` extension contract

Decide: extend the existing `_die` with `code=`/`doc=` kwargs vs. introduce a new `_die_pounce_error` helper.

**Output**: Inline ADR (3–5 lines) in this plan's changelog. Recommend extending — single rendering path, fewer surfaces to keep in sync, kwargs are additive.

**Acceptance**: Decision recorded; `_die`'s call sites enumerated (`rg '_die\(' src/pounce/_cli.py | wc -l`) so Sprint 1 knows the migration scope.

### Task 0.2 — Milo subcommand-help repair path

Investigate why `pounce config --help` shows `_command_config` instead of the registered subcommands. Hypothesis: `_install_branded_help` recursion at `_cli.py:90-96` walks `_subparsers._actions` but the `config` group is a nested CLI built via `cli.group(...)` whose subparser dest pattern `_command_config` isn't recognized as a subcommand container.

**Output**: One-paragraph diagnosis in this plan's changelog. Either patch is in pounce (`_install_branded_help`) or in milo (the help.kida template / HelpState). Decide which side gets the fix.

**Acceptance**: Reproducer confirmed; fix locus identified (pounce vs milo). If milo, file an issue and patch pounce-side as fallback.

### Task 0.3 — `check` success rendering

Confirm whether `_output.check_results(all_passed=True, ...)` actually emits text in the all-pass case. If it renders to a Kida template, inspect that template for an empty-on-success branch.

**Output**: Confirmed root cause + the template path that needs editing. One sentence.

**Acceptance**: Run `pounce check --app app:app` (with `app.py` reachable on `sys.path`) and observe stdout. If empty, identify whether the bug is in `check_results()`, the template, or in milo capturing stdout.

---

## Sprint 1: Error Codes at the CLI Surface

**Goal**: Every CLI error printout carries `Code: POUNCE_X_Y` and a doc anchor, closing the loop on the troubleshooting catalog.

### Task 1.1 — Extend `_die` with `code` and `doc` kwargs

Add `code: str | None = None` and `doc: str | None = None` to `_die` in `src/pounce/_cli.py`. Render them on lines after the message + hint, with stable formatting (`Code: POUNCE_X_Y` and `See:  docs/troubleshooting.md#anchor`).

**Files**: `src/pounce/_cli.py` (the `_die` helper and the `_output.error` / branded-error rendering it delegates to, which lives in `src/pounce/_output.py`).

**Acceptance**:
- `_die("msg", code="POUNCE_TLS_CERT_MISSING", doc="docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING")` writes both lines to stderr.
- `_die("msg")` (no code/doc) renders identically to today — no regression in existing error formatting.
- New unit test in `tests/unit/test_cli_errors.py` captures stderr and asserts the format.

### Task 1.2 — Pass `code`/`doc` from every `PounceError` catch site

Audit every `_die(...)` and `_output.error(...)` call in `src/pounce/_cli.py` (line numbers from current HEAD: 260, 266, 273-283, 700, 726, 737, 761, 803, 810, 905, 919, 962, 1045). For sites that catch `PounceError` (or one of its subclasses), thread `code=exc.code, doc=exc.doc` through.

For sites that don't have a `PounceError` (raw `OSError`, `ValueError`, `ImportError`), introduce a small mapping or wrap into the appropriate `PounceError` subclass at the catch site so the code surfaces. Example: `_cli.py:260` catches `(ValueError, ImportError, AttributeError, TypeError)` from app import — these become `AppError(..., code="POUNCE_APP_IMPORT_FAILED")`.

**Files**: `src/pounce/_cli.py`, possibly new codes in `src/pounce/_errors.py` (none expected — categories already cover this).

**Acceptance**:
- `pounce serve --app nonexistent:app` prints `Code: POUNCE_APP_IMPORT_FAILED` (or the specific code the team picks) and a `See:` line that resolves.
- `rg '_die\(' src/pounce/_cli.py` and `rg '_output\.error\(' src/pounce/_cli.py` — every match catching a `PounceError` passes `code=`.
- New integration test invokes the CLI in a subprocess for 3 representative error paths (missing app, missing TLS cert, malformed config) and asserts each emits its `POUNCE_` code.

### Task 1.3 — Emit `code`/`doc` in JSON log mode

When `log_format=json`, ensure CLI errors include `code`, `hint`, `doc` as structured fields, not embedded in the message string. Reuses the same renderer path; no new format.

**Files**: `src/pounce/_output.py` (JSON branch of error rendering).

**Acceptance**:
- `pounce --log-format json serve --app nonexistent:app 2>&1 | jq` returns an object with `code`, `hint`, `doc` keys.
- Existing log-format tests still pass.

---

## Sprint 2: Three UX Fixes

**Goal**: The three smallest gaps with the highest first-impression cost.

### Task 2.1 — Render subcommands in `pounce config --help`

Patch `_install_branded_help` (`src/pounce/_cli.py:78-96`) or the help.kida template so command groups render their subcommands. Per Sprint 0.2 outcome, locate the exact failure (recursion misses `_command_config` group, or template lacks the subcommand block).

**Files**: `src/pounce/_cli.py` (fallback) or `src/pounce/templates/help.kida` (preferred — this is presentation).

**Acceptance**:
- `pounce config --help` lists `schema` and `show` with their descriptions, mirroring the format of top-level `pounce --help`.
- `pounce <any-other-group> --help` (none today, but built for future-proofing) renders the same way.
- No regression in top-level `pounce --help` output (snapshot test).

### Task 2.2 — `pounce check` prints success line

Per Sprint 0.3 outcome, fix the rendering path so `all_passed=True` emits a confirmation summary like:

```
✓ Config valid (12 fields resolved: 9 defaults, 3 overrides)
✓ App importable: app:app
✓ Port 8000 available on 127.0.0.1
All checks passed.
```

**Files**: `src/pounce/_output.py` (or the corresponding template under `src/pounce/templates/`).

**Acceptance**:
- `pounce check --app app:app` exits 0 *and* prints a line per check plus a final `All checks passed.`
- `pounce check` with a deliberately bad config still prints the failure list as today (no regression).
- Snapshot test for the success rendering.

### Task 2.3 — Stop printing `None` from `pounce init`

Investigate milo's CLI dispatcher: does it `print()` the return value of every command function? If so, the right fix is either (a) milo respects `None` returns silently, or (b) pounce returns an explicit empty string / sentinel.

**Files**: `src/pounce/_cli.py:949-975` (`init`) and possibly milo (file an issue + apply workaround in pounce).

**Acceptance**:
- `pounce init` in a fresh dir produces output ending with `Next: pounce serve --app app:app` and a clean newline. No `None`.
- Other commands (`info`, `check`, `bench`) reviewed for the same trailing-`None` regression and fixed if present.
- Subprocess test in `tests/unit/test_init_cli.py` asserts `b"None" not in stdout`.

---

## Sprint 3: `pounce check --config FILE` Parity

**Goal**: The same `--config` flag that `serve` accepts works on `check`. The just-scaffolded `pounce.toml` is validate-able.

### Task 3.1 — Add `config` parameter to `check`

Add `config: str | None = None` to the `check(...)` signature in `src/pounce/_cli.py:602`. Plumb it through to `load_config_with_overrides(cli_overrides, config_path=Path(config) if config else None)` at line 699.

**Files**: `src/pounce/_cli.py` only.

**Acceptance**:
- `pounce check --app app:app --config pounce.toml` resolves the TOML file and validates the merged config.
- `pounce check --app app:app` (no `--config`) still auto-detects via the existing loader behavior, identical to today.
- `pounce --llms-txt` lists `--config` under `check` parameters (auto-derived from the milo command annotation).
- Integration test: `pounce init && pounce check --app app:app --config pounce.toml` exits 0 in a fresh tempdir.

### Task 3.2 — `_CHECK_HELP` entry for `--config`

Add the `config` description to the per-command help dict (look for `_SERVE_HELP` / `_CHECK_HELP` patterns in `_cli.py:99-130`). Reuse the `_SERVE_HELP["config"]` string verbatim.

**Files**: `src/pounce/_cli.py`.

**Acceptance**:
- `pounce check --help` shows `--config` with the same description as `serve`.

---

## Sprint 4: `pounce init` Template Becomes a Tour Guide

**Goal**: A fresh agent reading the scaffolded `app.py` finds the map to the rest of pounce's surface.

### Task 4.1 — Expand `APP_TEMPLATE` with next-step signposts

Update `src/pounce/_init.APP_TEMPLATE` (currently `_init.py:23-47`) to include a richer header docstring listing the 5–6 commands the user will reach for next, plus a pointer to troubleshooting and `--mcp`. Keep the docstring tight (≤15 lines) — this is signposts, not a tutorial.

**Files**: `src/pounce/_init.py` (`APP_TEMPLATE` constant only).

**Acceptance**:
- Generated `app.py` docstring mentions: `pounce serve`, `pounce check`, `pounce config schema`, `pounce config show`, `pounce info`, `pounce --mcp`, and `docs/troubleshooting.md`.
- Generated `app.py` still parses (`python -c "import ast; ast.parse(open('app.py').read())"`).
- Existing init integration test (`tests/integration/test_init.py` if present, else `tests/unit/test_init.py`) still passes — the served app's response body is unchanged.
- Snapshot test on the template content prevents accidental regression.

---

## Sprint 5: `/_pounce/info` Introspection Endpoint

**Goal**: A running pounce instance reveals its state via an opt-in JSON endpoint. Live debugging stops requiring `tail -f | grep`.

This sprint is the deferred Sprint 4 from `docs/plans/vibe-coding-epic.md` (lines 262–284 of that doc), executed verbatim. The design ADRs in `docs/design/info-endpoint-redaction.md` and `docs/design/introspection-auth.md` define the contract.

### Task 5.1 — `/_pounce/info` endpoint behind config flag

Wire into the same path-dispatch layer as `_health.py`. New module `src/pounce/_introspect.py`. Gated by new config `introspection_enabled: bool = False`; bind-scoped by `introspection_bind: str = "127.0.0.1"`. Returns the `INFO_ALLOWLIST`-redacted config (already in `_config_schema.py`) plus live counters from lifecycle state (`workers.alive`, `uptime_seconds`, `active_connections`).

**Files**: new `src/pounce/_introspect.py`, two new fields in `src/pounce/config.py`, dispatch wiring in the request pipeline (look at `_health.py` for the pattern).

**Acceptance**:
- `introspection_enabled=False` (default): `curl http://127.0.0.1:8000/_pounce/info` → 404 (or route not registered).
- `introspection_enabled=True`: returns JSON with the contract from the parent epic line 96–107.
- `introspection_enabled=True` + bind to `0.0.0.0`: emits `WARNING` log at startup with a code (`POUNCE_CONFIG_INTROSPECTION_PUBLIC`).
- Integration test asserts no `INFO_ALLOWLIST=OMIT` field name appears in the response body.
- `make ty` clean — response shape is a `TypedDict` or frozen dataclass.

### Task 5.2 — Configurable endpoint path

Expose `introspection_path: str = "/_pounce/info"` so users can move it if it collides with their app's routes.

**Files**: `src/pounce/config.py`, `src/pounce/_introspect.py`.

**Acceptance**:
- Unit test: custom `introspection_path="/custom"` works; default returns 404.
- TOML template (`pounce config schema --output-format toml-template`) lists the new fields automatically (already wired through `dataclasses.fields`).

### Task 5.3 — Catalog the new error code

Add `POUNCE_CONFIG_INTROSPECTION_PUBLIC` (warning, not raise) to `docs/troubleshooting.md` per the existing catalog pattern. The coverage test (`tests/unit/test_troubleshooting_catalog.py`) will fail otherwise.

**Files**: `docs/troubleshooting.md`.

**Acceptance**: `make test` green.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Sprint 1 introduces a new `POUNCE_` code without a catalog entry | Medium | Low | `tests/unit/test_troubleshooting_catalog.py` already AST-walks raise sites; CI fails before merge |
| Sprint 2.1 fix lives in milo, not pounce — out-of-tree dependency on milo release | Medium | Medium | Sprint 0.2 explicitly identifies fix locus before Sprint 2 starts. If milo: ship workaround in pounce now (override `_install_branded_help` recursion to handle group containers), file milo issue, remove workaround when milo ships |
| Sprint 2.3 `None` print is a milo behavior the patch can't reach without forking | Medium | Low | Worst case: each pounce CLI command returns an empty string instead of `None`. Local fix, no milo coupling |
| Sprint 5 introspection endpoint leaks a field the allowlist missed | Low | Critical | `INFO_ALLOWLIST` is already fail-closed (Sprint 0.3 of parent epic); test enumerates `dataclasses.fields(ServerConfig)` and asserts every field is classified |
| Sprint 5 endpoint accidentally exposed publicly via misconfigured reverse proxy | Medium | High | Loopback bind by default; startup warning when binding 0.0.0.0; documented in troubleshooting catalog |
| Sprint 3 `--config` change affects `--llms-txt` workflow inference (`bench → check → config.show` workflow currently shares a different param set) | Low | Low | `--llms-txt` is auto-derived from milo annotations; verify the workflow output post-change |
| Performance regression from error-rendering changes | Very Low | Low | Error path is exceptional, not hot path. AGENTS.md invariant respected |

---

## Success Metrics

| Metric | Current | After Sprint 1 | After Sprint 3 | After Final Sprint |
|---|---|---|---|---|
| CLI errors that show their `POUNCE_` code | 0 of ~13 catch sites | 13 / 13 | 13 / 13 | 13 / 13 |
| `pounce config --help` shows subcommands | No | No | No | Yes (after Sprint 2) |
| `pounce check --config FILE` works | No | No | Yes | Yes |
| `pounce check` success line printed | No | No | Yes | Yes |
| `pounce init` clean output (no trailing `None`) | No | No | Yes | Yes |
| `pounce init` template lists ≥5 next-step commands | No | No | No | Yes (after Sprint 4) |
| `/_pounce/info` returns runtime state | No | No | No | Yes |
| Self-assessed agent-readiness audit score (parent epic baseline 97% target) | ~88% (hands-on) | ~92% | ~95% | ~98% |

The 98% ceiling reserves room for: framework-aware `init` flavors, MCP server exposing pounce introspection as a tool, executable CONTRIBUTING.md recipes in CI.

---

## Relationship to Existing Work

- **`docs/plans/vibe-coding-epic.md`** — direct predecessor. Sprints 0/1/2/3/5 of that epic shipped (per its changelog). Sprint 4 (introspection) was deferred and is rolled into Sprint 5 of *this* epic. No reopen of the parent epic; this is its successor.
- **`AGENTS.md`** — no change required. The "stop and ask" escape hatches still apply (Sprint 5 touches public API by adding two config fields; flag in PR per AGENTS.md guidance).
- **`docs/troubleshooting.md`** — Sprint 1 may surface 1–2 new codes (`POUNCE_APP_IMPORT_FAILED` is the likely one); Sprint 5 adds `POUNCE_CONFIG_INTROSPECTION_PUBLIC`. Coverage test enforces catalog inclusion.
- **`milo`** — possible upstream issue from Sprint 0.2 / Sprint 2.1 / Sprint 2.3. Plan absorbs a workaround if needed; tracks the upstream fix separately.
- **Merge / release freeze** — none known. Target 0.7.0 release alongside any parent-epic carryover.

---

## Changelog

| Date | Change | Reason |
|---|---|---|
| 2026-04-20 | Initial draft | Hands-on agent-perspective evaluation surfaced 7 last-mile UX gaps not closed by the parent epic. |
| 2026-04-20 | Sprint 0 complete — three ADRs recorded below | Investigations pinpoint each fix locus down to single lines. Sprints 1, 2.1, and 2.2 are smaller than originally scoped. |
| 2026-04-20 | Sprint 1 complete — ADR 1.1 added; `_die` + `_output.error` + `PounceError` auto-derivation shipped | Error codes now surface in both pretty and plain-text modes; `See:` line resolves to a catalog entry without any raise site having to pass `doc=`. End-to-end verified: `pounce serve --ssl-certfile /nonexistent.pem` now prints `Error:`, `Code:`, `Hint:`, and `See:` lines together. |
| 2026-04-20 | Sprint 2 complete — all three UX fixes shipped, ADR 2.1 added | (2.1) Nested subcommand groups now render correctly: the Python-side renderer and the `help.kida` template both accept `_command_<group>` dests in addition to the top-level `_command`. `pounce config --help` lists `schema` and `show`. (2.2) `pounce check` success path now writes per-check PASS/FAIL/WARN lines + `All checks passed.` to stderr via `_write`; the `logger.info` branch was dropped because the dispatcher runs before `configure_logging` installs handlers. (2.3) All 7 `@cli.command` registrations now pass `display_result=False` — milo's dispatcher no longer prints `str(None)` after each command. Subprocess test `tests/unit/test_init_cli.py` pins the invariant. |
| 2026-04-20 | Sprint 3 complete — `pounce check --config FILE` now has parity with `serve` | Added `config: str \| None = None` to the `check` signature (immediately after `app`, mirroring `serve`) and threaded `config_path=Path(config) if config else None` into `load_config_with_overrides`. Task 3.2 was automatically satisfied by the pre-existing `_CHECK_HELP = {**_SERVE_HELP}` spread — `--config` now shows up in `check --help` with the same description as `serve`. `--llms-txt` auto-derives the new param from milo annotations; no registry change. End-to-end verified: `pounce init && pounce check --config pounce.toml` exits 0 in a fresh tempdir with `[PASS] Config validation: Valid`. The plumbing is also *semantically* tested — a TOML whose `port` is already bound causes `check` to FAIL, proving the config file actually reaches the pre-flight port validator. |
| 2026-04-20 | Sprint 4 complete — `pounce init` template becomes a tour guide | Replaced `APP_TEMPLATE`'s 2-line "Run: pounce serve" header with a 12-line signpost docstring naming `serve`, `check`, `info`, `config schema`, `config show`, `--mcp`, and `docs/troubleshooting.md`. A fresh agent reading the scaffolded `app.py` finds the map to every other pounce command without leaving the file. Fenced by 5 new assertions in `TestAppTemplateSignposts`: (a) the file still parses, (b) every signpost string is present, (c) signposts live inside the module docstring (not a print), (d) docstring budget ≤15 non-blank lines, (e) the response body is byte-identical to pre-Sprint-4. No behaviour change — pure documentation-in-code. |
| 2026-04-20 | Sprint 5 complete — `/_pounce/info` introspection endpoint shipped | New module `src/pounce/_introspect.py` builds a three-section JSON payload (`runtime` / `worker` / `config`) and is dispatched from `worker.py` next to the health-check hook. Three new `ServerConfig` fields gate it: `introspection_enabled` (default `False`), `introspection_bind` (default `"127.0.0.1"`), `introspection_path` (default `"/_pounce/info"`). The redacted config view reuses the pre-existing `INFO_ALLOWLIST` (which already had pre-allocated entries for the three new fields) — fail-closed: every `REDACT_TO_BOOL` field surfaces as `<name>_set: bool`, raw secret values (`ssl_certfile`, `sentry_dsn`, `otel_endpoint`, `trusted_hosts` entries) never appear in the body. `Server._warn_if_introspection_public` emits `POUNCE_CONFIG_INTROSPECTION_PUBLIC` (catalogued in `docs/troubleshooting.md`) when the endpoint is reachable from a non-loopback interface. The catalog scanner in `tests/unit/test_troubleshooting_catalog.py` was extended to recognise `logger.warning("POUNCE_X: ...")`-prefixed emissions so the new code is automatically picked up. Verified end-to-end on a real pounce server: `curl http://127.0.0.1:8765/_pounce/info` returns the full payload; disabled mode falls through to the user app (no route registration); public-bind starts the server with a JSON-formatted warning containing the code + doc anchor. 17 new tests in `tests/unit/test_introspect.py`; full `make test` (1498 unit tests) green; ruff and ty clean. |

### Sprint 0 ADRs

#### ADR 0.1 — Extend `_die`, do not introduce a new helper

**Decision**: Extend the existing `_die(...)` in `src/pounce/_cli.py:423-433` with two additive kwargs (`code: str | None = None`, `doc: str | None = None`). No new `_die_pounce_error` helper.

**Why this is smaller than expected**: The rendering layer is *already wired*. `_output.error(...)` (`src/pounce/_output.py:70-105`) already accepts `code=` and `docs_url=`, and `templates/error.kida` already renders both:

- `error.kida:15` — `{% if code %} {{ code | yellow }}{% endif %}` next to the "error" header
- `error.kida:30-32` — `{% if docs_url %}{{ "docs:" | dim }} {{ docs_url | underline }}{% endif %}`

`_die` simply drops `code`/`doc` on the floor by not threading them through. The fix is a one-line signature change plus one-line forward to `_output.error(..., code=code, docs_url=doc)`.

**Call-site enumeration** (7 sites in `_cli.py`):
- `261, 267` — argument-parsing errors during `serve` setup (no PounceError, raw `OSError`/`ValueError`/`ImportError`)
- `278` — the `PounceError` catch site (highest-impact: this is where `exc.code`/`exc.doc` already exist and are silently dropped)
- `968` — `init` collision error (raises `InitError`, not a `PounceError` — see ADR 0.4 below)
- `1007, 1046, 1059` — `config` subcommand error paths

**Recommendation for Sprint 1.2**: prioritize site `278` (covers the `PounceError`-bearing surface end-to-end). Sites `261/267/737/761/803/810/905/919/1045` should be reviewed for whether they should be wrapping into `PounceError` subclasses — separate cleanup, not blocking.

#### ADR 0.2 — Subcommand-help bug is in pounce, one-line fix

**Diagnosis confirmed**: `_render_branded_help` at `src/pounce/_cli.py:39` hard-codes `if action.dest == "_command"`. Argparse names the subparser dest based on the parser group: top-level pounce parser uses `_command`, but `cli.group("config", ...)` builds a nested parser whose subparser dest is `_command_config` (and any future group `cli.group("X", ...)` would be `_command_X`).

The `_install_branded_help` recursion at `_cli.py:90-96` correctly walks into the `config` subparser (so its branded format_help patch is installed), but when that patched function runs, it iterates the nested parser's action groups and never matches `dest == "_command"` — the `_command_config` action is treated as a generic positional.

**Fix locus**: `src/pounce/_cli.py:39`. Change to `if (action.dest == "_command" or action.dest.startswith("_command_")) and isinstance(action.choices, dict):`. Pure pounce-side; no milo dependency. No upstream issue needed.

**Verification plan for Sprint 2.1**:
- `pounce config --help` lists `schema` and `show` with descriptions matching `--llms-txt` output.
- Snapshot test of top-level `pounce --help` ensures no regression for the `dest == "_command"` case.
- Future-proof: any new `cli.group("X", ...)` automatically gets correct subcommand rendering.

#### ADR 0.3 — `check` silence root cause: `logger.info` fires before logging is configured

**Diagnosis confirmed**: `_output.check_results` at `src/pounce/_output.py:282-301` branches on `_is_pretty() or sys.stderr.isatty() or FORCE_COLOR`. The pretty branch correctly renders `templates/check.kida` (which already handles the `all_passed=True` case at `check.kida:20-22` with `"All checks passed — ready to serve"`).

The non-pretty branch (line 292-301) routes through `logger.info(...)` per check. The `check` command does not call `configure_logging` before `check_results`, so the root logger is unconfigured and the `info`-level lines are silently dropped — even when `all_passed=True`. Result: in any non-TTY context (pipes, `> file`, CI logs, agent subprocess), `pounce check` exits cleanly with zero stdout/stderr.

**Fix locus**: `src/pounce/_output.py:292-301`. Replace `logger.info(...)` calls with `_write(...)` (the same stderr-locked writer the pretty branch uses). For the success case, also emit a final `_write("All checks passed.")` line so the non-pretty output mirrors the pretty template's final status line.

**Verification plan for Sprint 2.2**:
- `pounce check --app app:app | cat` (forces non-TTY) produces one PASS line per check + final summary, exit 0.
- `pounce check --app app:app` in a TTY still renders the pretty template (no regression).
- New unit test calls `_output.check_results(version="x", checks=[...], all_passed=True)` with a stderr capture and asserts non-empty output in both branches.

#### ADR 1.1 — Auto-derive `PounceError.doc` from `code` rather than threading `doc=` through 33 raise sites

**Decision**: In `PounceError.__init__`, default `doc` to `f"docs/troubleshooting.md#{self.code}"` when the caller omits it. Raise sites that already pass `code=` (33 of them across `_fast_h1.py`, `supervisor.py`, `sync_worker.py`, `worker.py`, `net/tls.py`, `asgi/lifespan.py`, `protocols/h1.py`) get a functioning `See:` line for free.

**Why this is correct**:

1. **The invariant is already enforced.** `tests/unit/test_troubleshooting_catalog.py` AST-walks every emitted code and asserts a `### POUNCE_X` heading exists for each. So the mapping `code → docs/troubleshooting.md#<code>` is total, not probabilistic.
2. **One change, zero raise-site churn.** The alternative — adding `doc="docs/troubleshooting.md#<code>"` to each raise site — duplicates the code in every call. 33 mechanical edits for zero information gain.
3. **Explicit override still wins.** If a caller ever needs a non-catalog anchor (e.g. a design doc link), passing `doc=` overrides the default. Test: `TestPounceError.test_doc_explicit_override_wins`.

**Files touched in Sprint 1**:

- `src/pounce/_errors.py` — one line in `PounceError.__init__`: `self.doc = doc if doc is not None else f"docs/troubleshooting.md#{self.code}"`.
- `src/pounce/_cli.py` — `_die` gained `code` and `doc` kwargs forwarded to `_output.error`; the `PounceError` catch site at line 278 now threads `code=exc.code, doc=exc.doc`.
- `src/pounce/_output.py` — plain-text branch of `error()` now renders `Code:` and `See:` lines, matching the kida template's pretty output. (Agents hit the plain-text branch almost exclusively because subprocess stderr is non-TTY.)
- `tests/unit/test_cli_errors.py` — new: 7 tests covering the `_die` contract, the `_output.error` forwarding, and the end-to-end `PounceError → _die → _output` path.
- `tests/unit/test_errors.py` — updated the `doc is None` backward-compat assertion to the new auto-derived value; added `test_doc_auto_derives_from_code` and `test_doc_explicit_override_wins`.

**Scope explicitly deferred** (per ADR 0.1 recommendation): wrapping the non-PounceError raise sites in `_cli.py` (lines 261, 267, and the `config`/`init` paths) into `PounceError` subclasses. The `app:app` import-failure path still emits `Error: ... Hint: ...` without a code — that's separate cleanup, not a Sprint 1 blocker.

#### ADR 2.1 — Local fix for `None` print uses milo's `display_result=False` flag, not a return-value sentinel

**Background**: ADR 0.4 proposed returning `""` (or `0`) from each `@cli.command` function to defuse milo's dispatcher from printing `str(None)`. Implementation-time research turned up a cleaner mechanism.

**Decision**: Every `@cli.command` (and `@config_group.command`) registration in pounce now sets `display_result=False`. This is milo's built-in kwarg (`milo/commands.py:245-256`) whose semantics exactly match what pounce wants — suppress plain-format printing of the command's return value while still flowing data through `--format json` / `--output-file` if the user asks for it. No per-function return-value change needed.

**Why this beats ADR 0.4's proposal**:

1. **Zero per-command code change.** Seven commands each get `display_result=False` in the decorator; no return statements touched.
2. **Semantically correct.** Every pounce CLI command prints its own output via `print()` (config subcommands) or `_output._write` (check, info, error paths). None of them *return data for milo to format*. Setting `display_result=False` tells milo that truthfully, instead of relying on milo's default "print whatever you get back" behavior.
3. **Leaves the `--format json` / `--output-file` door open.** If a future command does want milo-managed serialization, it can drop the flag and return structured data.
4. **No milo patch required.** The upstream behavior (str-ing `None` unconditionally in `milo/output.py:52`) is still arguably a milo bug — `str(None) == "None"` leaks through for any command using the default — but pounce doesn't need to wait on an upstream release.

**Files touched in Sprint 2**:

- `src/pounce/_cli.py` — six `@cli.command` / `@config_group.command` decorators gained `display_result=False`; the `_render_branded_help` subcommand-action detection now accepts `_command_<group>` dests in addition to `_command`.
- `src/pounce/_bench.py` — `@cli.command("bench", ...)` also gained `display_result=False`.
- `src/pounce/templates/help.kida` — lines 34 and 38 of the action-grouping loop updated to match the Python-side rule: accept any dest starting with `_command_` as a subcommand container, exclude those dests from the "regular options" bucket.
- `src/pounce/_output.py` — `check_results` non-pretty branch now writes via `_write(...)` (same stderr-locked writer the pretty branch uses) and emits `All checks passed.` when `all_passed=True`. `logger.info` calls were dropped — the `check` command runs before `configure_logging` installs handlers, so those were silently no-ops.
- `tests/unit/test_cli_help.py` — new: subprocess-driven tests for `pounce config --help` and `pounce --help` snapshot regression.
- `tests/unit/test_cli_check.py` — new `TestCheckResultsNonPretty` class (3 tests) capturing stderr via monkeypatched `sys.stderr` and asserting pass/fail/warn icons + summary line.
- `tests/unit/test_init_cli.py` — new subprocess-driven invariant: `b"None" not in stdout` across `init`, `check`, and `info`. This is a CI-grade regression fence — any future `@cli.command` that omits `display_result=False` will flunk this test.

**Scope explicitly deferred**: filing a milo upstream issue for the unconditional `str(None)` in `_format_plain`. The pounce-side fix is complete without it.

#### ADR 0.4 — `init`'s `None` print is milo dispatcher behavior; local fix is to return a non-None sentinel

**Superseded by ADR 2.1.** The implementation used milo's `display_result=False` flag rather than return-value sentinels. Keep this ADR for historical context; the actual fix is documented above.

**Diagnosis (deferred to Sprint 2.3 implementation)**: The `init` function at `_cli.py:949-975` falls off the end with implicit `None`. Other commands (`check`, `serve`) call `sys.exit(...)` before falling off. The trailing `None` is milo's CLI dispatcher printing the function's return value when not None-suppressed.

**Two viable fixes, in order of preference**:
1. **Local**: `return 0` (or `return ""`) at the end of every `@cli.command` function in `pounce`. Removes pounce's exposure to the upstream behavior. ~7 lines touched.
2. **Upstream**: file a milo issue for "do not print None returns from command functions." Apply local fix in pounce in the meantime.

**Recommendation**: Both. Land the local fix in Sprint 2.3 (returns `None`-suppression directly); file the upstream issue in parallel so a future milo upgrade lets us drop the explicit returns.
