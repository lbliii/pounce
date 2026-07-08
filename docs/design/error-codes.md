# ADR: Error-Code Naming Scheme

**Status**: Accepted
**Date**: 2026-04-20
**Epic**: [vibe-coding-epic.md](../plans/vibe-coding-epic.md) — Sprint 0.1
**Decider**: Sprint 0 design task

## Context

Pounce has 9 exception subclasses (`_errors.py`) raised at 24 sites across `src/pounce/`. Today each raise carries only a string message and a status code. When an error reaches a user — via a stack trace, an HTTP response, or a log line — there is no stable identifier that:

- Can be grepped from a bug report back to its raise site
- Can anchor a docs entry (`troubleshooting.md#...`)
- Can survive message-text rephrasing without breaking external consumers

Agents debugging from pounce output have to pattern-match on free-form English. That is fragile and it's the primary thing this ADR fixes.

## Decision

Every `PounceError` carries a **semantic** code of the form `POUNCE_<CATEGORY>_<SPECIFIC>`, assigned at the raise site, uppercase, `SNAKE_CASE`.

- **Semantic** (`POUNCE_TLS_CERT_MISSING`) — chosen over numeric (`POUNCE_E042`).
- Defaults to a category-level fallback (e.g. `POUNCE_TLS_E`) when a specific code isn't warranted.
- Unique per (class, code) pair. Intentional sharing across sites is allowed but must be deliberate — the test suite enforces the scheme, not uniqueness.

## Naming Rules

```
POUNCE_<CATEGORY>_<SPECIFIC>
  └──┬──┘  └────┬────┘  └───┬───┘
  namespace  category    what went wrong
```

1. **`POUNCE_` prefix, always.** Makes codes greppable across vendored/bundled environments where multiple libraries use the same scheme.
2. **Category from a fixed enum** (below). Category is the sole disambiguator when two codes coincidentally collide on `<SPECIFIC>`.
3. **`<SPECIFIC>` is declarative past-tense or noun-phrase.** `CERT_MISSING`, not `MISSING_CERT_ERROR` (the `_E` suffix is redundant with the class name).
4. **Uppercase, underscores, ASCII letters + digits.** Regex: `^POUNCE_[A-Z]+_[A-Z0-9_]+$`.
5. **Category fallback code** is `POUNCE_<CATEGORY>_E` (e.g. `POUNCE_PARSE_E`). Used when a specific code isn't worth minting.
6. **One raise site → one code.** Two distinct raise sites that mean the same thing may intentionally share a code (rare; must be commented).
7. **Codes are append-only.** Never rename or repurpose a code. Deprecate by stopping use; the code stays reserved for tooling that has seen it in the wild.

## Category Enum

Derived from existing `_errors.py` classes, plus `CONFIG` for Sprint 2's config-validation additions.

| Category     | Class                    | Status | Examples                                                   |
|--------------|--------------------------|--------|------------------------------------------------------------|
| `PARSE`      | `ParseError`             | 400    | `POUNCE_PARSE_MALFORMED_REQUEST_LINE`, `POUNCE_PARSE_BAD_HEADER` |
| `TIMEOUT`    | `RequestTimeoutError`    | 408    | `POUNCE_TIMEOUT_REQUEST`, `POUNCE_TIMEOUT_KEEPALIVE`       |
| `LIMIT`      | `LimitError`             | 413/431| `POUNCE_LIMIT_HEADER_SIZE`, `POUNCE_LIMIT_BODY_SIZE`       |
| `APP`        | `AppError`               | 500    | `POUNCE_APP_UNHANDLED`, `POUNCE_APP_BAD_RESPONSE`          |
| `LIFESPAN`   | `LifespanError`          | 500    | `POUNCE_LIFESPAN_STARTUP_FAILED`, `POUNCE_LIFESPAN_TIMEOUT` |
| `SUPERVISOR` | `SupervisorError`        | 500    | `POUNCE_SUPERVISOR_SPAWN_FAILED`, `POUNCE_SUPERVISOR_RESTART_EXHAUSTED` |
| `WORKER`     | `WorkerError`            | 500    | `POUNCE_WORKER_STARTUP_FAILED`, `POUNCE_WORKER_CRASHED`     |
| `TLS`        | `TLSError`               | 500    | `POUNCE_TLS_CERT_MISSING`, `POUNCE_TLS_HANDSHAKE_FAILED`   |
| `RELOAD`     | `ReloadError`            | 500    | `POUNCE_RELOAD_WATCHER_FAILED`, `POUNCE_RELOAD_DRAIN_TIMEOUT` |
| `CONFIG`     | (added in Sprint 2)      | N/A    | `POUNCE_CONFIG_INVALID_VALUE`, `POUNCE_CONFIG_FILE_NOT_FOUND` |

Adding a new category requires adding a new `PounceError` subclass. This is intentional — categories are load-bearing and should not proliferate.

## Anti-Collision Rule

A test (`tests/unit/test_error_codes.py`) collects every string literal passed as `code=` to a pounce error constructor, asserts:

1. Every code matches the regex `^POUNCE_[A-Z]+_[A-Z0-9_]+$`.
2. Every code's category segment matches the raising class's declared category.
3. Within a single class, codes are unique — unless the raise site comment explicitly documents intentional reuse.

The test runs via AST inspection, not runtime, so dead branches don't escape it.

## Alternatives Considered

### Numeric codes (`POUNCE_E042`)

**Pros**: Short, language-neutral, traditional (HTTP, ORA-nnnn, compiler errors).
**Cons**:
- Requires a central registry file; every new code is a coordination point.
- Unreadable in stack traces (`POUNCE_E042` tells you nothing; `POUNCE_TLS_CERT_MISSING` tells you everything).
- Bit-rot risk: codes get deleted mid-series, leaving gaps or getting reused accidentally.
- No greppability from raise site to docs.

**Rejected.** Pounce is a single-language Python library aimed at agents that read English. Semantic wins here.

### Hierarchical dotted codes (`pounce.tls.cert_missing`)

**Pros**: Arguably prettier.
**Cons**:
- Case collisions with config keys, log fields.
- Breaks on shell paste (dots interpreted by regex/glob tools).
- Uppercase-screamsnake is the established convention (HTTP 404, ENOENT, OOM_KILLER).

**Rejected** on ergonomics.

### No codes — just better error messages

**Pros**: Zero migration cost.
**Cons**: Fails the core requirement — messages are free-form English, not stable identifiers. Agents surfacing errors to users need something to cite.

**Rejected.**

## Consequences

### Positive

- Every pounce error is greppable from stack trace → source → docs.
- `X-Pounce-Error-Code` response header is a stable contract for HTTP consumers.
- Troubleshooting doc (Sprint 5.2) has clean anchors: `docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING`.
- Adding a new raise site has a natural "pick a code" checkpoint that forces the author to think about how the error is surfaced.

### Negative

- Append-only discipline means we accumulate codes over time. Mitigation: codes are cheap, the class hierarchy provides a natural browsing index.
- One more thing to get right in code review. Mitigation: the AST test catches violations automatically.

### Neutral

- External consumers (error trackers, dashboards) can start keying on `code` instead of message. They don't have to — but they can.

## References

- `src/pounce/_errors.py` — current exception hierarchy
- `src/pounce/_fast_h1.py`, `src/pounce/protocols/h1.py`, `src/pounce/supervisor.py`, `src/pounce/asgi/lifespan.py`, `src/pounce/net/tls.py` — the 24 raise sites to migrate in Sprint 1.2
- POSIX `errno` + Windows `HRESULT` — prior art for categorized error codes
