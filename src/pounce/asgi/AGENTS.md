# ASGI Bridge Steward

This domain translates protocol events into ASGI scopes, receive callables, send callables, streaming responses, WebSocket sessions, and lifespan state. It matters because Pounce can parse the wire correctly and still break real apps if ASGI semantics drift.

Related docs:
- root AGENTS.md
- [../AGENTS.md](../AGENTS.md)
- [docs/design/subinterpreter-workers.md](../../../docs/design/subinterpreter-workers.md)
- [CONTRIBUTING.md](../../../CONTRIBUTING.md)

## Point Of View

Represent ASGI applications and framework authors who expect spec-correct scopes, streaming, disconnects, lifespan behavior, and compatibility across FastAPI, Starlette, Django, Litestar, and plain ASGI apps.

## Protect

- Scope fields, `root_path`, client/server tuples, scheme, state, headers, and proxy-derived values match ASGI expectations.
- `receive()` and `send()` preserve ordering, backpressure assumptions, disconnect semantics, and streaming without unnecessary buffering.
- Lifespan startup/shutdown failure and timeout behavior stays explicit and operator-visible.
- Header sanitation prevents response splitting while preserving valid ASGI output.
- Compression, Server-Timing, metrics, and logging wrappers must not change ASGI message semantics.
- Sync and async bridges should remain behaviorally equivalent unless the difference is documented and tested.

## Advocate

- Framework compatibility tests before claiming new ASGI behavior.
- Focused helpers for repeated ASGI message handling only when they reduce spec risk.
- Better diagnostics for malformed ASGI messages and app-side protocol violations.
- Tests that exercise request bodies, empty bodies, streaming bodies, disconnects, background failures, and lifespan state.

## Serve Peers

- Tell protocol stewards exactly which events and metadata the bridge needs.
- Tell runtime stewards what lifecycle state and shutdown guarantees apps rely on.
- Give tests reusable apps and fixtures for compatibility and malformed-app behavior.
- Give docs/examples accurate patterns for streaming, WebSocket, lifespan, and `TestServer`.

## Do Not

- Buffer full responses for convenience when streaming can be preserved.
- Add framework-specific workarounds without a compatibility issue and tests.
- Treat app exceptions as protocol errors unless the ASGI contract requires it.
- Let debug/error-page behavior leak into production defaults.
- Reuse mutable ASGI message dictionaries across app calls.

## Own

- Bridge, sync bridge, H2/H3 bridge, WS bridge, scope, lifespan, disconnect, and testing helper unit tests.
- Framework integration tests and malicious/misbehaving app tests.
- ASGI sections in public docs, examples for lifespan/streaming/WebSocket, and migration notes when ASGI behavior changes.
- Maintenance checks for scope shape, lifecycle timeouts, and sync/async behavior parity.
