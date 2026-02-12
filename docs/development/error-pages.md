# Development Error Pages

Pounce provides **beautiful, detailed error pages** during development to help you debug issues quickly. In production, errors return simple 500 responses without exposing internals.

## Overview

When your ASGI app raises an exception in development mode, pounce shows:

- **Full traceback** with source code context
- **Syntax highlighting** (via Rosettes if installed)
- **Local variables** for each stack frame
- **Request details** (method, path, headers)
- **Dark theme** optimized for readability

**Never shown in production.** Controlled by a single `debug` flag.

## Quick Start

### Enable Debug Mode

```python
from pounce import ServerConfig

config = ServerConfig(
    debug=True,  # Enable rich error pages
    workers=1,    # Use 1 worker for easier debugging
)
```

Or via command line:

```bash
pounce myapp:app --debug --workers=1
```

### Example Error Page

When this code raises an exception:

```python
async def app(scope, receive, send):
    user_id = 42
    result = risky_operation(user_id)  # Raises ValueError
    ...
```

You'll see a beautiful error page with:

1. **Exception header**: `ValueError: Invalid user ID`
2. **Request context**: `GET /api/users/42`
3. **Full traceback** with highlighted error line
4. **Local variables**: `user_id = 42`, `result = <unavailable>`
5. **Source code** with 5 lines before/after

## Features

### Syntax Highlighting

Install Rosettes for syntax-highlighted source code:

```bash
pip install rosettes  # Part of the Bengal ecosystem
```

Without Rosettes, code is shown as plain text (still readable).

### Local Variables

Each stack frame shows local variables at the time of the error:

```
Local Variables
--------------
user_id = 42
request_data = {'name': 'Alice', 'email': 'alice@example.com'}
database = <DatabaseConnection connected=True>
```

**Security:** Sensitive variables (`password`, `secret`, `token`, `api_key`, `private_key`) are automatically **redacted**.

### Request Details

See the full request that triggered the error:

```
Request Details
--------------
GET /api/users/42
Host: localhost:8000
User-Agent: curl/7.79.1
Accept: application/json
```

**Security:** Authorization headers, cookies, and tokens are **excluded** from error pages.

### Source Code Context

Each frame shows **5 lines before and after** the error line:

```python
  37  def process_user(user_id):
  38      if user_id < 0:
  39          raise ValueError("Invalid user ID")
  40
> 41      result = fetch_from_database(user_id)  # Error here
  42
  43      if result is None:
  44          raise NotFoundError("User not found")
  45
  46      return result
```

The error line is **highlighted** for quick identification.

## Production Safety

### Debug Mode is OFF by Default

```python
config = ServerConfig()  # debug=False (production-safe)
```

In production, errors return a simple response:

```
HTTP/1.1 500 Internal Server Error
Content-Type: text/plain

Internal Server Error
```

**No source code, no tracebacks, no variable values.**

### Security Guarantees

1. **No Source Code Exposure**: Production errors never show your code
2. **No Traceback Leaks**: Stack traces only in debug mode
3. **Sensitive Data Redacted**: Passwords, secrets, tokens removed
4. **Header Sanitization**: Auth headers excluded from error pages

### Deployment Checklist

Before deploying to production:

```python
# ❌ NEVER do this in production
config = ServerConfig(debug=True)  # DANGER!

# ✅ Always use this in production
config = ServerConfig(debug=False)  # Safe (default)
```

Or via environment variable:

```bash
# Development
export POUNCE_DEBUG=true
pounce myapp:app

# Production
export POUNCE_DEBUG=false  # Or just omit it
pounce myapp:app
```

## Advanced Usage

### Custom Error Handling

You can still implement custom error handling in your app:

```python
from pounce import Response

async def app(scope, receive, send):
    try:
        # Your app logic
        await process_request(scope, receive, send)
    except ValueError as e:
        # Custom error handling
        await send({
            "type": "http.response.start",
            "status": 400,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({
            "type": "http.response.body",
            "body": f'{{"error": "{e}"}}'.encode(),
        })
```

Pounce's debug pages only activate when exceptions **propagate** to the server level (unhandled by your app).

### Middleware Integration

Debug error pages work seamlessly with middleware:

```python
from pounce import ServerConfig, CORSMiddleware

config = ServerConfig(
    debug=True,
    middleware=[CORSMiddleware()],
)
```

Middleware exceptions also show rich error pages in debug mode.

### Logging Integration

Errors are still **logged** regardless of debug mode:

```python
# Both modes log the full traceback
[ERROR] ASGI app error on GET /api/users/42
Traceback (most recent call last):
  File "app.py", line 41, in process_user
    result = fetch_from_database(user_id)
ValueError: Invalid user ID
```

Debug mode only affects the **HTTP response**, not logging.

## Comparison with Other Servers

| Server | Rich Error Pages | Syntax Highlighting | Always Safe |
|--------|-----------------|---------------------|-------------|
| **pounce** | ✅ Built-in | ✅ Via Rosettes | ✅ Off by default |
| Uvicorn | ❌ Plain text only | ❌ No | ⚠️ On by default |
| Flask/Werkzeug | ✅ Yes (Werkzeug debugger) | ❌ No | ❌ Easy to leak |
| FastAPI | ⚠️ Basic HTML | ❌ No | ✅ Safe |

Pounce provides the **best debugging experience** while being **production-safe by default**.

## Troubleshooting

### "No source code shown in error page"

**Problem:** Error page doesn't show source code

**Cause:** File path is not accessible or invalid

**Solution:** Ensure your app files are readable and use absolute imports

### "Variables show as <unavailable>"

**Problem:** Local variables display `<unavailable>`

**Cause:** Variable repr() raised an exception

**Solution:** This is normal for objects that can't be printed. Check the traceback for the actual error.

### "Error page is blank or broken"

**Problem:** Rich error page doesn't render

**Cause:** Exception in debug page rendering

**Solution:** Pounce falls back to simple error. Check server logs for the underlying issue.

## Best Practices

1. **Development Only**: Never enable `debug=True` in production
2. **Single Worker**: Use `workers=1` during debugging for clearer errors
3. **Install Rosettes**: Get syntax highlighting for better readability
4. **Review Error Pages**: Check what information is exposed before sharing screenshots
5. **Test Both Modes**: Verify error handling works in both debug and production

## Integration with Bengal Ecosystem

### With Chirp (Web Framework)

```python
from chirp import Chirp
from pounce import ServerConfig, run

app = Chirp()

@app.route("/")
async def index(request):
    raise ValueError("Oops!")  # Shows rich error page in debug mode

if __name__ == "__main__":
    run("app:app", debug=True, workers=1)
```

### With Rosettes (Syntax Highlighter)

```bash
pip install rosettes
```

Pounce automatically uses Rosettes if installed. No configuration needed!

## See Also

- [Configuration Reference](../reference/config.md) — All config options
- [Logging Guide](../features/structured-logging.md) — Structured error logs
- [Production Deployment](../deployment/production.md) — Production best practices
