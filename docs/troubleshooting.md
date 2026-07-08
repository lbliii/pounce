# Troubleshooting

Every pounce error carries a semantic code of the form
`POUNCE_<CATEGORY>_<SPECIFIC>`. You'll see codes in:

- The `X-Pounce-Error-Code` response header on pounce-generated 4xx/5xx.
- Log lines written by workers and the supervisor.
- Response bodies when `ServerConfig.debug=True` is set.

This file is the catalog. Each entry is grouped by category and includes what
the code means, what typically causes it, and what to do.

See [docs/design/error-codes.md](design/error-codes.md) for the naming scheme
and [AGENTS.md](../AGENTS.md) for contributing norms.

---

## Category fallbacks

Every `PounceError` subclass carries a `default_code` that is emitted when a
raise site does not pass an explicit `code=`. If you see one of these in logs,
the underlying condition was not specific enough to warrant a finer code —
file an issue with the traceback so we can mint one.

### POUNCE_E_UNKNOWN
Fallback for `PounceError` itself. Should not appear in production. If it
does, attach the traceback to a bug report.

### POUNCE_PARSE_E
A parser-layer error with no specific code. See logs for the offending bytes.

### POUNCE_TIMEOUT_E
A request or keep-alive timeout with no specific code. Check
`header_timeout`, `request_timeout`, `keep_alive_timeout`, and `write_timeout`.

### POUNCE_TIMEOUT_REQUEST_BODY
The client stopped making progress while uploading a request body. Check the
client and proxy path first. Increase `request_timeout` only for clients that
legitimately upload slowly.

### POUNCE_TIMEOUT_WRITE
The client or downstream proxy stopped accepting response bytes. Pounce closes
the affected HTTP/1.1, HTTP/2, or WebSocket connection after `write_timeout`.
Check downstream health and backpressure before increasing the timeout. HTTP/3
uses QUIC's `http3_idle_timeout` because its output path has no asynchronous
stream-writer drain.

### POUNCE_LIMIT_E
A size-limit rejection with no specific code. Check `max_header_size`,
`max_request_size`, and related ceilings.

### POUNCE_APP_E
The ASGI app raised an unhandled exception. Check the worker log for the
application traceback; this is the generic 500.

### POUNCE_LIFESPAN_E
A lifespan handler failure with no specific code. Check the app's
`lifespan.startup` / `lifespan.shutdown` handlers.

### POUNCE_SUPERVISOR_E
A supervisor failure (spawn, distribute, reap) with no specific code. Check
supervisor logs.

### POUNCE_WORKER_E
A worker-level failure surfaced to the supervisor. Check worker logs for
the underlying cause.

### POUNCE_WORKER_STARTUP_FAILED
A required `pounce.worker.startup` hook raised, timed out, or never reported
ready while `worker_startup_failure="shutdown"` was enabled. Fix the hook's
dependency or initialization failure; increase `startup_timeout` only when the
hook is healthy but legitimately slow. Use the default `"ignore"` policy only
for generic ASGI apps that intentionally do not implement this extension.

### POUNCE_TLS_E
A TLS configuration failure with no specific code. Check certfile, keyfile,
and ciphers.

### POUNCE_RELOAD_E
A reload-path failure with no specific code. Check file-watcher and restart
logs.

---

## PARSE — Malformed HTTP (400)

### POUNCE_PARSE_MALFORMED_REQUEST_LINE
The request line could not be tokenized into method / target / version.
**Cause:** junk on the wire, a client that isn't HTTP, or an L4 proxy
forwarding non-HTTP traffic.
**Do:** check the client, verify the listener is receiving HTTP/1.1.

### POUNCE_PARSE_BAD_METHOD
The HTTP method contained bytes outside the RFC 7230 token charset.
**Cause:** the client sent `GET ` with extra whitespace, a non-ASCII method,
or binary garbage.
**Do:** fix the client; pounce will not accept non-standard methods.

### POUNCE_PARSE_BAD_TARGET
The request target contains characters that are not valid per RFC 3986.
**Cause:** unencoded spaces, control bytes, or non-UTF-8 in the URL.
**Do:** URL-encode the path on the client side.

### POUNCE_PARSE_BAD_VERSION
The HTTP version on the request line is not one pounce supports on this
listener.
**Cause:** a client speaking HTTP/0.9 or a typo like `HTTP/1.2`.
**Do:** use HTTP/1.0 or HTTP/1.1. Enable HTTP/2 via TLS ALPN if needed.

