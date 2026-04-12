# Phase 5b: Production Competitiveness — Executive Summary

**Date:** February 12, 2026
**Status:** Complete (all features shipped in v0.3.0–v0.5.1)
**Target:** Pounce v0.2.0 — Production-ready ASGI server for Bengal & Chirp

---

## Why Phase 5b?

Pounce has completed Phase 5a (Production Grade) with excellent security hardening and observability foundations. However, it still has **critical gaps** that prevent it from being a competitive Uvicorn/Hypercorn alternative in 2026:

1. **No static file serving** → Bengal SSG sites require Nginx in front
2. **Lifespan state missing** → ASGI 3.0 spec non-compliance
3. **No middleware hooks** → Chirp apps can't inject auth/CORS/rate limiting
4. **No graceful reload** → Production deploys require container restarts
5. **Missing modern features** → WS compression, OTel, rich dev errors

**Phase 5b solves these gaps** to make pounce production-ready for 2026.

---

## What We're Building

### 10 Features Across 3 Categories

#### Production Features (4)
1. **Static File Serving** — Zero-copy sendfile, ETag, Range, precompressed
2. **Graceful Worker Reload** — SIGHUP triggers zero-downtime rolling restart
3. **Connection Draining** — Clean SIGTERM shutdown for Kubernetes
4. **Structured Logging** — Rich JSON lifecycle events for production debugging

#### ASGI Spec & Extensibility (3)
5. **Lifespan State** — ASGI 3.0 compliant state dict sharing
6. **Middleware System** — Pre-request, post-response, exception hooks
7. **OpenTelemetry** — Distributed tracing with W3C context propagation

#### Developer Experience (2)
8. **Rich Error Pages** — Syntax-highlighted tracebacks with Rosettes (dev mode only)
9. **WebSocket Compression** — RFC 7692 permessage-deflate (60-80% bandwidth reduction)

#### Future Roadmap (1)
10. **HTTP/3 Spike** — Research aioquic, design doc, prototype, decision

---

## Impact on Bengal & Chirp

### Bengal (SSG)
- ✅ **Serve sites directly** without Nginx (static files + precompressed)
- ✅ **Development flow:** `pounce` watches templates, auto-reloads
- ✅ **Production deploys:** Graceful reload on new build
- ✅ **Performance:** Zero-copy sendfile matches Nginx

### Chirp (Hypermedia/HTMX)
- ✅ **Middleware:** Auth, CORS, rate limiting without forking bridge
- ✅ **Lifespan state:** DB pools, caches initialized at startup
- ✅ **Rich errors:** Syntax-highlighted tracebacks in dev (Rosettes!)
- ✅ **WebSocket:** Compressed for real-time features (if needed)
- ✅ **Observability:** OTel traces for distributed debugging

---

## Implementation Plan

### 5 Waves Over 4-6 Weeks

```
Wave 1 (Week 1-2): Foundation
├─ Static Files (P0) ............... 3-5 days
├─ Lifespan State (P0) ............. 1-2 days
└─ Dev Error Pages (P0) ............ 2-3 days

Wave 2 (Week 2-3): Extensibility
├─ Middleware System (P0) .......... 3-4 days
└─ WebSocket Compression (P1) ...... 2-3 days

Wave 3 (Week 3-4): Production Ops
├─ Graceful Reload (P0) ............ 4-6 days
└─ Connection Draining (P1) ........ 3-4 days

Wave 4 (Week 4-5): Observability
├─ Structured Logging (P1) ......... 2-3 days
└─ OpenTelemetry (P1) .............. 3-4 days

Wave 5 (Week 5-6): Future Roadmap
└─ HTTP/3 Spike (P1) ............... 3-5 days
```

**Total Effort:** 27-39 working days (5-8 weeks solo, 3-4 weeks with 2 devs)

---

## Technical Highlights

### Static File Serving
- **Zero-copy sendfile** on Linux/macOS (os.sendfile)
- **Precompressed variants** (.gz, .zst) served automatically
- **ETag caching** with 304 Not Modified
- **Range requests** for video/audio streaming (206 Partial Content)
- **Security:** Path traversal prevention, hidden file blocking

