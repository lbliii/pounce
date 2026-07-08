# Stack Hygiene Baseline

**Status:** Adopted 2026-07-08 · **Scope:** all b-stack repos (pounce, kida, patitas, rosettes, zoomies, chirp, bengal, milo-cli, purr; apps: furatena, elbysodic)

Every practice below already exists somewhere in the stack — this baseline is the union of the stack's own best practices, each named for the repo that originated it or implements it best. The rule: **adopt the best implementation everywhere it applies.** Each repo carries a "Hygiene baseline alignment" epic whose checklist is its gap rows from this document (epic index at the bottom).

This doc lives in pounce because pounce anchors the stack's claims/proof culture (see `public-claims.json`, `protocol-proof-ledger.json`, `core-contract.md`), but it governs all repos equally — including pounce (pounce#264).

---

## Tier 1 — Table stakes (every repo, no exceptions)

| # | Practice | Originator / best implementation |
|---|----------|----------------------------------|
| 1 | Cloud CI on push/PR: ruff check + format, ty, tests | all repos with CI; gaps: purr, elbysodic |
| 2 | Dual-interpreter matrix: 3.14 (GIL) **and** 3.14t lanes | pounce; every other lib is 3.14t-only. The free-threading advantage claim needs its control group, and most users are still GIL-on |
| 3 | Hard coverage gate in CI — enforced, not just configured | milo (`--cov-fail-under=80` in CI). For repos below 80: patitas' **documented honest ratchet** (a floor set at measured coverage, with rationale and a target) |
| 4 | ty posture: strict with a zero-`type: ignore` goal, or a documented floor + downward ratchet | chirp (strict, zero-ignore goal); bengal's diagnostic-ceiling is the acceptable gradual pattern *if* the ceiling has a schedule |
| 5 | Changelog fragments (towncrier / `changelog.d/`) + CI fragment gate | kida, chirp, bengal, milo, elbysodic |
| 6 | Release automation with a pre-publish gate | kida (release-gate job before PyPI publish); furatena (tag-is-ancestor-of-main validation + gates before build) |
| 7 | Governance docs: CONTRIBUTING, SECURITY.md, AGENTS.md/CLAUDE.md | pounce (CONTRIBUTING with executable recipes), chirp (full set) |
| 8 | Issue taxonomy: saga/epic/task + priority + type + theme | pounce's scheme is canonical (`saga`/`epic`/`task`, `priority/P0–P3`, `type/*`, `theme/*`). Repos with established local theme vocabularies (patitas, zoomies, furatena, elbysodic) keep them but align the structural trio + priority naming so cross-repo queries work |

## Tier 2 — Verification (adopt from the originator where applicable)

| # | Practice | Originator |
|---|----------|------------|
| 9 | Benchmark regression gating: committed baselines + per-PR comparison | kida (regression gate + committed baselines); milo (per-PR comparison comment with regression flags); bengal (perf-gate with honest hardware caveats) |
| 10 | Silent-exception CI gate + raise-message linter — every raise carries the offending value, the expectation, and the governing spec citation | zoomies (`scripts/lint_raise_messages.py` + S110/S112 + a test enforcing the linter). The cheapest, highest-value port in the stack |
| 11 | Thread-safety/concurrency CI lane (a dedicated job, not just markers), plus a runtime GIL-off assertion wherever free-threading is claimed | kida (thread-safety job); chirp (`data-pg-gil-gate` fails if the GIL re-enables); milo (runtime `sys._is_gil_enabled()` assert in CI) |
| 12 | Property-based testing actually used, not just declared | kida (five property suites + sandbox fuzz), patitas, zoomies (QPACK), rosettes (lexer invariants) |
| 13 | Golden/corpus testing | rosettes (55-language token fixtures + a CI job policing fixture coverage); bengal (~50 fixture sites + render-output regression) |
| 14 | Differential oracle vs incumbents | patitas (differential fuzz against markdown-it-py *and* mistune). Applies to: kida vs Jinja2, rosettes vs Pygments, zoomies vs aioquic |
| 15 | Mutation testing (weekly) | bengal (mutmut, 70% target). Natural next adopters: kida, patitas (parser/compiler cores) |
| 16 | Architecture contracts (import-linter) | bengal (`.importlinter` layered contracts). Applies to: chirp, pounce |
| 17 | One-command self-verification verb | milo (`milo verify` — 10-check dual-surface conformance incl. a subprocess JSON-RPC handshake); chirp (`chirp check --deploy`, 70+ rules + PR contract-diff gate); pounce (`pounce check`); bengal (health validators) |
| 18 | Issue↔test acceptance linkage | chirp (`@pytest.mark.issue(N)` + PR closure gate + weekly backlog reconciliation) |
| 19 | Multi-lane CI with per-module coverage ratchets | furatena (fast/contract/coverage-ratchet/agent lanes; `check_core_coverage.py`). The pattern for large suites (bengal, chirp) |

