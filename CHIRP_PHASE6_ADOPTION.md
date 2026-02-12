# Chirp Phase 6 Adoption Guide

Analysis of what chirp needs to update to take full advantage of pounce Phase 6 features.

## Current State

**Chirp currently only uses pounce for development:**
- Single worker mode (`workers=1`)
- Development server only (`run_dev_server()`)
- No production configuration
- No Phase 6 feature exposure

**Files using pounce:**
- `src/chirp/server/dev.py` - Dev server setup
- `src/chirp/cli/_run.py` - `chirp run` command
- `src/chirp/app.py` - `app.run()` method

---

## TL;DR: What Needs to Change?

### 🟢 **ZERO CHANGES REQUIRED** for Phase 6 features to work!

Your existing chirp apps automatically benefit from:
- ✅ All Phase 6 features work through pounce config
- ✅ No chirp code changes needed
- ✅ Apps remain framework-agnostic

### 🟡 **OPTIONAL ENHANCEMENTS** for better DX:

1. Add production server utilities
2. Expose Phase 6 config in `AppConfig`
3. Add CLI flags for production features
4. Add deployment documentation

---

## Detailed Analysis

### 1. No Changes Required (It Just Works!)

Any chirp app can immediately use Phase 6 features by configuring pounce directly:

**Current chirp app (no changes):**
```python
from chirp import App

app = App()

@app.route("/")
def index():
    return "Hello, World!"

app.run()  # ← Uses pounce under the hood
```

**Enable Phase 6 features externally:**
```python
# production.py - Run with Phase 6 features enabled
from myapp import app  # Import your chirp app
from pounce import run, ServerConfig

config = ServerConfig(
    # Enable all Phase 6 features
    metrics_enabled=True,
    rate_limit_enabled=True,
    request_queue_enabled=True,
    sentry_dsn="...",
    workers=4,
    # ... all other config
)

# Run the chirp app with production config
run(app, config=config)
```

**Result:** ✅ All Phase 6 features work with zero chirp changes!

---

### 2. Optional Enhancement #1: Production Server Utility

**Problem:** Chirp only has `run_dev_server()`, no production equivalent.

**Solution:** Add `src/chirp/server/production.py`

```python
"""Production server with pounce Phase 6 features.

Starts a pounce production server with multi-worker,
metrics, rate limiting, queueing, and error tracking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chirp import App


def run_production_server(
    app: App,
    host: str = "0.0.0.0",
    port: int = 8000,
    workers: int = 0,  # 0 = auto-detect
    *,
    # Phase 6.1: Prometheus Metrics
    metrics_enabled: bool = True,
    metrics_path: str = "/metrics",

    # Phase 6.2: Rate Limiting
    rate_limit_enabled: bool = False,
    rate_limit_requests_per_second: float = 100.0,
    rate_limit_burst: int = 200,

    # Phase 6.3: Request Queueing
    request_queue_enabled: bool = False,
    request_queue_max_depth: int = 1000,

    # Phase 6.4: Sentry
    sentry_dsn: str | None = None,
    sentry_environment: str | None = None,
    sentry_release: str | None = None,

    # Phase 6.5: Hot Reload
    reload_timeout: float = 30.0,

    # Additional config
    lifecycle_logging: bool = True,
    log_format: str = "json",
) -> None:
    """Run chirp app in production mode with Phase 6 features.

    Args:
        app: Chirp App instance
        host: Bind address (default: 0.0.0.0)
        port: Bind port (default: 8000)
        workers: Worker count (0 = auto, default: 0)
        metrics_enabled: Enable /metrics endpoint
        rate_limit_enabled: Enable per-IP rate limiting
        request_queue_enabled: Enable request queueing
        sentry_dsn: Sentry DSN for error tracking
        ...

    Example:
        >>> from myapp import app
        >>> from chirp.server.production import run_production_server
        >>> run_production_server(
        ...     app,
        ...     workers=4,
        ...     metrics_enabled=True,
        ...     rate_limit_enabled=True,
        ... )
    """
    from pounce.config import ServerConfig
    from pounce.server import Server

    config = ServerConfig(
        host=host,
        port=port,
        workers=workers,

        # Phase 6 features
        metrics_enabled=metrics_enabled,
        metrics_path=metrics_path,
        rate_limit_enabled=rate_limit_enabled,
        rate_limit_requests_per_second=rate_limit_requests_per_second,
        rate_limit_burst=rate_limit_burst,
        request_queue_enabled=request_queue_enabled,
        request_queue_max_depth=request_queue_max_depth,
        sentry_dsn=sentry_dsn,
        sentry_environment=sentry_environment,
        sentry_release=sentry_release,
        reload_timeout=reload_timeout,

        # Production settings
        lifecycle_logging=lifecycle_logging,
        log_format=log_format,

        # Use chirp's health check if configured
        health_check_path="/health" if hasattr(app, "health_check") else None,
    )

    server = Server(config, app)
    server.run()
```

