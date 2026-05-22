# Steward Questions

These are targeted SME questions the bootstrap cannot answer from code, tests,
docs, release notes, or git history. Do not invent answers; either update the
relevant steward file after confirmation or keep the question here.

## Runtime And Public API

- Which `ServerConfig` fields are considered stable public API versus beta
  implementation knobs?
- Should `worker_mode="subinterpreter"` be described as beta, limited, or stable
  in future public docs?
- Which observability names, metric names, and lifecycle event names are allowed
  to change without a migration note?

## Protocol

- Which optional protocol gaps in `docs/design/protocol-proof-ledger.json` are
  release blockers versus acceptable documented limitations?
- Should HTTP/3 eventually seek parity with H1/H2, or remain explicitly limited
  to selected production paths?
- What parser safety checks are considered non-negotiable even if they reject a
  client that another ASGI server accepts?

## ASGI Bridge

- Which ASGI extension messages are intended public contracts versus internal
  bridge/runtime capabilities?
- What level of framework compatibility is required before docs can say a
  framework is fully supported?
- Are sync-app and async-app behavior differences acceptable when performance is
  materially better, or should parity always win?

## Transport And TLS

- Should Pounce ever own certificate reload or certificate discovery, or should
  that remain external deployment tooling?
- Which bind/exposure warnings should be fatal in production-facing commands, if
  any?
- What platforms beyond Linux and macOS should transport behavior explicitly
  support or decline?

## Operator Output Templates

- Which output fields are stable enough for external log parsers or deployment
  automation to depend on?
- Should pretty terminal output prioritize compact incident output or richer
  first-run education when those goals conflict?
- What is the long-term policy for branding hooks such as app display metadata?

## Evidence

- Which targeted test suites should be mandatory before merging hot-path changes
  when full CI is impractical locally?
- What amount of flakiness justifies quarantining a test versus blocking a PR?
- Should framework compatibility expand beyond FastAPI, Starlette, Django, and
  Litestar?

## Design And Troubleshooting

- Which design decisions need ADRs before implementation rather than PR-body
  explanation after implementation?
- Should old `POUNCE_*` codes remain documented forever once unused, or move to a
  deprecated section?
- Which public claim phrases are too risky to allow even with proof?

## Public Documentation Site

- What product story should the homepage lead with when performance,
  compatibility, and free-threading pull in different directions?
- Which docs pages are canonical versus generated mirrors of README/design
  content?
- How should limited optional protocol support be presented without burying
  useful features?

## Examples

- Which examples are production-ready recipes and which are intentionally
  prototypes?
- Should examples favor minimal code or operational completeness when those
  conflict?
- What framework examples, if any, should Pounce own without competing with each
  framework's own scaffolders?

## Performance Evidence

- What benchmark workloads represent the real deployment shapes Pounce most
  wants to optimize for?
- What regression threshold is meaningful enough to block a PR given benchmark
  variance?
- Which performance numbers are strategic public claims versus local tuning
  examples?

## CI And Release

- Which checks are required for every PR versus only release candidates?
- Should release notes be drafted from Towncrier fragments, site release pages,
  or a separate maintainer-authored summary?
- What is the policy for skipping changelog fragments on docs-only or
  steward-system changes?

## Planning

- Which roadmap items are committed direction versus exploratory options?
- When should an implemented plan become an ADR instead of staying in planning
  records?
- What kinds of downstream ecosystem needs should influence Pounce's roadmap,
  and which should remain external?
