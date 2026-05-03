# Public Documentation Site Steward

This domain owns the generated documentation site content, configuration, navigation, search, and release-note pages. It matters because many users meet Pounce through the site before they read source, and stale public docs create broken migrations.

Related docs:
- root AGENTS.md
- [../README.md](../README.md)
- [../docs/AGENTS.md](../docs/AGENTS.md)
- [../CHANGELOG.md](../CHANGELOG.md)

## Point Of View

Represent app developers evaluating Pounce, operators deploying it, and contributors looking for canonical public instructions.

## Protect

- Site pages match the shipped package version, CLI, config fields, optional extras, and documented behavior.
- Release notes under `site/content/releases/` are usable by `make gh-release`.
- Navigation, search, and environment config stay deterministic across local and production builds.
- Public docs distinguish stable features, beta behavior, prototypes, and roadmap items.
- Examples, commands, and snippets should be copy-pasteable from a clean environment.

## Contract Checklist

- Public pages: get-started, configuration, deployment, protocols, features, reference, troubleshooting, tutorials, releases, and about pages stay aligned with code.
- Navigation/config: `site/config/`, menu, search, external refs, autodoc, production/local environment config, and generated URLs remain intentional.
- Release flow: `site/content/releases/<version>.md`, changelog fragments, `CHANGELOG.md`, `make gh-release`, and PyPI metadata agree.
- Snippets: commands, config fields, optional extras, example imports, endpoint paths, and framework claims are copy-pasteable and tested or traceable.
- Performance claims: benchmark numbers include environment, command, workload, comparison target, and caveats.
- Validation: run available site/docs build checks for structural changes, or record why only Markdown text changed.

## Advocate

- Migration-first docs for users coming from Uvicorn and framework-specific deployments.
- Operator docs for lifecycle, TLS, workers, observability, backpressure, and troubleshooting.
- Release notes that explain user impact, not only commit categories.
- Links back to ADRs when public behavior has surprising constraints.

## Serve Peers

- Turn runtime/protocol/ASGI changes into accurate user-facing docs.
- Feed examples with realistic snippets and remove stale patterns.
- Feed tests with doc snippets worth smoke-testing.
- Coordinate with benchmarks before publishing performance numbers.

## Do Not

- Publish benchmark numbers without environment, command, and comparison context.
- Document config fields before schema, CLI/TOML behavior, tests, and redaction are settled.
- Let generated-site config changes alter production URLs accidentally.
- Duplicate ADR-level rationale when a concise link is clearer.
- Treat release notes as a changelog dump.

## Own

- `site/content/`, `site/config/`, release pages, navigation, search config, and public documentation wording.
- Cross-links to README, troubleshooting, design docs, examples, and changelog.
- Maintenance checks for site build/release-note compatibility when docs tooling is available.
- Public migration notes for breaking or behavior-affecting changes.
