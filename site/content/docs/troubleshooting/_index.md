---
title: Troubleshooting
description: Common issues and solutions
draft: false
weight: 90
lang: en
type: doc
tags: [troubleshooting, debugging, errors]
keywords: [troubleshooting, debugging, errors, common issues, solutions]
category: reference
icon: alert-triangle
---

## Installation Issues

### `pounce: command not found`

The `pounce` CLI is installed as a Python script. Ensure your Python scripts directory is on your `PATH`:

```bash
# Check where pip installs scripts
python -m site --user-base

# The scripts directory is usually:
# macOS/Linux: ~/.local/bin
# Windows: %APPDATA%\Python\PythonXY\Scripts
```

### `requires Python >= 3.14`

Pounce requires Python 3.14 or later. Check your version:

```bash
python --version
```

For free-threading support, you need a Python 3.14t build.

## Startup Issues

### `Could not import module`

Ensure the module is importable from your current directory:

```bash
# Verify the module can be imported
python -c "import myapp"

# Common fix: run from the project root
cd /path/to/project
pounce serve --app myapp:app
```

### `ssl_certfile and ssl_keyfile must both be set`

Both TLS options must be provided together:

```bash
# Wrong
pounce serve --app myapp:app --ssl-certfile cert.pem

# Correct
pounce serve --app myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem
```

## Runtime Issues

### HTTP 200 response is silently truncated behind an edge proxy

Client-to-edge and edge-to-origin protocols are separate hops. Check the edge
and Pounce logs to identify the origin protocol, and upgrade to a release with
the HTTP/2 active-stream timeout and flow-control fixes.

As a temporary diagnostic mitigation, force HTTP/1.1 when Pounce owns origin
TLS:

```bash
pounce serve --app myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem --no-http2
```

The TOML/`ServerConfig` equivalent is `http2_enabled = false`. It does not
affect TLS terminated by the edge. Preserve `keep_alive_timeout`,
`request_timeout`, and `write_timeout` as separate downstream configuration
fields; increasing one does not mitigate a failure governed by another.

### Workers crashing in a loop

The supervisor limits restarts to 5 per 60-second window. If workers are crashing repeatedly:

1. Check logs for the crash reason (`--log-level debug`)
2. Run in single-worker mode to isolate the issue (`--workers 1`)
3. Common causes: unhandled exceptions in ASGI app, segfaults in C extensions

### High memory usage

If you're on a GIL build, workers are processes — each with its own memory copy. Switch to Python 3.14t for thread-based workers with shared memory, or reduce the worker count.

### Request-body timeouts

`POUNCE_TIMEOUT_REQUEST_BODY` means the client stopped making progress while
uploading. Check the client and proxy path before increasing the deadline:

```bash
pounce serve --app myapp:app --request-timeout 60
```

### Response write timeouts

`POUNCE_TIMEOUT_WRITE` means an HTTP/1.1, HTTP/2, or WebSocket downstream peer
stopped accepting response bytes. Check downstream health and backpressure
before increasing `write_timeout`. HTTP/3 output liveness is governed by
`http3_idle_timeout`.

### Connection refused

Check that the host and port are correct and not blocked by a firewall:

```bash
pounce serve --app myapp:app --host 0.0.0.0 --port 8000
```

`127.0.0.1` (default) only accepts local connections. Use `0.0.0.0` to accept connections from all interfaces.

## Performance Issues

### Slow responses

1. Enable Server-Timing to identify bottlenecks: `--server-timing`
2. Check if compression is helping or hurting: `--no-compression`
3. Profile your ASGI application (the server overhead is minimal)

### Not using all CPU cores

Ensure `--workers 0` is set for auto-detection, or set an explicit count matching your CPU cores.

## See Also

- [[docs/reference/errors|Error Reference]] — Complete error hierarchy
- [[docs/about/faq|FAQ]] — Frequently asked questions
- [[docs/configuration/server-config|ServerConfig]] — Configuration validation
