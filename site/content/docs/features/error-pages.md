---
title: Development Error Pages
description: Rich HTML error pages with syntax highlighting for debugging
weight: 4
---

# Development Error Pages

When your ASGI app raises an unhandled exception in debug mode, pounce renders a detailed error page with full traceback, syntax highlighting, local variables, and request context. In production, errors return a plain `500 Internal Server Error` with no internals exposed.

## Enable Debug Mode

```python
from pounce import ServerConfig

config = ServerConfig(debug=True, workers=1)
```

Use a config file or programmatic `ServerConfig` for `debug=True`; the current
CLI does not expose a debug-mode flag.

## What You See

1. **Exception header** with type and message
2. **Request context** (method, path, headers)
3. **Full traceback** with highlighted error line and 5 lines of surrounding source
4. **Local variables** per stack frame (sensitive values like `password`, `secret`, `token`, `api_key` are redacted)

Install [Rosettes](https://github.com/bengal-python/rosettes) for syntax highlighting (`pip install rosettes`). Without it, source code renders as plain text.

## Production Safety

Debug mode is **off by default**. In production:

- No source code, tracebacks, or variable values are exposed
- Authorization headers and cookies are excluded from error output
- Errors return plain text: `Internal Server Error`

```python
# Production (default)
config = ServerConfig()  # debug=False

# Development only
config = ServerConfig(debug=True)
```

## Custom Error Handling

Pounce's debug pages only activate for exceptions that **propagate to the server**. Catch exceptions in your app to return custom responses:

```python
async def app(scope, receive, send):
    try:
        await process_request(scope, receive, send)
    except ValueError as e:
        await send({"type": "http.response.start", "status": 400,
                     "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body",
                     "body": f'{{"error": "{e}"}}'.encode()})
```

Errors are always **logged** regardless of debug mode. Debug only affects the HTTP response.
