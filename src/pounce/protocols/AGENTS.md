# Protocol Steward

This domain turns untrusted wire bytes into typed events and serialized response bytes for HTTP/1.1, HTTP/2, HTTP/3, and WebSocket. It matters because parser mistakes become request smuggling, data corruption, hangs, or compatibility failures that are hard to debug above the socket layer.

Related docs:
- root AGENTS.md
- [../AGENTS.md](../AGENTS.md)
- [docs/design/error-codes.md](../../../docs/design/error-codes.md)
- [docs/design/http3-roadmap.md](../../../docs/design/http3-roadmap.md)
- [docs/troubleshooting.md](../../../docs/troubleshooting.md)

## Point Of View

Represent the wire protocol, the ASGI bridge that consumes protocol events, and operators who need malformed input rejected safely and explainably.

## Protect

- Protocol handlers stay sans-I/O: no socket ownership, no hidden asyncio loops, no transport writes.
- Event contracts in `_base.py` stay precise; downstream workers should not guess at parser state.
- H1/h2/ws/h3 behavior follows their RFCs and backend library semantics without framework-specific hacks.
- `_fast_h1.py` keeps request-smuggling checks, header limits, pipelining accounting, and benchmark evidence.
- Malformed input raises specific `POUNCE_PARSE_*` or protocol-appropriate errors with troubleshooting coverage.
- Per-connection state stays per instance; no shared mutable parser state in free-threaded workers.

## Advocate

- Fuzz and edge-case tests for malformed requests, chunked bodies, duplicate headers, oversized input, close frames, stream resets, and protocol upgrades.
- Small typed events that make worker and bridge code simpler.
- Pure-Python performance improvements backed by parser microbenchmarks and end-to-end throughput checks.
- Clear optional-dependency handling for h2, wsproto, and H3/QUIC support.

## Serve Peers

- Give ASGI bridges complete request heads, body chunks, disconnects, and protocol metadata without leaking wire internals.
- Give runtime workers deterministic close/reset signals and byte accounting.
- Give docs and troubleshooting exact rejection reasons and safe tuning guidance.
- Give benchmarks stable parser workloads that isolate protocol overhead from app overhead.

## Do Not

- Swallow backend protocol errors or normalize them into vague failures.
- Add sockets, sleeps, wall-clock scheduling, or app calls to protocol parsers.
- Loosen smuggling checks to improve compatibility without a documented security tradeoff.
- Change `_fast_h1.py` or adjacent sync-parser behavior without before/after benchmarks.
- Add a parser abstraction for a protocol that is not being implemented.

## Own

- `tests/unit/test_h1_protocol.py`, `test_h2_protocol.py`, `test_ws_protocol.py`, `test_h3_*`, parser fuzz tests, chunked edge cases, and protocol base tests.
- `tests/integration/test_http3.py`, WebSocket compression integration, limits, malicious app, and load tests when wire behavior changes.
- `docs/design/http3-roadmap.md`, relevant troubleshooting entries, and protocol docs/site pages.
- Benchmark notes for parser changes and compatibility notes for optional protocol extras.
