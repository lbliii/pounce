# Sentry Error Tracking

**Phase 6.4 Complete: Sentry Error Tracking Integration** ✅

Optional integration with Sentry for automatic error reporting, performance monitoring, and request context capture.

## Overview

Sentry integration provides production error tracking:
- **Automatic error capture** - Exceptions captured from ASGI apps
- **Request context** - Full request details in error reports
- **Performance monitoring** - Transaction tracking and profiling
- **Breadcrumbs** - Debug context for error reports
- **User tracking** - Associate errors with users
- **Release tracking** - Track errors by deployment

### Why Sentry?

**Production error tracking:**
- Real-time error alerts
- Stack traces with context
- Error grouping and deduplication
- Performance monitoring
- Release health tracking

**Better debugging:**
- Full request/response context
- Breadcrumb trail of events
- User session replay
- Source maps for minified code

## Quick Start

### Installation

Sentry SDK is an **optional dependency**:

```bash
pip install sentry-sdk
```

Or add to `requirements.txt`:
```
sentry-sdk>=2.0.0
```

### Basic Configuration

Enable Sentry with your DSN:

```python
from pounce import ServerConfig

config = ServerConfig(
    sentry_dsn="https://examplePublicKey@o0.ingest.sentry.io/0",
    sentry_environment="production",
    sentry_release="myapp@1.0.0",
)
```

### Get Your DSN

