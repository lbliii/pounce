# Epic: Framework Compatibility Matrix — Certify Pounce Against Every Major ASGI Framework

**Status**: Draft
**Created**: 2026-04-12
**Target**: Pounce 0.6.0 (Q2 2026)
**Estimated Effort**: 24–36 hours
**Dependencies**: FastAPI, Starlette, Litestar, Django (ASGI), chirp — installed as test extras
**Source**: ROADMAP.md Q2 goals, codebase analysis of existing test infrastructure and ASGI compliance tests

---

## Why This Matters

Pounce has full protocol coverage (H1, H2, H3, WebSocket) and passes its own ASGI 3.0 compliance suite — but **zero tests exercise a real framework**. The `pounce info` command detects installed frameworks, the startup banner names them, but nobody has verified that a FastAPI route, a Django view, or a Litestar handler actually works end-to-end through Pounce's worker pipeline.

The ROADMAP is explicit: the Q2 goal isn't just "it works" — it's getting Pounce mentioned as a recommended server in framework documentation. That requires **evidence**: a public compatibility matrix backed by CI-green integration tests.

### Consequences of Not Doing This

1. **Adoption blocker** — Teams won't switch from Uvicorn without proof their framework works. "It should work" is not evidence.
2. **Hidden bugs** — Each framework exercises different ASGI patterns (middleware stacks, lifespan state, background tasks, streaming responses, WebSocket routing). Bugs in Pounce's ASGI bridge may only surface under specific framework usage patterns.
3. **Missed Q2 window** — The ROADMAP targets framework outreach this quarter. PRs to FastAPI/Litestar/Django docs require a compatibility matrix to link to.
4. **Regression risk** — Without framework tests in CI, future Pounce changes could silently break framework compatibility.

### Evidence Table

| Source | Finding | Proposal Impact |
|--------|---------|-----------------|
| `tests/integration/test_asgi_compliance.py` | Tests raw ASGI scope/lifecycle, no framework apps | FIXES — adds framework-specific integration tests |
| `tests/integration/test_bengal_compat.py` | Only Bengal/chirp static site tested | FIXES — extends to FastAPI, Starlette, Litestar, Django |
| `src/pounce/_output.py:175-178` | Framework detection for banner (FastAPI, Starlette, Litestar, Django) | ALIGNED — tests validate what the banner claims |
| `ROADMAP.md:38-48` | Q2 goal: "certify compatibility with every major ASGI framework" | ALIGNED |
| `docs/FEATURES.md:633` | Claims "Works with FastAPI, Starlette, Quart, Django, Flask (via asgiref)" | FIXES — backs the claim with tests |
| `src/pounce/asgi/bridge.py` | Scope construction, bodyless fast-path, compressor integration | VALIDATES — framework tests exercise these code paths |
| `src/pounce/asgi/lifespan.py` | Startup/shutdown with state sharing, timeout enforcement | VALIDATES — frameworks use lifespan differently |
| `src/pounce/worker.py` | WebSocket upgrade, disconnect monitoring, streaming | VALIDATES — frameworks exercise all three patterns |

### Invariants

These must remain true throughout or we stop and reassess:

1. **Existing test suite green**: `pytest tests/ -x -q --timeout=10` passes after every sprint. No regressions.
2. **Framework tests are optional**: Tests skip gracefully (`pytest.importorskip`) when framework deps aren't installed. Core CI never fails due to missing optional framework.
3. **No framework-specific code in Pounce**: Compatibility is achieved through correct ASGI implementation, not framework-specific hacks. If a framework fails, we fix the ASGI bridge — not add a workaround.

---

## Target Architecture

```
tests/
  integration/
    frameworks/                      # New directory
      conftest.py                    # Shared fixtures (server start, HTTP client)
      test_fastapi_compat.py         # FastAPI routes, deps, middleware, WebSocket, lifespan
      test_starlette_compat.py       # Starlette routing, middleware, streaming, WebSocket
      test_litestar_compat.py        # Litestar handlers, dependency injection, guards
      test_django_asgi_compat.py     # Django ASGI views, middleware, channels
      test_chirp_compat.py           # chirp framework (Bengal ecosystem)

docs/
  compatibility-matrix.md            # Public-facing matrix: framework × feature × status

pyproject.toml
  [project.optional-dependencies]
    test-frameworks = [...]          # Optional deps for framework compat tests
```

Each framework test file exercises the same **feature checklist**:

| Feature | What It Tests |
|---------|--------------|
| Basic routing | GET/POST/PUT/DELETE with path params, query params, JSON body |
| Middleware | Framework middleware stack executes correctly |
| Lifespan | startup/shutdown events, `scope["state"]` sharing |
| Streaming | SSE / StreamingResponse works end-to-end |
| WebSocket | Connect, send, receive, close |
| Background tasks | Starlette-style BackgroundTask completes after response |
| Error handling | Framework exception handlers produce correct responses |
| Static files | Framework-level static file mounting (if applicable) |
| Dependency injection | Framework DI resolves correctly (FastAPI Depends, Litestar DI) |
| HTTP/2 | Multiplexed requests through framework (if h2 installed) |

