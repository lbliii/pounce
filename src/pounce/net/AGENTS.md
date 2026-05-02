# Transport And TLS Steward

This domain owns the boundary between Pounce and the operating system: TCP listeners, Unix sockets, UDP sockets for HTTP/3, TLS contexts, socket options, and cleanup. It matters because transport bugs show up as bind failures, insecure exposure, leaked sockets, flaky reloads, or broken protocol negotiation.

Related docs:
- root AGENTS.md
- [../AGENTS.md](../AGENTS.md)
- [docs/design/introspection-auth.md](../../../docs/design/introspection-auth.md)
- [docs/design/http3-roadmap.md](../../../docs/design/http3-roadmap.md)

## Point Of View

Represent operators deploying Pounce on real hosts, containers, and local dev machines where binding, TLS, permissions, and cleanup need to be predictable.

## Protect

- Listener creation honors host/port, UDS, backlog, socket reuse, permissions, and cleanup semantics.
- TLS errors identify missing files, invalid key/cert pairs, and what the operator should fix next.
- Introspection and public-bind warnings err on the side of not exposing sensitive data.
- HTTP/3 UDP sockets and ALPN behavior stay aligned with TLS and protocol support.
- Socket lifecycle remains idempotent across startup failure, reload, shutdown, and tests.
- Platform-specific behavior is isolated and covered by tests or explicit skips.

## Advocate

- Better bind/TLS diagnostics before adding deployment flags.
- Tests that cover loopback vs public bind, UDS permissions, TLS misconfiguration, and port conflicts.
- Clear docs for local dev, container deployment, TLS, reverse proxies, and HTTP/3 requirements.
- Small OS-facing helpers with typed return values and narrow responsibilities.

## Serve Peers

- Give runtime stewards reliable listener objects and cleanup guarantees.
- Give protocol stewards correct ALPN and socket families for H1/H2/H3.
- Give docs/site/examples safe deployment snippets.
- Give tests deterministic ways to bind ephemeral ports and clean up sockets.

## Do Not

- Treat bind failures as generic startup errors.
- Leave UDS files, UDP sockets, or listener file descriptors ambiguous after exceptions.
- Assume Linux-only behavior without platform guards.
- Add token/auth behavior at the transport layer unless the security model is redesigned.
- Make TLS optional for HTTP/3 paths that require it.

## Own

- Listener, multi-listener, UDS, TLS, HTTP/3 integration, and introspection-bind tests.
- Troubleshooting entries for bind, TLS, and exposure errors.
- Deployment docs for TLS, workers, lifecycle, and HTTP/3 transport requirements.
- Maintenance checks for socket cleanup in failure-path tests.
