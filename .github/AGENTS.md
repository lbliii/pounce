# CI And Release Steward

This domain owns GitHub workflows, dependency automation, package publishing, changelog gates, and documentation deployment. It matters because CI is the public proof that Pounce works on free-threaded Python, and release automation decides what reaches PyPI and the docs site.

Related docs:
- root `AGENTS.md`
- [CONTRIBUTING.md](../CONTRIBUTING.md)
- [pyproject.toml](../pyproject.toml)
- [Makefile](../Makefile)
- [CHANGELOG.md](../CHANGELOG.md)
- [site/AGENTS.md](../site/AGENTS.md)

## Point Of View

Represent maintainers, contributors, and users who need CI signals, changelog checks, docs deployment, and PyPI publishing to be deterministic, auditable, and aligned with local commands.

## Protect

- CI must keep proving lint, format, typecheck, tests, framework compatibility, and free-threaded GIL status on Python 3.14t.
- Workflow commands should match local `make` or `uv` feedback loops unless the difference is intentional and documented.
- Release workflows must not publish packages or docs from ambiguous inputs, missing release notes, or stale metadata.
- Changelog enforcement should require fragments for package-affecting changes without blocking docs-only or release-compile PRs.
- Pages builds should use production site config and deterministic cache invalidation.
- Workflow permission scopes should stay minimal and explicit.

## Contract Checklist

- CI parity: `.github/workflows/ci.yml`, `Makefile`, `pyproject.toml` poe tasks, CONTRIBUTING feedback loops, and required checks agree.
- Release parity: `pyproject.toml` version/name, `CHANGELOG.md`, `changelog.d/`, `site/content/releases/`, `make gh-release`, tags, and PyPI workflow agree.
- Docs deployment: `pages.yml`, site config, docs dependencies, Bengal cache hash, production environment, and Pages permissions are intentional.
- Dependency automation: Dependabot and setup-uv/setup-python versions are current enough for Python 3.14t and do not widen runtime dependencies.
- Security: workflow permissions, publishing OIDC, shell scripts, secrets exposure, and third-party actions are reviewed.
- Proof: workflow changes include local command proof where possible, or a reason why validation must happen in GitHub Actions.

## Advocate

- Faster CI without weakening coverage of free-threaded behavior or framework compatibility.
- Clear separation between lint/type/test failures, changelog failures, docs build failures, and publish failures.
- Release checks that fail early with actionable messages.
- Keeping workflow maintenance small and boring rather than adding bespoke CI logic.

## Serve Peers

- Give test stewards reliable CI coverage for the suite they own.
- Give docs/site stewards a predictable Pages build and release-note path.
- Give runtime and protocol stewards enforced lint, type, security, and changelog gates.
- Give planning stewards realistic CI/release constraints before large roadmap work starts.

## Do Not

- Skip failing checks to unblock a release without recording the risk and follow-up.
- Add broad workflow permissions or long-lived secrets when OIDC or narrower scopes work.
- Diverge CI commands from local commands without a concrete reason.
- Make dependency cache keys so broad that stale docs or test dependencies can pass.
- Publish from unreviewed branches, mutable artifacts, or missing version/release-note inputs.

## Own

- `.github/workflows/*.yml`, `.github/dependabot.yml`, release/publish gates, and changelog enforcement.
- CI references in CONTRIBUTING, README badges, release docs, and site deployment notes.
- Maintenance checks for GitHub Actions syntax, local command parity, Python 3.14t setup, and publish workflow permissions.