---

## Sprint Structure

| Sprint | Focus | Effort | Risk | Ships Independently? |
|--------|-------|--------|------|---------------------|
| 0 | Design: test infrastructure, feature checklist, skip strategy | 3h | Low | Yes (RFC only) |
| 1 | Starlette compatibility (baseline — FastAPI depends on it) | 6h | Low | Yes |
| 2 | FastAPI compatibility (most popular framework) | 6h | Medium | Yes |
| 3 | Litestar compatibility | 5h | Medium | Yes |
| 4 | Django ASGI compatibility | 6h | High | Yes |
| 5 | Matrix documentation, CI integration, chirp compat | 4h | Low | Yes |

---

## Sprint 0: Design & Validate

**Goal**: Establish test infrastructure and feature checklist before writing any framework tests.

### Task 0.1 — Shared test fixtures

Design `tests/integration/frameworks/conftest.py` with:
- `pounce_server(app, config)` fixture: starts a real Pounce worker, returns (host, port)
- `http_client(server)` fixture: httpx.AsyncClient pointed at the server
- `ws_client(server)` fixture: WebSocket test client
- Skip decorator: `@pytest.mark.framework("fastapi")` + `pytest.importorskip()`

**Files**: `tests/integration/frameworks/conftest.py`
**Acceptance**: Fixture module imports without error; `pytest --collect-only tests/integration/frameworks/` shows 0 tests (no test files yet)

### Task 0.2 — Optional dependency group

Add `test-frameworks` optional dependency group to `pyproject.toml`:
```toml
[project.optional-dependencies]
test-frameworks = ["fastapi>=0.110", "starlette>=0.40", "litestar>=2.0", "django>=5.0", "httpx>=0.27"]
```

**Files**: `pyproject.toml`
**Acceptance**: `uv sync --group dev --extra test-frameworks` installs all framework deps

### Task 0.3 — Feature checklist template

Create a test template documenting exactly which features each framework file must exercise. This becomes the contract for Sprints 1–4.

**Files**: `docs/plans/framework-compatibility-matrix.md` (this file, append checklist)
**Acceptance**: Checklist has 10+ feature rows with clear pass/fail criteria

---

## Sprint 1: Starlette Compatibility

**Goal**: Prove Pounce correctly serves Starlette apps — the foundation FastAPI builds on.

### Task 1.1 — Basic Starlette app tests

Test routing (Route, Mount), request/response, path params, query params, JSON body, form data.

**Files**: `tests/integration/frameworks/test_starlette_compat.py`
**Acceptance**: `pytest tests/integration/frameworks/test_starlette_compat.py -x -q` passes; covers GET, POST, path params, query params, JSON response

### Task 1.2 — Starlette middleware and lifespan

Test Starlette middleware stack (BaseHTTPMiddleware, pure ASGI middleware), lifespan with state.

**Acceptance**: Middleware modifies request/response correctly; `scope["state"]` populated from lifespan startup is accessible in route handlers

### Task 1.3 — Starlette streaming and WebSocket

Test StreamingResponse (SSE pattern), WebSocketRoute.

**Acceptance**: Streaming response delivers chunks; WebSocket echo works (connect, send, receive, close)

### Task 1.4 — Starlette background tasks

Test BackgroundTask completes after response is sent.

**Acceptance**: Response returns immediately; background task side-effect (file write) completes within 5s

---

## Sprint 2: FastAPI Compatibility

**Goal**: Prove Pounce correctly serves FastAPI apps — the most popular ASGI framework.

### Task 2.1 — FastAPI routing and dependency injection

Test path operations, Depends(), path/query/body params with Pydantic models.

**Files**: `tests/integration/frameworks/test_fastapi_compat.py`
**Acceptance**: `pytest tests/integration/frameworks/test_fastapi_compat.py -x -q` passes; covers GET/POST/PUT/DELETE, Depends(), Pydantic validation

### Task 2.2 — FastAPI middleware, exception handlers, lifespan

Test app middleware, custom exception handlers, lifespan context manager pattern.

**Acceptance**: Custom exception handler returns correct JSON error; lifespan state accessible

### Task 2.3 — FastAPI WebSocket and streaming

Test FastAPI WebSocket endpoints, StreamingResponse.

**Acceptance**: WebSocket endpoint works; EventSourceResponse (SSE) delivers events

### Task 2.4 — FastAPI with TestClient vs Pounce