### POUNCE_PARSE_BAD_HEADER_NAME
A header field-name contains bytes outside the token charset.
**Cause:** junk bytes between headers, a client emitting spaces in names.
**Do:** fix the client.

### POUNCE_PARSE_DUPLICATE_CONTENT_LENGTH
The request carried more than one `Content-Length` header.
**Cause:** broken client or intermediary merging requests.
**Do:** reject at the proxy; this is also a smuggling vector.

### POUNCE_PARSE_DUPLICATE_HOST
The request carried more than one `Host` header. RFC 9112 §3.2 forbids this; it
is a request-smuggling / routing-desync vector for host-based routing.
**Cause:** broken client or intermediary, or a smuggling attempt.
**Do:** pounce always rejects (both worker paths). Investigate the upstream.

### POUNCE_PARSE_BAD_CONTENT_LENGTH
`Content-Length` is not a valid non-negative integer.
**Cause:** client bug; whitespace, hex, or signed values in the header.
**Do:** fix the client.

### POUNCE_PARSE_NEGATIVE_CONTENT_LENGTH
`Content-Length` parsed but is negative.
**Cause:** client bug.
**Do:** fix the client.

### POUNCE_PARSE_SMUGGLING_CL_TE
Request carries both `Content-Length` and `Transfer-Encoding`. Forbidden by
RFC 7230 §3.3.3 and a classic request-smuggling vector.
**Cause:** broken proxy, smuggling attempt.
**Do:** pounce always rejects. Investigate the upstream.

### POUNCE_PARSE_HEADERS_TOO_LARGE
Combined header bytes exceeded `max_header_size` (default 64 KiB).
**Cause:** legitimate large cookies, or an attacker.
**Do:** raise `max_header_size` if legitimate; otherwise block upstream.

### POUNCE_PARSE_TOO_MANY_HEADERS
Header count exceeded `max_headers` (default 100).
**Cause:** legitimate heavy metadata, or an attacker.
**Do:** raise `max_headers` if legitimate; otherwise block upstream.

### POUNCE_PARSE_H11_REJECTED
Fallback parser (`h11`) rejected the request. Emitted when the sync fast
parser is disabled and h11 returned a protocol error.
**Cause:** protocol violations not caught by pounce's fast parser.
**Do:** attach the traceback; we may add a more specific code.

### POUNCE_PARSE_CHUNKED_UNSUPPORTED
Request used `Transfer-Encoding: chunked` against the **sync** worker, which
does not decode chunked bodies in-band.
**Cause:** client sent a chunked body to a sync-worker listener.
**Do:** set `worker_mode='async'` or enable the async-handoff pool.

---

## LIMIT — Size enforcement (413 / 431)

### POUNCE_LIMIT_REQUEST_TOO_LARGE
A request exceeded the buffer or size ceiling (usually headers).
**Cause:** oversized headers or body.
**Do:** raise `max_header_size` or `max_request_size` if legitimate.

---

## APP — ASGI application failures (500)

### POUNCE_APP_NO_RESPONSE
The ASGI app returned without ever sending `http.response.start`.
**Cause:** app coroutine raised before sending, or returned `None` silently.
**Do:** check the app traceback. Every request must produce a start message.

### POUNCE_APP_DEBUG_PAGE_FAILED
Rendering the debug error page itself raised. Pounce fell back to a minimal
500.
**Cause:** template engine broke or the exception object could not be
formatted.
**Do:** check worker logs; this usually accompanies another traceback.

---

## LIFESPAN — ASGI lifespan (500)

### POUNCE_LIFESPAN_STARTUP_FAILED
The app's `lifespan.startup` handler raised, timed out, or sent
`lifespan.startup.failed`.
**Cause:** broken init code, missing DB connection, bad config.
**Do:** check the app's lifespan startup logic. Raise `startup_timeout` if
a slow dependency is legitimate.

---

## SUPERVISOR — Worker lifecycle (500)

### POUNCE_SUPERVISOR_FORK_UNAVAILABLE
Process workers were requested but the `fork` start method isn't available
(e.g. on Windows).
**Cause:** Pounce selected process workers on a platform without `fork`.
**Do:** run process workers on a platform with `fork` support, use a single
worker, or use a free-threaded Python build so `worker_mode='auto'` can select
thread workers.

