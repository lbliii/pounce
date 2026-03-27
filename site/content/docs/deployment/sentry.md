---
title: Sentry
description: Automatic error tracking and performance monitoring with Sentry
draft: false
weight: 100
lang: en
type: doc
tags: [deployment, observability, sentry, error-tracking]
keywords: [sentry, error tracking, performance monitoring, exception capture, breadcrumbs]
category: how-to
---

Optional integration with Sentry for automatic error reporting, performance monitoring, and request context capture.

## Overview

Sentry integration provides production error tracking:
- **Automatic error capture** - Exceptions captured from ASGI apps
- **Request context** - Full request details in error reports
- **Performance monitoring** - Transaction tracking and profiling
- **Breadcrumbs** - Debug context for error reports
- **User tracking** - Associate errors with users
- **Release tracking** - Track errors by deployment

## Quick Start

### Installation

Sentry SDK is an **optional dependency**:

```bash
pip install sentry-sdk
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

```python
from pounce import run, ServerConfig

config = ServerConfig(
    sentry_dsn="https://example@o0.ingest.sentry.io/0",
    sentry_environment="production",
    sentry_release="myapp@1.2.3",
    sentry_traces_sample_rate=0.1,
    metrics_enabled=True,
    rate_limit_enabled=True,
    request_queue_enabled=True,
)

run("myapp:app", config=config)
```

### Environment Variables

```bash
export SENTRY_DSN="https://example@o0.ingest.sentry.io/0"
export SENTRY_ENVIRONMENT="production"
export SENTRY_RELEASE="myapp@1.0.0"

pounce run myapp:app
```

## Manual Instrumentation

### Capture Exceptions

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
```

### Capture Messages

```python
from pounce._sentry import capture_message

capture_message(
    "Unusual login pattern detected",
    level="warning",
    tags={"component": "auth"},
    extra={"user_id": user.id},
)
```

### Add Breadcrumbs

```python
from pounce._sentry import add_breadcrumb

add_breadcrumb("Database query started", category="db")
result = db.query("SELECT * FROM users")
add_breadcrumb("Database query completed", category="db", data={"rows": len(result)})
```

### Set User Context

```python
from pounce._sentry import set_user

set_user(
    user_id=str(user.id),
    email=user.email,
    username=user.username,
    ip_address=request.client.host,
)
```

### Track Transactions

```python
from pounce._sentry import start_transaction

with start_transaction("process_order", op="task"):
    process_payment(order)
    send_confirmation(order)
```

## Best Practices

### Release Tracking

Always set release version:

```python
config = ServerConfig(
    sentry_release=f"myapp@{GIT_SHA}",
)
```

### Sampling Strategy

**Production (high traffic):**
```python
config = ServerConfig(
    sentry_traces_sample_rate=0.01,  # 1%
    sentry_profiles_sample_rate=0.01,
)
```

**Staging (moderate traffic):**
```python
config = ServerConfig(
    sentry_traces_sample_rate=0.5,  # 50%
    sentry_profiles_sample_rate=0.1,
)
```

### Sensitive Data

Sentry automatically scrubs passwords, API keys, and tokens. For additional scrubbing:

```python
import sentry_sdk

sentry_sdk.init(
    before_send=scrub_sensitive_data,
)

def scrub_sensitive_data(event, hint):
    if "request" in event:
        if "headers" in event["request"]:
            event["request"]["headers"].pop("Authorization", None)
    return event
```

## Kubernetes Deployment

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
          value: "myapp@1.0.0"
```

## Troubleshooting

### No Events in Sentry

1. **Check DSN** - Verify DSN is correct
2. **Check SDK** - Ensure `sentry-sdk` is installed
3. **Check logs** - Look for Sentry initialization messages
4. **Test manually:**
```python
from pounce._sentry import capture_message
capture_message("Test from pounce", level="info")
```

### High Overhead

1. Lower sample rates (`sentry_traces_sample_rate=0.01`)
2. Disable profiling (`sentry_profiles_sample_rate=0.0`)
3. Filter noisy events with `before_send`

## Performance Impact

- **Error capture:** ~1-5ms per error (rare)
- **Performance tracking:** ~0.1-1ms per request (at 10% sample rate)
- **Breadcrumbs:** ~0.01ms per breadcrumb
- **Async sending:** Events sent in background

## See Also

- [Observability](./observability) — Health checks, request IDs, Prometheus metrics
- [Graceful Shutdown](./graceful-shutdown) — Clean shutdown with Sentry flush