Document any behavioral differences between FastAPI's built-in TestClient (Starlette/httpx) and running through Pounce.

**Acceptance**: Document written; no blocking differences found (or bugs filed)

---

## Sprint 3: Litestar Compatibility

**Goal**: Prove Pounce correctly serves Litestar apps.

### Task 3.1 — Litestar routing and DI

Test route handlers, dependency injection, guards, DTOs.

**Files**: `tests/integration/frameworks/test_litestar_compat.py`
**Acceptance**: `pytest tests/integration/frameworks/test_litestar_compat.py -x -q` passes

### Task 3.2 — Litestar middleware, lifespan, WebSocket

Test Litestar middleware, lifespan hooks, WebSocket handlers.

**Acceptance**: All three patterns work correctly through Pounce

### Task 3.3 — Litestar streaming and SSE

Test Litestar's Stream response and SSE support.

**Acceptance**: Streaming responses deliver correctly

---

## Sprint 4: Django ASGI Compatibility

**Goal**: Prove Pounce correctly serves Django in ASGI mode — the largest potential user base.

### Task 4.1 — Django ASGI views

Test Django async views, class-based views, URL routing through `get_asgi_application()`.

**Files**: `tests/integration/frameworks/test_django_asgi_compat.py`
**Acceptance**: `pytest tests/integration/frameworks/test_django_asgi_compat.py -x -q` passes

### Task 4.2 — Django middleware and static files

Test Django middleware stack in ASGI mode, Django's staticfiles serving.

**Acceptance**: Django middleware (CSRF, session, auth) executes correctly; static files served

### Task 4.3 — Django Channels WebSocket (stretch)

Test Django Channels WebSocket consumers if channels is installed.

**Acceptance**: Basic WebSocket consumer works, or documented as unsupported with reason

**Risk note**: Django ASGI has the most divergent implementation. `get_asgi_application()` wraps sync views in `sync_to_async`. This is the most likely sprint to discover Pounce bugs.

---

## Sprint 5: Matrix Documentation & CI

**Goal**: Publish the compatibility matrix and integrate framework tests into CI.

### Task 5.1 — Compatibility matrix document

Create `docs/compatibility-matrix.md` with framework × feature grid, version ranges tested, known limitations.

**Files**: `docs/compatibility-matrix.md`
**Acceptance**: Document covers all 4 frameworks + chirp; every cell has pass/fail/N-A status

### Task 5.2 — CI workflow

Add GitHub Actions job that installs framework deps and runs framework tests. Separate from main test suite (optional, non-blocking).

**Files**: `.github/workflows/framework-compat.yml`
**Acceptance**: `gh workflow run framework-compat` succeeds

### Task 5.3 — chirp compatibility test

Extend existing bengal compat test to cover chirp's ASGI app patterns (routing, middleware, lifespan).

**Files**: `tests/integration/frameworks/test_chirp_compat.py`
**Acceptance**: chirp app serves correctly through Pounce

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Framework discovers Pounce ASGI bug | Medium | High | Sprint 0 establishes inline ASGI compliance baseline; bugs found are high-value fixes |
| Django ASGI divergence blocks Sprint 4 | Medium | Medium | Django sprint is last; Sprints 1-3 deliver value independently |
| Framework API changes break tests | Low | Low | Pin minimum versions in `test-frameworks` extra; test against released versions only |
| Free-threading incompatibility in framework | Medium | Medium | Test under both GIL and nogil modes; document framework-specific nogil status |
| Test infrastructure too complex | Low | Medium | Sprint 0 designs fixtures before any framework tests; reuse existing `start_worker` pattern from conftest.py |

---

## Success Metrics

| Metric | Current | After Sprint 2 | After Sprint 5 |
|--------|---------|----------------|----------------|
| Frameworks tested | 1 (bengal static only) | 3 (Starlette, FastAPI, bengal) | 5 (+ Litestar, Django) |
| Framework integration tests | 1 file, ~5 tests | 3 files, ~40 tests | 6 files, ~70 tests |
| Features per framework | Static only | 8+ features each | 10 features each |
| Public compatibility matrix | None | Draft | Published |
| CI framework job | None | None | Green |

---

## Relationship to Existing Work

- **ASGI compliance tests** (`test_asgi_compliance.py`) — prerequisite, validates raw ASGI correctness. Framework tests build on this foundation.
- **Published benchmarks** (ROADMAP Q2) — parallel effort. Framework compat tests provide the apps that benchmarks can drive load against.
- **Framework outreach** (ROADMAP Q2) — downstream. The compatibility matrix is the artifact that enables PRs to framework docs.
- **RFC 9842 compression** (implemented) — framework tests should verify compression works through framework middleware stacks.

---

## Changelog

- 2026-04-12: Initial draft from codebase analysis and ROADMAP Q2 goals.
