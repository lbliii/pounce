# Epic: RFC 9842 Compression Dictionary Transport — Shared Zstd Dictionaries for API Responses

**Status**: Draft
**Created**: 2026-04-12
**Target**: Pounce 0.7.0 (Q3 2026)
**Estimated Effort**: 32–48 hours
**Dependencies**: Python 3.14+ with `compression.zstd` (PEP 784), existing `_compression.py` pipeline
**Source**: Codebase analysis of `_compression.py`, `_request_pipeline.py`, `config.py`, `ROADMAP.md`

---

## Why This Matters

Pounce already negotiates zstd and gzip per-request, but every response starts compression from scratch. API endpoints returning repetitive JSON structures (same keys, similar shapes, shared string patterns) compress poorly because the compressor never learns the patterns. RFC 9842 lets clients and servers share pre-trained zstd dictionaries — the server compresses using known patterns, the client decompresses using the same dictionary. Chrome has supported this since version 123+.

**No other Python ASGI server offers this.** It's genuine differentiation for Pounce.

### Consequences of Not Doing This

1. **Missed compression ratio** — Typical API JSON responses compress ~70% with generic zstd. Dictionary-aware compression reaches ~85–92% for repetitive payloads (30–60% smaller than generic zstd).
2. **Bandwidth waste on mobile/constrained networks** — API-heavy SPAs transfer megabytes of structurally similar JSON that could be a fraction of the size.
3. **Competitive gap** — CDN edge servers (Cloudflare, Fastly) are adopting RFC 9842. Pounce should compress as well as the CDN, not worse.

### Evidence Table

| Source | Finding | Proposal Impact |
|--------|---------|-----------------|
| `_compression.py` (221 lines) | 2 compressors (Gzip, Zstd), no dictionary support | FIXES — adds `DictZstdCompressor` |
| `_compression.py:47-87` | `Compressor` protocol has 4 methods, no dict parameter | FIXES — extends protocol or adds dict-aware variant |
| `config.py:99-101` | `compression: bool`, `compression_min_size: int` — no dict config | FIXES — adds `compression_dictionary_path` and `compression_dictionary_id` |
| `_request_pipeline.py:47-60` | `negotiate_compressor()` checks `Accept-Encoding` only | FIXES — also checks `Available-Dictionary` header |
| `ROADMAP.md:74-76` | RFC 9842 planned for Q3 2026, described as "genuine innovation" | ALIGNED |
| `_compression.py:26-31` | Zstd import with fallback — `_HAS_ZSTD` flag | UNRELATED — dict feature requires zstd, gating is already handled |

### Invariants

These must remain true throughout or we stop and reassess:

1. **Generic compression untouched**: Requests without `Available-Dictionary` header must receive identical responses as today. Zero regression for existing clients.
2. **Per-request compressor isolation**: Dictionary compressors must remain per-request instances with no shared mutable state — free-threading safety is non-negotiable.
3. **Test suite green**: Full `pytest tests/ -x -q --timeout=10` passes after every sprint.

---

## Target Architecture

```
Client sends:                 Server does:
─────────────                 ────────────
GET /api/v1/items             1. Check Accept-Encoding for "zstd"
Accept-Encoding: zstd         2. Check Available-Dictionary header
Available-Dictionary: :abc:   3. Look up dict ID "abc" in config.compression_dictionaries
                              4. If match: create DictZstdCompressor(dict=loaded_dict)
                              5. Compress response using dictionary
                              6. Set Content-Encoding: dcz (dictionary-compressed zstd)
                              7. Set Used-Dictionary: :abc:

Without dictionary:           Falls through to normal negotiate_encoding() path
```

**New types:**

```python
@dataclass(frozen=True)
class CompressionDictionary:
    id: str              # Dictionary ID (sf-binary from Available-Dictionary)
    data: bytes          # Raw dictionary bytes
    match: str           # URL pattern this dict applies to (e.g., "/api/*")

class DictZstdCompressor:
    """Zstd compressor pre-loaded with a shared dictionary."""
    def __init__(self, dict_data: bytes, level: int = 3): ...
    # Implements Compressor protocol
```

**Config additions:**

```python
# In ServerConfig
compression_dictionaries: tuple[CompressionDictionary, ...] = ()
```

---

## Sprint Structure

