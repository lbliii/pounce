---
title: Error Reference
description: Pounce error hierarchy and common error messages
draft: false
weight: 20
lang: en
type: doc
tags: [errors, exceptions, reference]
keywords: [errors, exceptions, validation, configuration, runtime]
category: reference
---

## Error Hierarchy

Pounce uses a structured error hierarchy for clear error messages:

```
PounceError (base)
├── ConfigError — Invalid configuration
├── ImportError — Application import failures
├── ProtocolError — Protocol-level errors
├── LifespanError — ASGI lifespan failures
└── ShutdownError — Shutdown-related issues
```

## Configuration Errors

These occur at startup when `ServerConfig` validation fails:

| Error | Cause |
|-------|-------|
| `host must be a non-empty string` | Empty host string |
| `port must be 0-65535` | Port out of valid range |
| `workers must be >= 0` | Negative worker count |
| `ssl_certfile and ssl_keyfile must both be set or both be None` | Only one TLS file provided |
| `log_level must be one of [...]` | Invalid log level string |

## Import Errors

| Error | Cause |
|-------|-------|
| `Could not import module 'X'` | Module not found or import error |
| `Module 'X' has no attribute 'Y'` | App attribute doesn't exist |
| `App 'X' is not callable` | Imported object isn't an ASGI callable |

## Protocol Errors

| Error | Cause |
|-------|-------|
| `Request too large` | Body exceeds `max_request_size` |
| `Too many headers` | Header count exceeds `max_headers` |
| `Header size exceeded` | Headers exceed `max_header_size` |
| `Request timeout` | Request not completed within `request_timeout` |

## Lifespan Errors

| Error | Cause |
|-------|-------|
| `Lifespan startup failed` | App sent `lifespan.startup.failed` |
| `Lifespan startup timed out` | App didn't respond to startup event |

## Runtime Behavior

Pounce follows the "fail loudly" principle:

- **Configuration errors** — Raised immediately at startup, before binding sockets
- **Import errors** — Raised before any workers start
- **Protocol errors** — Logged and connection closed gracefully
- **Lifespan errors** — Server exits with non-zero status

## See Also

- [[docs/configuration/server-config|ServerConfig]] — Configuration validation rules
- [[docs/troubleshooting|Troubleshooting]] — Common issues and solutions
