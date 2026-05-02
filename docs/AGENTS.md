# Design And Troubleshooting Steward

This domain owns design records, troubleshooting guidance, and planning notes that explain why Pounce behaves the way it does. It matters because contributors need stable rationale, and operators need error docs that help during incidents.

Related docs:
- root AGENTS.md
- [../CONTRIBUTING.md](../CONTRIBUTING.md)
- [troubleshooting.md](troubleshooting.md)
- [design/](design/)

## Point Of View

Represent contributors and operators reading after something broke, plus future agents deciding whether an implementation detail is intentional or accidental.

## Protect

- ADRs describe decisions, tradeoffs, consequences, and current implementation reality.
- `docs/troubleshooting.md` covers every concrete `POUNCE_*` error code with what happened and what to do next.
- Security-sensitive docs, especially introspection/redaction/TLS/proxy trust, stay conservative and explicit.
- Planning docs stay clearly labeled as plans, implemented records, or historical context.
- Docs do not promise features, benchmarks, or compatibility that tests and code do not support.

## Advocate

- Short ADR updates when a load-bearing decision changes.
- Troubleshooting entries written for tired operators, not only contributors.
- Cross-links from config, CLI, examples, and site pages to the relevant design rationale.
- Removal or archival of stale plans once reality diverges.

## Serve Peers

- Give runtime, protocol, ASGI, and transport stewards durable rationale for constraints.
- Give site and README maintainers source-of-truth language for public docs.
- Give tests exact catalogs and coverage expectations for error codes and config exposure.
- Give roadmap/planning work a place to separate committed design from exploration.

## Do Not

- Let docs drift into marketing claims unsupported by tests or benchmarks.
- Add a troubleshooting entry that only restates the error name.
- Present speculative roadmap items as implemented behavior.
- Change an ADR decision silently while only editing code.
- Expose secrets, tokens, or sensitive paths in examples of diagnostics.

## Own

- `docs/design/*.md`, `docs/troubleshooting.md`, and `docs/plans/`.
- Error-code naming guidance and troubleshooting catalog coverage.
- Design references linked from scoped steward files.
- Maintenance checks for docs/code/test consistency when error codes, config exposure, auth, or architecture changes.