**Usage in chirp apps:**
```python
from chirp import App
from chirp.server.production import run_production_server

app = App()

@app.route("/")
def index():
    return "Hello!"

if __name__ == "__main__":
    run_production_server(
        app,
        workers=4,
        metrics_enabled=True,
        rate_limit_enabled=True,
    )
```

---

### 3. Optional Enhancement #2: Expose in AppConfig

**Problem:** Users have to import pounce directly for production features.

**Solution:** Add Phase 6 options to `src/chirp/config.py`

```python
@dataclass(frozen=True, slots=True)
class AppConfig:
    """Application configuration. Immutable after creation."""

    # ... existing fields ...

    # Production (Phase 6 features)
    workers: int = 0  # 0 = auto-detect (multi-worker for production)

    # Phase 6.1: Prometheus Metrics
    metrics_enabled: bool = False
    metrics_path: str = "/metrics"

    # Phase 6.2: Rate Limiting
    rate_limit_enabled: bool = False
    rate_limit_requests_per_second: float = 100.0
    rate_limit_burst: int = 200

    # Phase 6.3: Request Queueing
    request_queue_enabled: bool = False
    request_queue_max_depth: int = 1000

    # Phase 6.4: Sentry
    sentry_dsn: str | None = None
    sentry_environment: str | None = None
    sentry_release: str | None = None

    # Phase 6.5: Hot Reload
    reload_timeout: float = 30.0
```

**Update `app.run()` to use production config when `debug=False`:**

```python
# In src/chirp/app.py

def run(self, host: str | None = None, port: int | None = None) -> None:
    """Start the server (dev or production based on config.debug)."""
    host = host or self.config.host
    port = port or self.config.port

    if self.config.debug:
        # Development mode (existing behavior)
        from chirp.server.dev import run_dev_server
        run_dev_server(
            self, host, port,
            reload=True,
            reload_include=self.config.reload_include,
            reload_dirs=self.config.reload_dirs,
        )
    else:
        # Production mode (new!)
        from chirp.server.production import run_production_server
        run_production_server(
            self,
            host=host,
            port=port,
            workers=self.config.workers,
            metrics_enabled=self.config.metrics_enabled,
            rate_limit_enabled=self.config.rate_limit_enabled,
            # ... pass all Phase 6 config
        )
```

**Result:** Users can now configure everything via `AppConfig`:

```python
from chirp import App, AppConfig

config = AppConfig(
    debug=False,  # ← Production mode
    workers=4,
    metrics_enabled=True,
    rate_limit_enabled=True,
    sentry_dsn="https://...",
)

app = App(config=config)

@app.route("/")
def index():
    return "Hello!"

app.run()  # ← Automatically uses production mode with Phase 6 features!
```

---

### 4. Optional Enhancement #3: CLI Flags

**Problem:** `chirp run` only supports dev mode.

**Solution:** Add production flags to CLI

```python
# In src/chirp/cli/_run.py

def run_server(args: argparse.Namespace) -> None:
    """Start the chirp server (dev or production)."""
    app = resolve_app(args.app)

    host = args.host or app.config.host
    port = args.port or app.config.port

    # Check if production mode requested
    if args.production or not app.config.debug:
        from chirp.server.production import run_production_server

        run_production_server(
            app,
            host=host,
            port=port,
            workers=args.workers or app.config.workers,
            metrics_enabled=args.metrics,
            rate_limit_enabled=args.rate_limit,
            # ...
        )
    else:
        # Development mode (existing)
        from chirp.server.dev import run_dev_server
        run_dev_server(app, host, port, ...)
```

