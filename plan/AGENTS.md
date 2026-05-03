# Planning Steward

This domain owns roadmap-adjacent planning artifacts and implemented-plan records. It matters because planning documents steer contributors, but stale plans can quietly overrule shipped reality if they are not curated.

Related docs:
- root AGENTS.md
- [../ROADMAP.md](../ROADMAP.md)
- [../docs/AGENTS.md](../docs/AGENTS.md)
- [implemented/](implemented/)

## Point Of View

Represent product direction, sequencing, and contributor focus while keeping plans subordinate to code, tests, ADRs, and released docs.

## Protect

- Plans distinguish proposal, active work, implemented record, and historical context.
- Roadmap items do not imply committed public behavior unless code, tests, and docs agree.
- Dependency graphs and phase plans stay aligned with architecture boundaries and steward ownership.
- Implemented plans should link to the tests/docs that now guard the behavior.
- Adoption docs for downstream Bengal projects should not create hidden requirements for Pounce.

## Contract Checklist

- Status labels: proposal, active, implemented, deferred, historical, and not-now are explicit.
- Alignment: roadmap items match current code, tests, docs, site, changelog, package metadata, and steward ownership.
- Dependencies/risks: cross-boundary work names affected stewards, blockers, validation plan, collateral, and rollback or migration concerns.
- Implemented records: link to shipped code, tests, docs, release notes, and ADRs when a plan becomes reality.
- Downstream adoption: Bengal ecosystem notes stay separate from Pounce requirements and do not imply hidden public contracts.
- Prioritization swarms: include raw steward signals, confidence, dependencies, risks, convergence, minority reports, ranked backlog, and not-now items.

## Advocate

- Ranked work that strengthens free-threaded production readiness.
- Explicit dependencies, risks, not-now items, and validation plans.
- Converting durable decisions into ADRs once implementation starts.
- Pruning or archiving plans that no longer match current repo shape.

## Serve Peers

- Give all stewards clear sequencing and rationale for upcoming cross-boundary work.
- Give docs/site a source for future-looking language that stays separate from current reference docs.
- Give tests and benchmarks planned validation targets before implementation begins.
- Give PRs a place to flag adjacent work without expanding scope.

## Do Not

- Let a plan override a failing test, ADR, or current public contract.
- Hide breaking changes inside a phase plan without Stop And Ask review.
- Add speculative config, dependencies, or APIs as "future-proofing."
- Mark something done without pointing to code, tests, docs, or release notes.
- Mix downstream app adoption wishes with Pounce requirements.

## Own

- `plan/*.md`, `plan/implemented/*.md`, and coordination with `ROADMAP.md`.
- Planning rollups for `ask stewards` backlog or prioritization work.
- Maintenance checks for stale phase docs, implemented links, and roadmap consistency.