| Sprint | Focus | Effort | Risk | Ships Independently? |
|--------|-------|--------|------|---------------------|
| 0 | Design: dictionary loading, header parsing, encoding negotiation | 4h | Low | Yes (RFC only) |
| 1 | `DictZstdCompressor` + dictionary loading from files | 8h | Low | Yes (internal API, tested) |
| 2 | Header negotiation: `Available-Dictionary` parsing + `Used-Dictionary` response | 8h | Medium | Yes (compressor selected by headers) |
| 3 | Config integration: `compression_dictionaries` in `ServerConfig` + CLI | 6h | Low | Yes (configurable, end-to-end) |
| 4 | Dictionary serving endpoint + client discovery | 6h | Medium | Yes (clients can fetch dicts) |

---

## Sprint 0: Design & Validate

**Goal**: Prove the stdlib `compression.zstd` supports dictionary compression and confirm RFC 9842 header semantics.

### Task 0.1 — Validate zstd dictionary API

Confirm `compression.zstd.ZstdCompressor` accepts a `dict_data` parameter (or equivalent). Write a standalone script that:
1. Trains a dictionary from sample JSON payloads
2. Compresses with dictionary vs without
3. Measures ratio improvement

**Acceptance**: Script runs on Python 3.14, ratio improvement > 20% on sample API JSON.

### Task 0.2 — RFC 9842 header specification

Document the exact header semantics:
- `Available-Dictionary` request header format (structured field, sf-binary)
- `Use-As-Dictionary` response header for dictionary advertisement
- `Content-Encoding: dcz` (dictionary-compressed zstd) vs `dcb` (dictionary-compressed brotli)
- Dictionary ID derivation (SHA-256 hash of dictionary content, sf-binary encoded)

**Acceptance**: Design doc in `docs/plans/` with header examples and edge cases.

### Task 0.3 — Integration point design

Map exactly where in the request pipeline dictionary negotiation happens:
- `negotiate_compressor()` in `_request_pipeline.py` — extend or wrap?
- `create_compressor()` in `_compression.py` — add `dict_data` parameter?
- Header injection in `asgi/bridge.py` `create_send()` — where does `Used-Dictionary` go?

**Acceptance**: Written design with file paths and function signatures. Reviewed against both Worker and SyncWorker paths to ensure parity.

---

## Sprint 1: DictZstdCompressor

**Goal**: Implement dictionary-aware zstd compression that satisfies the existing `Compressor` protocol.

### Task 1.1 — Implement `DictZstdCompressor`

Add to `_compression.py`:
- `DictZstdCompressor` class conforming to `Compressor` protocol
- Dictionary loading from bytes
- `encoding` property returns `"dcz"` (dictionary-compressed zstd per RFC 9842)

**Files**: `src/pounce/_compression.py`
**Acceptance**: `DictZstdCompressor` passes same contract tests as `ZstdCompressor` + `rg 'class DictZstdCompressor' src/pounce/_compression.py` returns 1 hit.

### Task 1.2 — Dictionary file loading utility

Add `load_dictionary(path: Path) -> CompressionDictionary`:
- Reads dictionary file from disk
- Computes SHA-256 ID (sf-binary format)
- Returns frozen dataclass

**Files**: `src/pounce/_compression.py` (or new `src/pounce/_dictionary.py` if > 80 lines)
**Acceptance**: Unit test loads a test dictionary, verifies ID computation matches RFC spec.

### Task 1.3 — Unit tests

- Compress/decompress round-trip with dictionary
- `encoding` property returns `"dcz"`
- Streaming: `compress()` + `sync_flush()` + `flush()` sequence
- Error case: invalid dictionary data

**Files**: `tests/unit/test_compression.py` (extend existing)
**Acceptance**: `pytest tests/unit/test_compression.py -x -q` passes.

---

## Sprint 2: Header Negotiation

**Goal**: Parse `Available-Dictionary` from requests, select dictionary compressor, emit `Used-Dictionary` in response.

### Task 2.1 — Parse `Available-Dictionary` header