### Middleware System
```python
# Pre-request hook (can short-circuit)
async def auth_middleware(scope):
    if not scope["headers"].get("authorization"):
        return Response(status=401)  # Short-circuit
    return scope  # Continue to app

# Post-response hook
async def cors_middleware(scope, status, headers):
    headers.append(("access-control-allow-origin", "*"))
    return (status, headers)

# Usage
config = ServerConfig(middleware=[auth_middleware, cors_middleware])
```

### Graceful Reload
```bash
# Send SIGHUP to supervisor
kill -HUP $(cat pounce.pid)

# What happens:
1. Spawn new workers with updated code
2. Old workers stop accepting new connections
3. Old workers finish in-flight requests (30s timeout)
4. Old workers shutdown gracefully
5. Zero dropped requests
```

---

## Testing Strategy

### Quantitative Goals
- **500+ tests** (currently 426, adding 83 new + 34 updated)
- **85%+ code coverage** (currently ~80%)
- **Zero regressions** in existing benchmarks
- **Performance:** Static files within 10% of Nginx

### Integration Testing
- **Bengal SSG:** Serve full site, verify ETag, precompressed, 304
- **Chirp app:** Middleware, lifespan state, rich errors
- **Graceful reload:** 1000 req/s load, SIGHUP, zero drops
- **Kubernetes:** SIGTERM drain, clean shutdown

---

## Release Plan

### Alpha (After Wave 1)
**Tag:** `v0.2.0-alpha.1`
**Features:** Static files, lifespan state, dev error pages
**Audience:** Bengal/Chirp dogfooding
**Timeline:** End of Week 2

### Beta (After Wave 3)
**Tag:** `v0.2.0-beta.1`
**Features:** Add middleware, reload, WS compression
**Audience:** Early adopters, production testing
**Timeline:** End of Week 4

### Stable (After Wave 4)
**Tag:** `v0.2.0`
**Features:** Add structured logging, OTel
**Announcement:** "Production-ready for 2026"
**Timeline:** End of Week 6

### Future (After Wave 5)
**Tag:** `v0.3.0` (if H3 implemented) or `v0.2.1` (if deferred)
**Decision:** HTTP/3 full implementation or Phase 5c deferral
**Timeline:** TBD based on spike results

---

## Success Metrics

### Technical
- [ ] 500+ tests passing
- [ ] 85%+ code coverage
- [ ] Zero benchmark regressions
- [ ] Static serving within 10% of Nginx
- [ ] Graceful reload <1ms latency spike

### Product
- [ ] Bengal sites deploy without Nginx
- [ ] Chirp apps use middleware (3+ examples)
- [ ] 1+ production deployment with OTel
- [ ] 5+ external adopters (GitHub stars, issues, feedback)

### Ecosystem
- [ ] Benchmarks showing parity with Uvicorn 0.38+
- [ ] Blog post: "Pounce 0.2: Production-Ready Free-Threading"
- [ ] Conference talk submission (PyCon 2026?)
- [ ] Featured on Awesome ASGI list

---

## Risk Assessment

### Performance Regression
**Risk:** Medium
**Mitigation:** Benchmark every feature, fast-path unchanged (static bypasses ASGI, middleware optional)

### Scope Creep
**Risk:** Medium
**Mitigation:** Strict P0 adherence, defer P1 items to Phase 5c if needed

### HTTP/3 Complexity
**Risk:** High
**Mitigation:** Wave 5 is research only, full impl deferred to Phase 5c if too complex

### API Instability
**Risk:** Low
**Mitigation:** All features opt-in via config, existing apps unchanged

---

## Comparison: Before vs After Phase 5b

