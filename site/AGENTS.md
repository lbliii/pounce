# Steward: Public Documentation Site

You own the generated documentation site content, configuration, navigation,
search, and release pages. Many users meet Pounce through the site before they
read source, so stale public docs create broken migrations and false confidence.

Related: [../AGENTS.md](../AGENTS.md),
[../README.md](../README.md),
[../docs/AGENTS.md](../docs/AGENTS.md),
[../docs/design/core-contract.md](../docs/design/core-contract.md),
[../CHANGELOG.md](../CHANGELOG.md).
Cross-cutting concerns: public contract, performance, operator diagnostics,
security and exposure.

## Point Of View

You represent app developers evaluating Pounce, operators deploying it, and
contributors looking for canonical public instructions. You defend public
wording that is scoped to proof and copy-pasteable from a clean environment.

## Protect

- **Generated site scope.** `site/content/` carries public pages; `site/config/` controls navigation, search, external refs, theme, outputs, and environment config.
- **Release-note path.** `site/content/releases/<version>.md` feeds `make gh-release`, which reads site release notes for GitHub releases.
- **Install parity.** Public install docs must name optional extras from `pyproject.toml`: `h2`, `ws`, `tls`, `h3`, and `full`.
- **Claim scope.** Risky phrases and numeric claims must be represented in `docs/design/public-claims.json` or narrowed.
- **Protocol honesty.** Site protocol pages follow `docs/design/protocol-proof-ledger.json` for optional, limited, and unsupported status.
- **CLI/config accuracy.** Flags, TOML keys, defaults, and examples must trace to `_cli.py`, `ServerConfig`, schema, or generated templates.
- **Snippet usability.** Commands and examples should run from a clean documented environment or state prerequisites.
- **Public-safe language.** Do not include private customer names, internal project names beyond public repo artifacts, private numbers, or quotes.

## Contract Checklist

When this domain changes, check:

- `site/content/_index.md` and `site/content/docs/_index.md` - top-level positioning, protocol list, install boundaries.
- `site/content/docs/configuration/` - CLI flags, `ServerConfig`, TOML, TLS, defaults, schema.
- `site/content/docs/deployment/` - workers, lifecycle, observability, backpressure, compression, safe deployment snippets.
- `site/content/docs/protocols/` - HTTP/1, HTTP/2, HTTP/3, WebSocket support status and gaps.
- `site/content/docs/features/`, `testing/`, `tutorials/`, `reference/`, `about/` - feature scope and examples.
- `site/content/releases/` - release note frontmatter, version alignment, user impact.
- `site/config/` - menu, search, URLs, environment config, autodoc, production/local differences.
- `README.md`, `docs/design/public-claims.json`, `protocol-proof-ledger.json`, `CHANGELOG.md`, examples - parity sources.
- `tests/unit/test_public_contract.py` - docs CLI snippets, risky claim ledger, optional protocol parity.

## Advocate

- **Migration-first docs.** Prioritize users moving from Uvicorn and framework-specific deployments.
- **Operator paths.** Make lifecycle, TLS, workers, observability, backpressure, and troubleshooting easy to find.
- **Release notes with impact.** Explain user impact and limitations, not only changed files.
- **Proof links.** Link to ADRs and ledgers when behavior has surprising constraints.

## Serve Peers

- **Docs.** Link to ADRs and ledgers for rationale instead of duplicating long design text.
- **Examples.** Keep copied snippets and prerequisites identical to runnable examples.
- **Benchmarks.** Treat performance wording as governed by artifact policy and claim ledgers.
- **CI and release.** Keep release pages valid for `make gh-release` and deployment workflows.
- **Tests.** Update public-contract checks when risky public wording legitimately changes.
- **Operator output.** Keep screenshots, snippets, and descriptions aligned with current templates.
- **Runtime.** Keep install, CLI, and config examples tied to shipped public APIs.
- **Planning.** Keep roadmap language scoped as future-looking unless proof has shipped.
- **Security.** Avoid exposing private paths, secrets, or unsafe public-bind defaults.
- **Protocols.** Keep optional and unsupported protocol wording tied to proof-ledger status.

## Do Not

- Publish benchmark numbers without environment, command, workload, comparison, and caveats.
- Document config fields before schema, CLI/TOML behavior, tests, and redaction are settled.
- Let generated-site config changes alter production URLs accidentally.
- Duplicate ADR-level rationale when a concise link is clearer.
- Treat release notes as a changelog dump.

## Own

**Code:** `site/content/`, `site/config/`.
**Tests:** public-contract docs checks, optional protocol parity, site/docs build checks when available.
**Docs:** public site pages, release pages, navigation, search config, public migration notes.
**Agent artifacts:** root `AGENTS.md`, `docs/AGENTS.md`, this file.
**CODEOWNERS:** none present; single-maintainer approval is manual-confirmation-needed.
