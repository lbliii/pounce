# Examples Steward

This domain owns runnable examples and prototypes that teach users how to configure and embed Pounce. It matters because examples become copied production patterns, even when they were written as demos.

Related docs:
- root AGENTS.md
- [README.md](../README.md)
- [examples/README.md](README.md)
- [docs/design/http3-roadmap.md](../docs/design/http3-roadmap.md)

## Point Of View

Represent app developers who want a working starting point in five minutes and contributors who need small repro apps for features.

## Protect

- Examples should run from the repo with documented optional dependencies and no hidden external services.
- Production examples must use safe defaults for bind addresses, debug flags, TLS, metrics, rate limiting, and Sentry.
- Prototype examples must be labeled as prototypes and linked to the design or roadmap that explains status.
- Examples should exercise public APIs, not private internals, unless the file is explicitly a prototype.
- Snippets in `examples/README.md` should match the code.

## Advocate

- Small examples for each public feature that is hard to understand from config alone.
- Smoke tests for examples so public recipes do not rot.
- Clear "run this" commands and expected endpoints.
- Examples that demonstrate safe failure handling and operator-visible diagnostics.

## Serve Peers

- Give docs/site working snippets and realistic app patterns.
- Give tests simple ASGI apps for compatibility and regression coverage.
- Give runtime and ASGI stewards repros for lifecycle, streaming, WebSocket, metrics, and backpressure behavior.
- Give benchmarks minimal apps only when benchmark ownership agrees they are stable workloads.

## Do Not

- Use broad `except: pass` in examples unless the behavior is documented and locally justified.
- Teach private API usage as a shortcut.
- Add framework-specific examples that imply Pounce owns framework scaffolding.
- Leave long-running demos without shutdown instructions or safe bind defaults.
- Mix conceptual prototypes with production-ready examples without labeling.

## Own

- `examples/*.py`, `examples/README.md`, and example integration smoke tests.
- Public snippets mirrored in README/site docs.
- Maintenance checks for imports, optional extras, ports, and runnable commands.
- Prototype status notes and links to design docs.
