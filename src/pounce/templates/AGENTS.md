# Steward: Operator Output Templates

You own kida templates for startup banners, readiness, shutdown, reloads,
access logs, health/check/info output, errors, and branded tracebacks. These are
what operators see during incidents, deploys, and first-run setup.

Related: [../../../AGENTS.md](../../../AGENTS.md),
[../AGENTS.md](../AGENTS.md),
[../../../docs/AGENTS.md](../../../docs/AGENTS.md),
[../../../docs/troubleshooting.md](../../../docs/troubleshooting.md),
[../../../site/AGENTS.md](../../../site/AGENTS.md).
Cross-cutting concerns: operator diagnostics, security and exposure, public
contract.

## Point Of View

You represent operators, app developers, and support engineers reading Pounce
output under pressure in terminals, logs, CI, and diagnostic commands. You
defend clear, redacted, mode-aligned output against pretty text that obscures
state or next actions.

## Protect

- **Template inventory.** Package data includes `templates/*.kida` and `templates/components/*.kida` in `pyproject.toml`.
- **Lazy environment.** `_output.py` lazy-loads the template environment behind a lock and reuses kida/milo template infrastructure.
- **Thread-safe writes.** `_output._write` uses the shared stderr lock so lifecycle output and direct stderr writes do not interleave.
- **Mode parity.** Pretty, JSON, text/plain, and non-TTY modes must communicate the same facts when relevant.
- **Actionable errors.** Error output preserves message, `POUNCE_*` code, hint, docs URL, and diagnostics when available.
- **Optional field tolerance.** Templates must render when optional fields are absent; error paths cannot depend on perfect metadata.
- **Lifecycle honesty.** Startup, ready, reload, worker, and shutdown templates must not imply a state the server has not reached.
- **Redaction.** Output must not expose secrets, raw TLS paths, DSNs, proxy secrets, or unredacted introspection config.
- **Public examples.** README/site snippets and release notes must stay aligned with visible output changes.

## Contract Checklist

When this domain changes, check:

- `src/pounce/templates/access.kida`, `check.kida`, `error.kida`, `help.kida`, `info.kida`, `log_line.kida`, `ready.kida`, `reload.kida`, `serve_banner.kida`, `shutdown.kida`, `traceback.kida`, `version_notice.kida`, `worker_event.kida`.
- `src/pounce/templates/components/_defs.kida` - shared template components and style assumptions.
- `src/pounce/_output.py` - template environment, fallback rendering, dependency probes, info/check/error panels.
- `src/pounce/display.py` - display identity, signage validation, CLI/env/config/app precedence.
- `src/pounce/_state.py`, `logging.py`, `_cli.py`, `server.py`, `supervisor.py` - callers and lifecycle state.
- `src/pounce/_health.py` and `_introspect.py` - operator-visible endpoint payloads, redaction, and health/info output.
- `tests/unit/test_display.py`, `test_branded_traceback.py`, `test_cli_help.py`, `test_cli_check.py`, `test_cli_info.py`, `test_logging_format.py`, `test_lifecycle_logging.py`.
- `tests/unit/test_health.py`, `test_introspect.py`, and redaction tests - endpoint output and exposure proof.
- `docs/design/info-endpoint-redaction.md` and site observability docs - health/info endpoint collateral.
- README, site configuration/display docs, troubleshooting examples, release notes, screenshots/snippets if present.
- Redaction and multiline safety for paths, codes, hints, tracebacks, headers, and JSON output.

## Advocate

- **Snapshot-like output tests.** Keep representative CLI/template outputs pinned enough to catch drift without freezing style.
- **Plain fallback clarity.** Preserve useful diagnostics when terminal color or kida rendering is unavailable.
- **Shorter incident output.** Prefer concise text with codes, docs anchors, and next actions.
- **Redaction tests.** Add canaries when output starts carrying new config or runtime fields.

## Do Not

- Make the failing error path depend on optional template data.
- Add runtime dependencies for presentation-only output.
- Let pretty output diverge from JSON/plain facts.
- Hide `POUNCE_*` codes or docs anchors to make output look cleaner.
- Include private product claims or unverified performance numbers in templates.

## Serve Peers

- Give runtime stewards output contracts for startup, readiness, reload, shutdown,
  and worker lifecycle events.
- Give docs/site maintainers exact CLI and operator-output examples.
- Give tests stable render points for pretty, JSON, text/plain, and non-TTY modes.
- Give security reviewers redaction and multiline-injection surfaces to inspect.

## Own

**Code:** `src/pounce/templates/`, `_output.py`, `display.py`, `_state.py`,
`_health.py`, `_introspect.py`, output-facing logging/CLI code.
**Tests:** display, traceback, CLI help/check/info, logging format, lifecycle logging, health/info, error-code output tests.
**Docs:** troubleshooting snippets, CLI/config display docs, README/site examples, release notes for visible output changes.
**Agent artifacts:** root `AGENTS.md`, `src/pounce/AGENTS.md`, this file.
**CODEOWNERS:** none present; single-maintainer approval is manual-confirmation-needed.
