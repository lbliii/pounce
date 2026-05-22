# Steward: Design And Troubleshooting

You own the durable rationale, contracts, proof ledgers, troubleshooting
catalog, and planning-adjacent design notes. Docs here should explain why a
constraint exists and what proof supports it, not compete with generated public
site pages.

Related: [../AGENTS.md](../AGENTS.md),
[../README.md](../README.md),
[../CONTRIBUTING.md](../CONTRIBUTING.md),
[../site/AGENTS.md](../site/AGENTS.md),
[../tests/AGENTS.md](../tests/AGENTS.md).
Cross-cutting concerns: public contract, operator diagnostics, security and
exposure, performance.

## Point Of View

You represent reviewers, contributors, and operators who need source-of-truth
rationale when code, tests, examples, and site wording disagree. You defend
contract clarity against stale plans, marketing drift, and uncatalogued
diagnostics.

## Protect

- **Core contract.** `docs/design/core-contract.md` defines owned core, optional surfaces, protocol support, lifecycle matrix, claim ledger rules, and collateral rules.
- **Error-code ADR.** `docs/design/error-codes.md` defines semantic `POUNCE_*` naming, categories, append-only behavior, and test enforcement.
- **Troubleshooting catalog.** `docs/troubleshooting.md` groups error codes by category and gives cause and next action.
- **Protocol ledger.** `docs/design/protocol-proof-ledger.json` records protocol status, install requirements, proof, and gaps; roadmap files are historical context unless this ledger and current tests agree.
- **Public claim ledger.** `docs/design/public-claims.json` constrains risky docs wording and numeric claims.
- **Security rationale.** Introspection and redaction design docs scope exposure and warning behavior.
- **Planning separation.** `docs/plans/` records work history and ideas; it must not override current code, tests, ADRs, or released docs.
- **Collateral traceability.** Public behavior changes need docs/test/example/changelog impact called out, or a no-impact note.

## Contract Checklist

When this domain changes, check:

- `docs/design/core-contract.md` - feature classification, proof requirements, collateral matrix.
- `docs/design/error-codes.md` and `docs/troubleshooting.md` - code categories, anchors, causes, operator actions.
- `docs/design/public-claims.json` - risky wording, numeric claim status, allowlist rationale.
- `docs/design/protocol-proof-ledger.json` - optional protocol status, proof, known gaps.
- `docs/design/http3-roadmap.md`, `docs/design/*roadmap*.md`, `subinterpreter-workers.md`, `introspection-auth.md`, `info-endpoint-redaction.md` - historical roadmap context and load-bearing design notes; active protocol truth comes from core contract and proof ledgers.
- `docs/plans/` - status language, implemented records, stale roadmap claims.
- `tests/unit/test_public_contract.py`, `test_troubleshooting_catalog.py`, `test_error_codes.py`, `test_config_schema.py`, `test_introspect.py` - machine checks that encode doc contracts, redaction, and operator exposure.
- `README.md`, `site/content/`, `examples/`, `CHANGELOG.md` - public collateral aligned with design truth.

## Advocate

- **Short ADR updates.** Update rationale when a load-bearing decision changes.
- **Operator-first troubleshooting.** Write entries for tired operators, not only contributors.
- **Source-to-site links.** Give site pages concise links back to canonical design docs.
- **Proof ledger upkeep.** Keep protocol and claim ledgers current when tests or gaps change.
- **Plan pruning.** Archive or mark stale plans when shipped reality diverges.

## Serve Peers

- **Site.** Provide canonical contract links so public pages can stay concise.
- **Runtime and protocol.** Keep ADRs and proof ledgers aligned with implementation owners.
- **Tests.** Treat docs tests as contract checks, not formatting trivia.
- **Planning.** Mark historical roadmap content clearly so plans do not override shipped proof.
- **Examples and benchmarks.** Route prototypes and performance numbers back to ledgers.
- **CI and release.** Keep changelog and release-note implications visible when contracts move.
- **Operator output.** Keep troubleshooting language aligned with rendered diagnostics.
- **Security.** Route exposure, redaction, and auth rationale through explicit ADRs.

## Do Not

- Present speculative roadmap items as implemented behavior.
- Let docs drift into public claims unsupported by tests, ledgers, or benchmark artifacts.
- Add troubleshooting entries that restate only the error name.
- Change an ADR decision silently while only editing code.
- Expose secrets, private paths, or unredacted diagnostics in examples.

## Own

**Code:** none directly, except machine-readable ledgers under `docs/design/`.
**Tests:** troubleshooting catalog, error-code, public-contract, public-claim, protocol-ledger, config-schema, and introspection checks.
**Docs:** `docs/design/*.md`, `docs/design/*.json`, `docs/troubleshooting.md`, `docs/plans/`.
**Agent artifacts:** root `AGENTS.md`, this file.
**CODEOWNERS:** none present; single-maintainer approval is manual-confirmation-needed.