In `_request_pipeline.py`, extend `negotiate_compressor()` to:
1. Check for `Available-Dictionary` header (structured field, sf-binary)
2. Extract dictionary ID
3. Look up in loaded dictionaries
4. If found + client accepts zstd: return `DictZstdCompressor`
5. If not found: fall through to generic path (invariant #1)

**Files**: `src/pounce/_request_pipeline.py`, `src/pounce/_headers.py`
**Acceptance**: `negotiate_compressor()` returns `DictZstdCompressor` when matching dictionary available, `ZstdCompressor`/`GzipCompressor` otherwise.

### Task 2.2 — Emit `Used-Dictionary` response header

When `DictZstdCompressor` is selected, inject:
- `Content-Encoding: dcz`
- `Used-Dictionary: :<dict-id-sf-binary>:`

**Files**: `src/pounce/asgi/bridge.py`, `src/pounce/asgi/sync_bridge.py`
**Acceptance**: Integration test confirms response headers present when dictionary matches.

### Task 2.3 — Tests for negotiation paths

- Client sends matching `Available-Dictionary` → `dcz` response
- Client sends non-matching dictionary ID → generic zstd fallback
- Client sends `Available-Dictionary` but no `Accept-Encoding: zstd` → no compression
- Client sends no `Available-Dictionary` → existing behavior unchanged

**Files**: `tests/unit/test_compression.py`, `tests/unit/test_request_pipeline.py`
**Acceptance**: All 4 scenarios pass.

---

## Sprint 3: Config & CLI Integration

**Goal**: Make dictionary compression configurable via `ServerConfig` and CLI flags.

### Task 3.1 — `ServerConfig` fields

Add to `config.py`:
- `compression_dictionaries: tuple[CompressionDictionary, ...] = ()`
- Validation: files exist, IDs are unique, zstd available

**Files**: `src/pounce/config.py`
**Acceptance**: `rg 'compression_dictionaries' src/pounce/config.py` returns config field + validation.

### Task 3.2 — CLI flags

Add `--compression-dictionary PATH` (repeatable) to `_cli.py`.

**Files**: `src/pounce/_cli.py`
**Acceptance**: `python -m pounce --help` shows the flag. Smoke test: server starts with `--compression-dictionary test.dict`.

### Task 3.3 — Config file support

Support `compression_dictionaries` in `pounce.toml` / `[tool.pounce]`:

```toml
[[compression.dictionaries]]
path = "dicts/api-v1.dict"
match = "/api/v1/*"
```

**Files**: Config file parser (if it exists by then)
**Acceptance**: Config file test loads dictionaries correctly.

---

## Sprint 4: Dictionary Serving & Client Discovery

**Goal**: Let clients discover and download dictionaries via `Use-As-Dictionary` response header.

### Task 4.1 — Dictionary endpoint

Serve dictionary files at a configurable path (e.g., `/.well-known/compression-dictionary/<id>`).

**Files**: `src/pounce/_static.py` or new `src/pounce/_dictionary_endpoint.py`
**Acceptance**: `curl /.well-known/compression-dictionary/<id>` returns dictionary bytes with correct `Content-Type`.

### Task 4.2 — `Use-As-Dictionary` advertisement

For responses matching a dictionary's `match` pattern, add:
```
Use-As-Dictionary: match="/api/v1/*"
```
This tells browsers to fetch the dictionary for future requests.

**Files**: `src/pounce/asgi/bridge.py`
**Acceptance**: Integration test confirms header present on matching responses.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `compression.zstd` doesn't support dictionary parameter | Low | High | Sprint 0 validates API before any implementation |
| Dictionary loading adds startup latency | Low | Low | Dictionaries are small (32-64 KiB typical), loaded once at startup |
| Clients ignore `Available-Dictionary` (low adoption) | Medium | Low | Feature is opt-in; zero cost when unused (invariant #1) |
| `dcz` Content-Encoding not recognized by proxies | Medium | Medium | Only emit when client explicitly offers dictionary; falls through to generic zstd otherwise |
| Dictionary training requires external tooling | Medium | Low | Out of scope — document `zstd --train` workflow in deployment guide |

---

## Success Metrics

| Metric | Current | After Sprint 2 | After Sprint 4 |
|--------|---------|----------------|----------------|
| Compression ratio (repetitive API JSON) | ~70% (generic zstd) | ~88% (dictionary zstd) | ~88% |
| Response header overhead | 0 | +2 headers when dict matches | +2 headers + advertisement |
| Config options for dictionaries | 0 | 0 (hardcoded for testing) | Full CLI + config file |
| Browser compatibility | N/A | Chrome 123+, Firefox (pending) | Same + discovery |
| Regression on non-dict requests | 0 | 0 (invariant #1) | 0 |

---

## Relationship to Existing Work

- **`_compression.py` pipeline** — prerequisite, already stable. This plan extends it without modifying existing compressor behavior.
- **Config file support (Q2 2026)** — Sprint 3 depends on this if config file support ships first. Otherwise, CLI-only in Sprint 3, config file added as follow-up.
- **io_uring backend (Q3 2026)** — parallel, no dependency. Dictionary compression is protocol-layer, io_uring is I/O-layer.

---

## Changelog

- **2026-04-12**: Initial draft from codebase analysis.