### POUNCE_SUPERVISOR_SOCKET_COUNT_MISMATCH
The supervisor received a different number of listening sockets than workers.
**Cause:** internal wiring bug — file an issue.
**Do:** attach the config and traceback.

### POUNCE_SUPERVISOR_UDP_SOCKET_COUNT_MISMATCH
Same as above but for the HTTP/3 UDP listener set.
**Cause:** internal wiring bug — file an issue.
**Do:** attach the config and traceback.

### POUNCE_SUPERVISOR_MAX_CONNECTIONS_TOO_LOW
`max_connections` is below the per-worker minimum.
**Cause:** `max_connections` set lower than `workers`.
**Do:** raise `max_connections`, or reduce `workers`.

### POUNCE_SUPERVISOR_SUBINTERPRETER_NO_APP_PATH
`worker_mode='subinterpreter'` was set but no app import path was provided.
**Cause:** calling `Server()` programmatically without `app_path=`.
**Do:** pass `--app myapp:app` on the CLI or `app_path='myapp:app'` to
`Server()`.

---

## WORKER — Worker-mode mismatches (500)

### POUNCE_WORKER_WEBSOCKET_NEEDS_ASYNC
A WebSocket upgrade reached the sync worker, which cannot handle it.
**Cause:** `worker_mode='sync'` with a WebSocket-using app.
**Do:** set `worker_mode='async'` or enable the async handoff pool.

### POUNCE_WORKER_STREAMING_NEEDS_ASYNC
A streaming response reached the sync worker, which can only send complete
bodies.
**Cause:** `worker_mode='sync'` with an app that yields multiple
`http.response.body` chunks.
**Do:** set `worker_mode='async'` or enable the async handoff pool.

---

## TLS — Certificate and handshake (500)

### POUNCE_TLS_CERT_MISSING
TLS was requested (non-None `ssl_certfile` implied by other flags) but no
cert file was provided.
**Cause:** `--ssl-keyfile` without `--ssl-certfile`, or HTTP/3 enabled with
no TLS config.
**Do:** pass `--ssl-certfile=PATH` or set `ssl_certfile` in `pounce.toml`.

### POUNCE_TLS_CERT_FILE_NOT_FOUND
`ssl_certfile` or `ssl_keyfile` points at a path that does not exist.
**Cause:** typo, bad working directory, missing cert bundle.
**Do:** verify both paths exist and are readable by the pounce process.

### POUNCE_TLS_CERT_PERMISSION_DENIED
pounce cannot read the cert or key file.
**Cause:** restrictive file mode or ownership.
**Do:** check ownership and mode (e.g. `chmod 0600`, correct user).

### POUNCE_TLS_CONFIGURE_FAILED
`ssl.SSLContext.load_cert_chain` (or the underlying OpenSSL call) rejected
the cert/key pair.
**Cause:** mismatched cert/key, corrupted PEM, unsupported algorithm.
**Do:** verify the pair with `openssl x509 -in cert.pem -noout` and
`openssl rsa -in key.pem -check` (or `ec`/`ed25519` equivalents).

---

## CONFIG — Server configuration (warning, not raise)

### POUNCE_CONFIG_INTROSPECTION_PUBLIC
`introspection_enabled=True` was set while the server (or
`introspection_bind`) is bound to a non-loopback interface. The
`/_pounce/info` endpoint will be reachable from outside the local host.
**Cause:** deploying with `host="0.0.0.0"` (or any public address) without
moving the introspection endpoint behind a private bind / reverse-proxy
ACL.
**Do:** either keep `host` on `127.0.0.1`, set `introspection_enabled=False`
in production, or front pounce with a proxy that strips the
`introspection_path` (`/_pounce/info` by default) from external traffic.
The endpoint's payload is filtered through `INFO_ALLOWLIST` in
`src/pounce/_config_schema.py` (every `ServerConfig` field is either
`EXPOSE` or `REDACT_TO_BOOL`), so secrets like `ssl_certfile` and
`sentry_dsn` never appear verbatim — but the runtime fingerprint
(version, GIL state, worker count, uptime) is still informative to an
attacker probing your stack.

---

## Reporting new codes

If you raise a new `PounceError` anywhere in `src/pounce/`, add an entry
here. The test `tests/unit/test_troubleshooting_catalog.py` fails CI if any
emitted code is missing from this file.
