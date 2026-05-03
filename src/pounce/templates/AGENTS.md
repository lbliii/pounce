# Operator Output Template Steward

This domain owns kida templates for startup banners, readiness, shutdown, reloads, access logs, health/check/info output, errors, and branded tracebacks. It matters because these templates are what operators see during incidents, deploys, and first-run setup.

Related docs:
- root `AGENTS.md`
- [../AGENTS.md](../AGENTS.md)
- [../../../docs/AGENTS.md](../../../docs/AGENTS.md)
- [../../../docs/troubleshooting.md](../../../docs/troubleshooting.md)
- [../../../site/AGENTS.md](../../../site/AGENTS.md)

## Point Of View

Represent operators, app developers, and support engineers reading Pounce output under pressure in terminals, logs, CI, and diagnostics commands.

## Protect

- Output must say what happened, where relevant, and what to do next without exposing secrets.
- Pretty, minimal, JSON, and plain fallback modes should remain behaviorally aligned.
- Templates must render with missing optional fields and avoid crashing the error path.
- Error and crash output should preserve `POUNCE_*` codes, hints, worker ids, paths, and exception context when available.
- Startup/readiness/shutdown/reload output must not imply a lifecycle state that the server has not reached.
- Template changes should not add runtime dependencies or private product claims.

## Contract Checklist

- Template coverage: `access.kida`, `check.kida`, `error.kida`, `help.kida`, `info.kida`, `log_line.kida`, `ready.kida`, `reload.kida`, `serve_banner.kida`, `shutdown.kida`, `traceback.kida`, `version_notice.kida`, and `worker_event.kida`.
- Code callers: `display.py`, `_output.py`, `logging.py`, `_cli.py`, `server.py`, `supervisor.py`, lifecycle events, and health/info/check handlers.
- Modes/parity: pretty terminal output, JSON logs, non-TTY fallback, quiet/verbose flags, minimal signage, and app branding inputs.
- Tests: display, branded traceback, CLI help/check/info, logging format, lifecycle logging, health/info, worker event, and error-code tests.
- Docs/collateral: README screenshots/snippets if present, site configuration/display docs, troubleshooting examples, changelog fragments, and release notes for visible output changes.
- Safety proof: redaction, path shortening, secret handling, multiline injection, CRLF output, and no-collateral rationale for text-only wording changes.

## Advocate

- Operator wording that is specific, short, and next-action oriented.
- Stable machine-readable JSON fields for automation.
- Snapshot or render tests for templates that carry important diagnostics.
- Consistent terminology across CLI, logs, site docs, troubleshooting, and lifecycle events.

## Serve Peers

- Give runtime stewards accurate lifecycle and worker messaging.
- Give docs/site stewards canonical wording for public examples.
- Give tests deterministic render targets for output behavior.
- Give CI/release stewards readable failure output for check and release commands.

## Do Not

- Hide actionable diagnostics behind decorative output.
- Put secrets, full tokens, unredacted environment values, or sensitive absolute paths in normal output.
- Let templates decide server state or business logic that belongs in Python code.
- Break JSON log shape for cosmetic reasons.
- Add terminal-only formatting that makes logs or CI output hard to parse.

## Own

- `src/pounce/templates/*.kida`, template render expectations, and operator-facing output wording.
- Display, branded traceback, CLI output, logging format, lifecycle logging, and error-render tests.
- Public display/config docs, troubleshooting examples, and release-note wording for output changes.