1. Sign up at [sentry.io](https://sentry.io)
2. Create a new project
3. Copy the DSN from project settings
4. Add to your config

## Configuration Options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `sentry_dsn` | str \| None | `None` | Sentry DSN (None = disabled) |
| `sentry_environment` | str \| None | `None` | Environment name (e.g., "production") |
| `sentry_release` | str \| None | `None` | Release version (e.g., "myapp@1.0.0") |
| `sentry_traces_sample_rate` | float | `0.1` | Performance monitoring sample rate (0.0-1.0) |
| `sentry_profiles_sample_rate` | float | `0.1` | Profiling sample rate (0.0-1.0) |

## How It Works

### Automatic Error Capture

Exceptions in your ASGI app are automatically captured:

```python
from pounce import run, ServerConfig

async def app(scope, receive, send):
    # Any exception here will be captured by Sentry
    raise ValueError("Something went wrong!")

config = ServerConfig(
    sentry_dsn="https://example@o0.ingest.sentry.io/0",
)

run(app, config=config)
```

### Request Context

Every error includes full request context:
- HTTP method (GET, POST, etc.)
- URL and path
- Headers (sanitized for security)
- Query parameters
- Request body (when configured)
- Client IP address
- User agent

### Performance Monitoring

Track request performance automatically:
- Request duration
- Database query times
- External API calls
- Custom spans

Sample rate controls overhead:
- `0.0` - Disabled
- `0.1` - 10% of requests (default)
- `1.0` - 100% of requests (high overhead)

## Examples

### Production Deployment

Full production configuration:

```python
from pounce import run, ServerConfig

config = ServerConfig(
    # Sentry error tracking
    sentry_dsn="https://example@o0.ingest.sentry.io/0",
    sentry_environment="production",
    sentry_release="myapp@1.2.3",
    sentry_traces_sample_rate=0.1,  # 10% performance sampling

    # Also enable other production features
    metrics_enabled=True,
    rate_limit_enabled=True,
    request_queue_enabled=True,
)

run("myapp:app", config=config)
```

### Staging Environment

Different config for staging:

```python
config = ServerConfig(
    sentry_dsn="https://example@o0.ingest.sentry.io/0",
    sentry_environment="staging",
    sentry_release="myapp@1.2.3-rc1",
    sentry_traces_sample_rate=0.5,  # Higher sampling in staging
)
```

### Development

Disable Sentry in development:

```python
import os

config = ServerConfig(
    # Only enable in production
    sentry_dsn=os.getenv("SENTRY_DSN"),
    sentry_environment=os.getenv("ENVIRONMENT", "development"),
)
```

### Environment Variables

Configure via environment variables:

```bash
export SENTRY_DSN="https://example@o0.ingest.sentry.io/0"
export SENTRY_ENVIRONMENT="production"
export SENTRY_RELEASE="myapp@1.0.0"

pounce run myapp:app
```

```python
import os

config = ServerConfig(
    sentry_dsn=os.getenv("SENTRY_DSN"),
    sentry_environment=os.getenv("SENTRY_ENVIRONMENT"),
    sentry_release=os.getenv("SENTRY_RELEASE"),
)
```

## Manual Instrumentation

### Capture Exceptions

Manually capture exceptions with context:

```python
from pounce._sentry import capture_exception

try:
    risky_operation()
except Exception as e:
    capture_exception(
        e,
        level="error",
        tags={"component": "payment"},
        extra={"order_id": order.id},
    )
    # Handle error gracefully
```

### Capture Messages

Log important events:

```python
from pounce._sentry import capture_message

capture_message(
    "Unusual login pattern detected",
    level="warning",
    tags={"component": "auth"},
    extra={"user_id": user.id, "ip": request.client.host},
)
```

### Add Breadcrumbs

Leave breadcrumb trail for debugging:

```python
from pounce._sentry import add_breadcrumb

# Add breadcrumbs throughout request handling
add_breadcrumb("Database query started", category="db")
result = db.query("SELECT * FROM users")
add_breadcrumb("Database query completed", category="db", data={"rows": len(result)})

# If an error occurs, breadcrumbs are included in report
```

### Set User Context

Associate errors with users:

```python
from pounce._sentry import set_user

# After authentication
set_user(
    user_id=str(user.id),
    email=user.email,
    username=user.username,
    ip_address=request.client.host,
)
```

### Add Tags

Tag errors for filtering:

```python
from pounce._sentry import set_tag

set_tag("tenant", "acme-corp")
set_tag("feature_flag", "new_checkout")
```

### Add Context

Add structured context:

```python
from pounce._sentry import set_context

set_context("payment", {
    "provider": "stripe",
    "amount": 99.99,
    "currency": "USD",
})
```

### Track Transactions

Manual performance tracking:

```python
from pounce._sentry import start_transaction

with start_transaction("process_order", op="task"):
    # Business logic here
    process_payment(order)
    send_confirmation(order)
```

## Best Practices

### Release Tracking

Always set release version:

```python
# Use git commit SHA or semantic version
config = ServerConfig(
    sentry_release=f"myapp@{GIT_SHA}",
    # or
    sentry_release="myapp@1.2.3",
)
```

Benefits:
- Track which releases have errors
- See when errors were introduced
- Automatic error resolution when fixed

### Environment Configuration

Use different environments:

```python
config = ServerConfig(
    sentry_environment=os.getenv("ENVIRONMENT", "development"),
)
```

Common environments:
- `production` - Live traffic
- `staging` - Pre-production testing
- `development` - Local development
- `ci` - Continuous integration

### Sampling Strategy

Choose appropriate sample rates:

**Production (high traffic):**
```python
config = ServerConfig(
    sentry_traces_sample_rate=0.01,  # 1% (low overhead)
    sentry_profiles_sample_rate=0.01,
)
```

**Staging (moderate traffic):**
```python
config = ServerConfig(
    sentry_traces_sample_rate=0.5,  # 50% (more data)
    sentry_profiles_sample_rate=0.1,
)
```

**Development (low traffic):**
```python
config = ServerConfig(
    sentry_traces_sample_rate=1.0,  # 100% (all requests)
    sentry_profiles_sample_rate=1.0,
)
```

### Sensitive Data

Sentry automatically scrubs sensitive data:
- Passwords (in any field)
- API keys
- Access tokens
- Credit card numbers

Additional scrubbing:

```python
# In your app initialization
import sentry_sdk

sentry_sdk.init(
    before_send=scrub_sensitive_data,
)

def scrub_sensitive_data(event, hint):
    # Remove sensitive fields
    if "request" in event:
        if "headers" in event["request"]:
            event["request"]["headers"].pop("Authorization", None)
    return event
```

### Error Grouping

Sentry groups similar errors automatically. Improve grouping with fingerprints:

```python
from pounce._sentry import capture_exception

try:
    process_file(filename)
except FileNotFoundError as e:
    # Group by error type, not filename
    capture_exception(
        e,
        extra={
            "fingerprint": ["file-not-found", "process_file"],
        },
    )
```

### Before Shutdown

Flush events before shutdown:

```python
from pounce._sentry import flush

# Before shutdown
flush(timeout=5.0)  # Wait up to 5 seconds
```

## Kubernetes Deployment

### ConfigMap for DSN

Store DSN in ConfigMap:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: pounce-config
data:
  SENTRY_DSN: "https://example@o0.ingest.sentry.io/0"
  SENTRY_ENVIRONMENT: "production"
```

### Deployment with Sentry

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pounce-api
spec:
  template:
    spec:
      containers:
      - name: pounce
        image: myapp:1.0.0
        env:
        - name: SENTRY_DSN
          valueFrom:
            configMapKeyRef:
              name: pounce-config
              key: SENTRY_DSN
        - name: SENTRY_ENVIRONMENT
          valueFrom:
            configMapKeyRef:
              name: pounce-config
              key: SENTRY_ENVIRONMENT
        - name: SENTRY_RELEASE
          value: "myapp@1.0.0"  # Set during deployment
```

### Automatic Release Creation

Create releases during deployment:

```bash
# In CI/CD pipeline
export SENTRY_AUTH_TOKEN="your-auth-token"
export SENTRY_ORG="your-org"
export SENTRY_PROJECT="your-project"

# Create release
sentry-cli releases new "myapp@${VERSION}"

# Associate commits
sentry-cli releases set-commits "myapp@${VERSION}" --auto

# Finalize release
sentry-cli releases finalize "myapp@${VERSION}"

# Deploy
kubectl apply -f deployment.yaml
```

## Alerts and Notifications

### Email Alerts

Configure in Sentry UI:
1. Project Settings → Alerts
2. Create alert rule
3. Choose notification channel (email, Slack, PagerDuty)

### Alert Conditions

Example alert rules:
- **High error rate:** > 100 errors/minute
- **New errors:** First occurrence of error
- **Regression:** Error reappears after being resolved
- **Performance:** P95 latency > 1 second

### Slack Integration

1. Install Sentry Slack app
2. Configure in Sentry UI
3. Choose channels for different environments

## Troubleshooting

### No Events in Sentry

If events aren't appearing:

1. **Check DSN:** Verify DSN is correct
2. **Check SDK:** Ensure `sentry-sdk` is installed
3. **Check logs:** Look for Sentry initialization messages
4. **Test manually:**

```python
from pounce._sentry import capture_message

capture_message("Test from pounce", level="info")
```

### High Overhead

If Sentry impacts performance:

1. **Lower sample rates:**
```python
config = ServerConfig(
    sentry_traces_sample_rate=0.01,  # 1% instead of 10%
)
```

2. **Disable profiling:**
```python
config = ServerConfig(
    sentry_profiles_sample_rate=0.0,  # Disable
)
```

3. **Filter events:**
```python
import sentry_sdk

sentry_sdk.init(
    before_send=filter_events,
)

def filter_events(event, hint):
    # Don't send 404 errors
    if event.get("status_code") == 404:
        return None
    return event
```

### Too Many Events

If you're hitting Sentry quota:

1. **Increase sample rate:**
```python
config = ServerConfig(
    sentry_traces_sample_rate=0.05,  # Reduce from 0.1
)
```

2. **Filter noisy errors:**
```python
sentry_sdk.init(
    ignore_errors=[
        KeyboardInterrupt,
        # Add other errors to ignore
    ],
)
```

3. **Upgrade Sentry plan** - Higher quota limits

## Performance Impact

Sentry integration adds minimal overhead:
- **Error capture:** ~1-5ms per error (rare)
- **Performance tracking:** ~0.1-1ms per request (at 10% sample rate)
- **Breadcrumbs:** ~0.01ms per breadcrumb
- **Async sending:** Events sent in background

For 10% sampling:
- CPU: <0.5% additional load
- Memory: ~50 KB per worker
- Network: ~5-10 KB per event

## What's Next?

**Phase 6.5: Hot Reload without Connection Drops** 🚀

Next feature adds:
- Zero-downtime code reloads
- Worker replacement without dropping connections
- Graceful worker handoff
- Version tracking across workers

---

**See Also:**
- [Prometheus Metrics](prometheus-metrics.md) - Monitor error rates
- [Lifecycle Logging](../features/lifecycle-logging.md) - Structured error logging
- [Graceful Shutdown](graceful-shutdown.md) - Clean shutdown with Sentry flush