## Tier 3 — Evidence & claims (the pounce#227 pattern)

| # | Practice | Originator |
|---|----------|------------|
| 20 | Machine-checkable claims ledger (`public-claims.json`) — README claims cite ledger entries | pounce |
| 21 | Benchmark artifact schema + per-release published artifacts | pounce (schema + runner); bengal (committed baseline JSONs, including honest losing numbers); milo (dated, platform-stamped BASELINE.md) |
| 22 | Semantic error codes + troubleshooting catalog — align the *pattern* (`<NS>-<DOMAIN>-NNN` or category enum, each code anchored to a docs URL), keep per-repo namespaces | pounce (codes + catalog); bengal (`errors/codes.py` 12-category enum); milo (`M-DOM-NNN` + `milo doctor`); kida (`K-CMP-*` + catalog) |
| 23 | Honest-caveat claim voice with exact numbers | patitas (GFM 654/672 with the 18 gaps enumerated; CHANGELOG self-discloses past bugs). No claim outruns its artifact |
| 24 | Recorded operational evidence for deployables | elbysodic (`railway-production-smoke-record.md`, recovery drills, race-condition QA scripts); furatena (JSON evidence/baseline artifacts) |

---

## The evidence program

One epic per repo makes its standalone competitive claim checkable, extending pounce#227 stack-wide. The formula: **name the claim → name the incumbent → publish the artifact → cite it from the README in the honest-caveat voice → gate regressions in CI.**

Priority order: rosettes (claims currently outrun proof), zoomies (category entry needs real-peer interop), kida (cheapest — formalize what exists), patitas (nearly done), then chirp/bengal/milo (packaging), purr (deferred until beta; eventual claim: O(change) reactivity latency).

## Epic index (filed 2026-07-08)

| Repo | Hygiene alignment | Evidence |
|------|-------------------|----------|
| pounce | pounce#264 | done: pounce#227 / #228 / #240 |
| kida | kida#198 | kida#199 |
| patitas | patitas#105 | patitas#106 |
| rosettes | rosettes#5 | rosettes#4 (P1) |
| zoomies | zoomies#218 | zoomies#217 (P1; elevates zoomies#200/#201/#208) |
| chirp | chirp#620 | chirp#621 |
| bengal | bengal#694 | bengal#695 |
| milo-cli | milo-cli#109 | milo-cli#110 |
| purr | purr#1 | deferred until beta |
| furatena | furatena#365 | n/a (app; multi-lane CI, coverage ratchets, and evidence artifacts are the exports) |
| elbysodic | elbysodic#213 (cloud CI) | n/a (app; recorded-evidence practice is the export) |

## Maintenance

When a repo originates a new practice worth propagating, add it here with the originator named and open checklist items on the affected repos' alignment epics. When a gap row closes everywhere, mark the practice as baseline-met rather than deleting it — the table is also the stack's hygiene history.