**Add CLI arguments:**
```python
parser.add_argument("--production", action="store_true",
                   help="Run in production mode (multi-worker)")
parser.add_argument("--workers", type=int, default=0,
                   help="Worker count (0=auto-detect)")
parser.add_argument("--metrics", action="store_true",
                   help="Enable /metrics endpoint")
parser.add_argument("--rate-limit", action="store_true",
                   help="Enable per-IP rate limiting")
parser.add_argument("--queue", action="store_true",
                   help="Enable request queueing")
parser.add_argument("--sentry-dsn", type=str,
                   help="Sentry DSN for error tracking")
```

**Usage:**
```bash
# Development (existing)
chirp run myapp:app

# Production with Phase 6 features
chirp run myapp:app --production --workers 4 --metrics --rate-limit
```

---

### 5. Optional Enhancement #4: Sentry Integration

**Problem:** Sentry captures exceptions but doesn't know about chirp users.

**Solution:** Add chirp middleware to set Sentry user context

```python
# New file: src/chirp/middleware/sentry.py

"""Sentry middleware for chirp apps.

Automatically sets user context in Sentry based on chirp auth.
"""

def sentry_middleware(handler):
    """Middleware that sets Sentry user context."""
    async def middleware(request):
        # If Sentry is enabled and user is authenticated
        if request.user and request.user.is_authenticated:
            try:
                from pounce._sentry import set_user

                set_user(
                    user_id=str(request.user.id),
                    email=getattr(request.user, "email", None),
                    username=getattr(request.user, "username", None),
                )
            except ImportError:
                pass  # Sentry not installed

        return await handler(request)

    return middleware
```

**Usage:**
```python
from chirp import App
from chirp.middleware.sentry import sentry_middleware

app = App()
app.use(sentry_middleware)  # ← Auto-sets user context in Sentry
```

---

## Summary: What Should Chirp Do?

### Recommendation: Start with Documentation

**Why?** Phase 6 features already work! Just need to document them.

**Minimal effort, maximum value:**

1. **Add production deployment guide** (`docs/deployment/production.md`)
   - Show how to use pounce Phase 6 features with chirp
   - Include example production.py script
   - Kubernetes/Docker examples

2. **Add to README** (quick wins section)
   ```markdown
   ## Production Deployment

   Chirp apps run on pounce, which includes production-grade features:
   - Prometheus metrics for monitoring
   - Per-IP rate limiting for abuse protection
   - Request queueing for overload handling
   - Sentry integration for error tracking
   - Zero-downtime hot reload

   See [Production Deployment Guide](docs/deployment/production.md)
   ```

### Optional: Add Production Utilities

If you want first-class production support in chirp:

**Priority 1: Production server utility**
- Add `chirp.server.production.run_production_server()`
- Simple wrapper around pounce with Phase 6 config
- ~50 lines of code

**Priority 2: CLI production mode**
- Add `chirp run --production` flag
- Add CLI flags for Phase 6 features
- ~100 lines of code

**Priority 3: AppConfig integration**
- Add Phase 6 fields to `AppConfig`
- Update `app.run()` to detect production mode
- ~50 lines of code

**Priority 4: Sentry middleware**
- Auto-set user context in Sentry
- ~30 lines of code

---

## Timeline

**Immediate (Week 1):** Documentation
- Production deployment guide
- README updates
- Examples

**Short-term (Week 2-3):** Production utilities
- `run_production_server()` helper
- Example production scripts

**Medium-term (Month 1):** CLI integration
- `chirp run --production`
- CLI flags for features

**Long-term (Month 2):** Framework integration
- AppConfig fields
- Auto-detection of production mode
- Sentry middleware

---

## Bottom Line

**Do you NEED to change chirp?** ❌ No!
- Phase 6 works today with any chirp app
- Users can configure pounce directly
- Zero breaking changes

**Should you ENHANCE chirp?** ✅ Yes, for DX!
- Better developer experience
- One-line production setup
- Consistent configuration API

**Effort required?** 🟢 Minimal
- ~200 lines total code
- Mostly documentation
- No breaking changes
