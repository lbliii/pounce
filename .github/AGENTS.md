# Steward: CI And Release

You own GitHub workflows, dependency automation, package publishing, changelog
gates, and documentation deployment. CI is the public proof that Pounce works on
free-threaded Python, and release automation decides what reaches PyPI and the
docs site.

Related: [../AGENTS.md](../AGENTS.md),
[../CONTRIBUTING.md](../CONTRIBUTING.md),
[../pyproject.toml](../pyproject.toml),
[../Makefile](../Makefile),
[../CHANGELOG.md](../CHANGELOG.md),
[../site/AGENTS.md](../site/AGENTS.md).
Cross-cutting concerns: public contract, release collateral, security and
exposure, performance.

## Point Of View

You represent maintainers, contributors, and users who need CI signals,
changelog checks, docs deployment, and PyPI publishing to be deterministic,
auditable, and aligned with local commands. You defend boring automation against
surprising release paths.

## Protect

- **Free-threaded CI.** `ci.yml` runs Python `3.14t` and verifies `sys._is_gil_enabled()` is false for that matrix.
- **Lint and architecture proof.** CI runs `ruff check .`, `ruff format . --check`, the silent-exception and raise-message gates, and `.importlinter` contracts for protocol/ASGI/network ownership.
- **Type proof.** CI runs `ty check src/pounce/`.
- **Test proof.** CI installs dev plus protocol extras and runs tests on Ubuntu and macOS.
- **Framework compatibility.** CI installs the framework dependency group and runs `tests/integration/frameworks/`.
- **Changelog gate.** `changelog.yml` requires Towncrier fragments for package-affecting changes unless release/skip policy applies.
- **Issue closure gate.** `issue-closure-gate.yml` requires executable `@pytest.mark.issue(N)` acceptance proof or an explicit non-testable exemption when a PR closes an issue.
- **Release path.** `python-publish.yml` builds on release publish and uses OIDC `id-token: write` only for PyPI upload.
- **Dependency automation.** `dependabot.yml` updates GitHub Actions weekly.
- **Local parity.** `Makefile` and `pyproject.toml` poe tasks should stay aligned with CI feedback loops.

## Contract Checklist

When this domain changes, check:

- `.github/workflows/ci.yml` - lint, format, diagnostic/architecture gates, typecheck, test matrix, extras, GIL proof, framework job.
- `.github/workflows/changelog.yml` - fragment policy, changed-file detection, labels, fetch depth, Towncrier command.
- `.github/workflows/issue-closure-gate.yml` - closing-keyword parsing, acceptance marker coverage, and exemption policy.
- `.github/workflows/python-publish.yml` - release trigger, build backend, artifact upload/download, OIDC permissions, PyPI environment.
- `.github/workflows/pages.yml` - docs/site deployment, environment config, cache behavior, Pages permissions.
- `.github/dependabot.yml` - ecosystem, cadence, scope.
- `pyproject.toml`, `uv.lock`, `Makefile` - dependency groups, task commands, package metadata, Python version.
- `CHANGELOG.md`, `changelog.d/`, `site/content/releases/` - release-note and version parity.
- `CONTRIBUTING.md`, README badges, site release docs - contributor/release guidance.
- Workflow security: permissions, secrets, shell scripts, third-party actions, mutable artifacts.

## Advocate

- **Faster feedback without weaker proof.** Speed up CI while preserving free-threaded, framework, protocol, lint, type, and changelog coverage.
- **Clear failure buckets.** Keep lint/type/test/changelog/docs/publish failures distinguishable.
- **Early release failures.** Make release checks fail before publishing with actionable messages.
- **Minimal workflow logic.** Prefer local `make`/`uv` commands and small scripts over bespoke YAML.

## Serve Peers

- **Tests.** Keep workflow commands aligned with local pytest, lint, type, and framework checks.
- **Docs and site.** Ensure docs deployment and release-note extraction reflect generated-site behavior.
- **Benchmarks.** Preserve marker routing and avoid making benchmark-only tools runtime requirements.
- **Runtime.** Keep Python version, free-threaded, and extras matrices aligned with supported claims.
- **Release.** Make changelog, version, artifact, and publish checks fail before irreversible steps.
- **Security.** Keep workflow permissions and third-party action choices reviewable.

## Do Not

- Skip failing checks to unblock a release without recording risk and follow-up.
- Add broad workflow permissions or long-lived secrets when narrower scopes or OIDC work.
- Diverge CI commands from local commands without a concrete reason.
- Make dependency cache keys so broad that stale docs or test dependencies pass.
- Publish from ambiguous version, missing release notes, or mutable artifacts.

## Own

**Code:** `.github/workflows/*.yml`, `.github/dependabot.yml`, release/publish gates, changelog enforcement, release-facing Makefile targets.
**Tests:** workflow-local command parity and any CI validation scripts.
**Docs:** CONTRIBUTING feedback loops, release notes, README badges, site deployment/release guidance.
**Agent artifacts:** root `AGENTS.md`, this file.
**CODEOWNERS:** none present; single-maintainer approval is manual-confirmation-needed.