| Feature | Before 5b | After 5b | Competitor |
|---------|-----------|----------|------------|
| Static files | ❌ Need Nginx | ✅ Zero-copy sendfile | Uvicorn ✅ |
| Lifespan state | ❌ Non-compliant | ✅ ASGI 3.0 spec | Uvicorn ✅ |
| Middleware | ❌ App-level only | ✅ Server hooks | Uvicorn ✅ |
| Graceful reload | ⚠️ Dev only | ✅ Zero-downtime | Gunicorn ✅ |
| WS compression | ❌ No | ✅ permessage-deflate | Hypercorn ✅ |
| Dev errors | ⚠️ Basic | ✅ Rich tracebacks | FastAPI ✅ |
| OpenTelemetry | ❌ No | ✅ W3C traces | Granian ✅ |
| HTTP/2 | ✅ Yes | ✅ Yes | Uvicorn ❌ |
| HTTP/3 | ❌ No | ⚠️ Roadmap | Hypercorn ✅ |
| Free-threading | ✅ Native | ✅ Native | Uvicorn ⚠️ |

**After Phase 5b:** Pounce matches or exceeds all major competitors except HTTP/3 (which is Phase 5c roadmap).

---

## Resource Requirements

### Development
- **1-2 developers** full-time for 4-6 weeks
- **Skills needed:** Python 3.14t, asyncio, ASGI, HTTP specs, systems programming
- **Tools:** pytest, ruff, ty, wrk/hey (benchmarking), strace (profiling)

### Testing
- **CI/CD:** GitHub Actions (already set up)
- **Environments:** Linux, macOS, Python 3.14t
- **External:** Bengal test site, Chirp test app, OTel collector (Docker)

### Documentation
- **Writer:** 1 person, 3-5 days for all new docs
- **Bengal generator:** Already available for site build
- **Examples:** 5+ new example apps in `examples/`

---

## Next Steps

### Immediate (Today)
1. ✅ Review this executive summary
2. ✅ Review detailed plan: `PLAN_PHASE_5B.md`
3. ✅ Review task breakdown: `phase_5b_changes_summary.md`
4. 🔲 Approve plan (stakeholder sign-off)

### This Week
5. 🔲 Create branch: `git checkout -b phase-5b`
6. 🔲 Start Task #1.1 (Static Files) — see `phase_5b_1_static_files.md`
7. 🔲 Setup tracking: GitHub project board or task list
8. 🔲 Daily standup: progress, blockers, pivots

### Week 2
9. 🔲 Complete Wave 1 (Static, Lifespan, Errors)
10. 🔲 Alpha release: `v0.2.0-alpha.1`
11. 🔲 Dogfood with Bengal documentation site
12. 🔲 Start Wave 2 (Middleware, WS)

---

## Questions & Decisions

### Open Questions
1. **Static files:** Support custom handlers (image resizing)?
   → **NO**, keep minimal for v0.2.0

2. **Middleware:** Support ASGI middleware protocol (class-based)?
   → **YES**, add compat layer for Starlette/FastAPI middleware

3. **HTTP/3:** Is aioquic stable enough?
   → **RESEARCH** in Wave 5, decide after spike

4. **Reload:** Can thread-mode graceful reload work?
   → **PROBABLY NOT**, document as process-mode only

5. **OTel:** Vendor opentelemetry-api to avoid deps?
   → **NO**, keep optional dependency

### Approved Decisions
- ✅ Static files are built-in (not middleware-only)
- ✅ Lifespan state is per-worker (no cross-worker sharing)
- ✅ Dev error pages only active in reload mode (security)
- ✅ Middleware is optional (empty list = no-op, zero overhead)
- ✅ HTTP/3 is Phase 5c (not blocking v0.2.0)

---

## Conclusion

**Phase 5b transforms pounce from a promising experimental server into a production-ready Uvicorn alternative.**

Key outcomes:
- **Bengal sites** can deploy without Nginx (static files)
- **Chirp apps** can extend server behavior (middleware)
- **Production deploys** have zero downtime (graceful reload)
- **Developer experience** matches FastAPI (rich errors)
- **Observability** integrates with modern stacks (OTel)

**Timeline:** 4-6 weeks
**Risk:** Low (incremental features, opt-in)
**Impact:** High (production readiness for 2026)

**Approval needed to proceed.**

---

**Prepared by:** Claude Sonnet 4.5
**Reviewed by:** [Pending stakeholder review]
**Approved by:** [Pending]
**Date:** 2026-02-12
