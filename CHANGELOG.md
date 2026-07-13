# Changelog

All notable changes to pounce will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

---

<!-- towncrier release notes start -->

## [0.9.1] — 2026-07-13

### Added

- Add an artifact-ready Milo MCP workload, rotating fixed-rate request variants, and real-worker routing-header/SSE framing proof for hosted MCP deployments. ([#229](https://github.com/lbliii/pounce/issues/229))

### Changed

- CLI help now uses Milo's metadata hooks and schema-derived parameter
  descriptions instead of traversing argparse internals. Branded help, JSON
  Schema, and MCP discovery now share the command docstrings as their description
  source; the minimum Milo version is 0.4.0. ([#304](https://github.com/lbliii/pounce/issues/304))
- Pin the stable Railway release recipe to Pounce 0.9.0 after publication while
  keeping the separate main-branch canary on each repository checkout.

### Fixed

- `make gh-release` now reads the package name and version structurally from the
  PEP 621 `[project]` table, preventing Towncrier category names from corrupting
  GitHub release titles. ([#300](https://github.com/lbliii/pounce/issues/300))
- Async workers now retain every accepted connection task until its writer
  detaches, keep connections active through writer closure, and guard CPython
  3.14's server wakeup against a second delayed transport detach. This prevents
  asyncio weak-reference teardown from raising during SIGTERM without cutting
  off in-flight requests; capacity-rejection writers also close after failed
  writes. ([#301](https://github.com/lbliii/pounce/issues/301))
- Late HTTP/1 `GET` and `HEAD` probes for the configured readiness path now keep
  the structured draining JSON 503 response at async-worker and shared
  multi-worker connection-rejection boundaries. Other late paths and methods
  continue to receive the generic shutdown 503. ([#308](https://github.com/lbliii/pounce/issues/308))


## [0.9.0] — 2026-07-09

### Added

- Add sustained fixed-rate p50/p99/p999 benchmark evidence, uvicorn/Hypercorn/Granian comparisons, and weekly/manual/release artifact generation for Python 3.14 and 3.14t. ([#228](https://github.com/lbliii/pounce/issues/228))
- Define the long-lived HTTP stream contract: active SSE responses survive request-idle timers; `pounce.worker.draining` gives apps a bounded, generation-scoped close notification before reload or shutdown; `StreamOpened`/`StreamClosed` drive `http_streams_active` and `http_stream_duration_seconds`; and `RoundRobinTestProxy` provides a reusable two-instance SSE test substrate. ([#238](https://github.com/lbliii/pounce/issues/238))
- Add a reproducible real-CLI HTTP/3 benchmark profile with repeated-sample
  variance, raw QUIC-client output, and process telemetry; promote HTTP/3 from
  optional-limited to optional after closing its reload/drain and evidence gates. ([#240](https://github.com/lbliii/pounce/issues/240))
- Added state-specific timeout enforcement: request-body progress now reports
  `POUNCE_TIMEOUT_REQUEST_BODY`, blocked H1/H2/WebSocket response delivery is
  bounded by `write_timeout` with `POUNCE_TIMEOUT_WRITE`, and accepted WebSockets
  are no longer reaped by HTTP keep-alive timeout. ([#242](https://github.com/lbliii/pounce/issues/242))
- Added `http2_enabled` and the `--no-http2` CLI escape hatch so operators can
  force HTTP/1.1 ALPN at a Pounce-owned TLS origin while diagnosing edge/origin
  HTTP/2 failures. ([#243](https://github.com/lbliii/pounce/issues/243))
- Make `worker_startup_failure="shutdown"` fail server boot with exit code 1 and `POUNCE_WORKER_STARTUP_FAILED`, and delay multi-worker readiness until every worker startup hook and listener succeeds. ([#245](https://github.com/lbliii/pounce/issues/245))
- Add an official Railway deployment bundle with a uv-installed CPython 3.14t image, GIL-off boot assertion, `$PORT` binding, `/readyz` healthcheck, bounded deployment drain settings, and an executable deploy/redeploy traffic smoke proof. ([#248](https://github.com/lbliii/pounce/issues/248))
- `/_pounce/info` now reports the Pounce version, a structured Python build
  fingerprint, free-threaded build capability, runtime GIL state, and the
  operator-supplied `POUNCE_BUILD_ID` when set. ([#252](https://github.com/lbliii/pounce/issues/252))
- Add schema-validated per-process CPU and RSS time series to reproducible
  benchmark artifacts. ([#253](https://github.com/lbliii/pounce/issues/253))
- Prove and document HTTP `QUERY` method and request-body forwarding across HTTP/1.1, HTTP/2, and HTTP/3 without claiming application-level query or caching semantics. ([#257](https://github.com/lbliii/pounce/issues/257))
- CI and `make lint` now enforce strict protocol/ASGI/network import boundaries
  and a duplicate-aware public raise-message debt ratchet alongside the existing
  silent-exception gate. ([#264](https://github.com/lbliii/pounce/issues/264))
- Add a GitHub-connected Railway main canary image and public post-merge probe
  while keeping the published-release recipe pinned and distinct. ([#293](https://github.com/lbliii/pounce/issues/293))

### Changed

- Promote explicit subinterpreter ASGI web workers to stable after adding replacement-readiness gating, old-generation acceptor retirement, concurrent-load reload proof, and exact lifespan-state checks across reload and health-monitor respawn. Import-path, async-only, JSON-safe state, and dependency-compatibility limits remain explicit; proposed job/hybrid roles are out of scope. ([#239](https://github.com/lbliii/pounce/issues/239))
- Prove the OpenTelemetry request-span contract through the OTLP/HTTP collector
  boundary and document its guaranteed names, attributes, and status mapping. ([#255](https://github.com/lbliii/pounce/issues/255))

### Fixed

- Prevent subinterpreter crash recovery from closing a listener descriptor number
  after the worker already released it and the OS reassigned it to another
  resource. ([#106](https://github.com/lbliii/pounce/issues/106))
- Prevent HTTP/2 `keep_alive_timeout` from truncating active response streams; idle connections are still reaped normally. ([#231](https://github.com/lbliii/pounce/issues/231))
- Resume HTTP/2 response writes directly from flow-control window updates, avoiding read-loop starvation and very slow large-body delivery. ([#232](https://github.com/lbliii/pounce/issues/232))
- Prevent Linux subinterpreter reloads from resetting a connection accepted just before the old generation closes its listener. ([#239](https://github.com/lbliii/pounce/issues/239))
- Emit `pounce.worker.startup` and `pounce.worker.shutdown` scopes from sync workers on their per-worker runner loop, matching async and subinterpreter worker lifecycle behavior. ([#244](https://github.com/lbliii/pounce/issues/244))
- Resolved worker models are now reported in startup output and
  `/_pounce/info`; embedded subinterpreter mode requires `app_path` up front and
  uses the supervised isolated-worker path even when `workers=1`. ([#246](https://github.com/lbliii/pounce/issues/246))
- Serve built-in health, introspection, and compression-dictionary endpoints for `HEAD` with GET-equivalent status and headers but no response body; document the endpoint as `/readyz` readiness with separate application-owned `/healthz` liveness. ([#250](https://github.com/lbliii/pounce/issues/250))
- WebSocket `permessage-deflate` now requires an explicit client offer at the
  protocol boundary; HTTP/1.1 and HTTP/2 tests prove non-negotiating clients
  receive no extension header or compressed frames. ([#256](https://github.com/lbliii/pounce/issues/256))
- Encode Railway overlap and drain windows as numbers so the platform accepts the
  checked-in deployment recipe. ([#291](https://github.com/lbliii/pounce/issues/291))
- Restored the GitHub Pages site build by upgrading the documentation toolchain to Bengal 0.5.1 and rejecting deployment artifacts that lack the generated homepage.
- Async workers now close shutdown-503 transports even when a client resets or
  times out during the write, preventing free-threaded event-loop teardown
  tracebacks during SIGTERM drain. ([#297](https://github.com/lbliii/pounce/issues/297))
- Give each HTTP/3 worker generation its own duplicated UDP listener, fully
  retire the old generation before replacement, and preserve the
  supervisor-owned socket across reloads so transport cleanup cannot orphan or
  invalidate the new worker. ([#296](https://github.com/lbliii/pounce/pull/296))


## [0.8.2] — 2026-07-06

### Added

- `serve` and `check` now accept `--debug`, `--trusted-hosts`, and `--metrics`; branded help and the README point to the TOML escape hatch (`pounce config schema --output-format toml-template`). ([#158](https://github.com/lbliii/pounce/issues/158))

### Fixed

- Unify built-in health, introspection, and compression-dictionary endpoints across HTTP/1.1, HTTP/2, and HTTP/3, including real worker IDs in HTTP/3 health responses. ([#161](https://github.com/lbliii/pounce/issues/161))
- Built-in health endpoint returns HTTP 503 with `{"status":"draining"}` while a worker is draining, threaded through H1 async, sync, H2, and H3 paths so keep-alive load-balancer probes stop routing traffic during deploys. ([#107](https://github.com/lbliii/pounce/issues/107))
- Fix three production access-log/protocol bugs from Chirp triage: ASGI bridges (H1/H2/H3/sync) now drop app body bytes for HEAD so Content-Length matches zero wire octets (fixes `LocalProtocolError: Too much data for declared Content-Length`), `LoggingCollector` converts monotonic `timestamp_ns` to real UTC via a wall-clock offset, and long-lived SSE/chunked streams no longer get `slow:true` thanks to a new `ResponseCompleted.streaming` field. ([#217](https://github.com/lbliii/pounce/issues/217), [#218](https://github.com/lbliii/pounce/issues/218), [#219](https://github.com/lbliii/pounce/issues/219))
- Thread workers now always receive a dup'd listener handle so graceful reload on platforms with independent ``SO_REUSEPORT`` sockets no longer leaves the next generation with ``EBADF`` after old workers close their asyncio servers. ([#222](https://github.com/lbliii/pounce/pull/222))


## [0.8.1] — 2026-06-15

### Added

- Added the reload/drain-under-load benchmark profile (`benchmarks/drain_profile.py`): it drives steady keep-alive `/fast` + in-flight `/slow` + `/stream` load through the real `pounce serve` CLI, fires SIGHUP then SIGTERM, and emits an artifact-schema JSON whose per-sample `drain` block records in-flight completion, the 503/disconnect rate, drain duration, and orphan-worker absence, so reload/drain health feeds the regression gate alongside the streaming and worker-mode profiles. ([#141](https://github.com/lbliii/pounce/issues/141))

### Changed

- Benchmark hygiene: rewrote the banned PEP 758 parenless multi-`except` form (`except A, B:`) in `benchmarks/` into the portable parenthesized tuple `except (A, B):  # fmt: skip`, and added `benchmarks/` to the code-quality scan so the guard now covers it. No runtime behavior change. ([#155](https://github.com/lbliii/pounce/issues/155))
- Consolidated the H2/H3 per-request prelude (trusted-peer detection, content-encoding negotiation, and access-log filtering) onto the shared `_request_pipeline` helpers, which also restores the already-advertised RFC 9842 dictionary-compressed zstd (`dcz`) negotiation on HTTP/2 and HTTP/3. No user-visible behavior change. ([#160](https://github.com/lbliii/pounce/issues/160))
- Routed the sync worker's SyncApp and ASGI response paths through the shared `negotiate_compressor_from_meta` entry point and a local `_finalize_response_headers` helper, removing the duplicated content-encoding/content-length rewrite blocks. No user-visible behavior change. ([#162](https://github.com/lbliii/pounce/issues/162))
- Extracted the duplicated `FIRST_COMPLETED` race-and-drain logic shared by the disconnect monitor, body reader, and the HTTP/2 and HTTP/1.1 WebSocket bridges into a single `pounce._concurrency` helper that always cancels and awaits the losing task so no task is leaked; behavior is unchanged. ([#163](https://github.com/lbliii/pounce/issues/163))

### Fixed

- Subinterpreter workers no longer leak the dup'd listener file descriptor when they stop abnormally: the bootstrap now closes the reconstructed socket in a `finally` block (covering a startup-hook/`asyncio.start_server`/`asyncio.run` failure before the normal `server.close()`), and the supervisor records each worker's dup'd FD and reclaims it on crash-respawn and after force-stopping a non-draining old generation during graceful reload, so repeated crashes/reloads no longer exhaust the FD budget. ([#106](https://github.com/lbliii/pounce/issues/106))
- `graceful_reload` now rotates HTTP/3 (QUIC) workers alongside TCP workers so the H3 generation also picks up the reimported app — fixing a split-brain where old H3 workers kept serving stale code; each old H3 worker is retired via a per-worker reload event (not the shared shutdown event) and its in-flight streams drain within `shutdown_timeout` before its UDP transport closes, so HTTP/3 reload is brief-downtime rather than zero-downtime. ([#111](https://github.com/lbliii/pounce/issues/111))
- HTTP/3 graceful shutdown now drains in-flight stream tasks within `shutdown_timeout` instead of hard-cancelling them: existing requests finish (only stragglers past the deadline are aborted), new connections/streams are refused while draining, and `CONNECTION_CLOSE` is sent after the bounded wait — mirroring the TCP worker's stop-new / bounded-wait / abort-stragglers sequence. ([#112](https://github.com/lbliii/pounce/issues/112))
- Packaged a `chirp` install extra (`pip install bengal-pounce[chirp]`) and added `bengal-chirp>=0.7.1` to the dev dependency-group so the chirp example/compat tests now actually run in CI instead of being skipped. ([#150](https://github.com/lbliii/pounce/issues/150))


## [0.8.0] — 2026-06-12

> **Notable behavior changes (please review before upgrading).** No public API
> was removed, but a few defaults/behaviors changed:
>
> - **`X-Forwarded-For` is now hop-counted** (`forwarded_for_trusted_hops`,
>   default `1`): the client IP is taken that many positions from the right of
>   the chain. If you relied on the left-most XFF value, the perceived client IP
>   (used by rate limiting, allow/deny lists, and audit logs) may change. (#108)
> - **Sub-threshold responses are no longer compressed** on the async H1/H2/H3
>   paths: bodies smaller than `compression_min_size` are sent uncompressed,
>   matching the sync fast path. (#123)
> - **OpenTelemetry span names and attributes changed**: spans are now named by
>   method (or `{method} {route}`) instead of the raw path, and use stable OTel
>   HTTP semconv attribute names. Dashboards/queries keyed on the old names or
>   per-path span names must be updated. (#135)
> - **JSON access-log `req_id` is now full-length** (previously truncated to
>   8/12 chars), so it matches `X-Request-ID` byte-for-byte. Update any log
>   parser that keyed on the truncated id. (#138)
> - **`pounce info` now writes to stdout** (it was effectively swallowed when
>   piped/redirected). (#156)
> - **Graceful drain/reload now completes the deploy contract across worker
>   modes** (bounded `503` for new connections on full shutdown, in-flight
>   completion, prompt idle exit, no orphans) — behavior differs from prior
>   versions during SIGTERM/SIGHUP. (#100–#104)

### Added

- `ServerConfig.worker_startup_failure` lets frameworks opt into fail-loud `pounce.worker.startup` hooks. The default `"ignore"` preserves generic-ASGI compatibility — a hook exception or timeout is logged and serving continues. Setting it to `"shutdown"` makes a hook failure fatal: the worker refuses to accept connections and signals the supervisor (or single-worker server) to shut down instead of serving with uninitialised worker state. ([#65](https://github.com/lbliii/pounce/issues/65))
- The server now logs a startup INFO line stating the **effective aggregate** of backpressure limits across workers: for rate limiting, `rate req/s per IP per worker x N workers = ~aggregate` on process/subinterpreter builds (or "shared across workers; aggregate = configured" in thread mode), and for request queueing, `max depth D per worker x N workers = ~aggregate queued` (the queue is per worker in every mode). This makes the `limit x workers` multiplier visible to operators at startup. ([#109](https://github.com/lbliii/pounce/issues/109))
- HTTP/1.1 requests carrying `Expect: 100-continue` now receive an interim `100 Continue` status line before the body is read, on both the async (h11) and sync (fast-parser) workers, so compliant clients that withhold the body no longer stall. Trailers remain unsupported and are documented as such. ([#122](https://github.com/lbliii/pounce/issues/122))
- Static file responses now emit `Last-Modified` (from the file mtime, as an RFC 9110 GMT IMF-fixdate) on 200, 206, and 304 responses, and honor `If-Modified-Since` for date-based 304 revalidation (only when `If-None-Match` is absent, per RFC 9110 §13.1.3). `If-Range` is now respected on range requests: a request whose `If-Range` value does not match the current representation returns the full 200 entity instead of a 206. Because Pounce emits weak ETags, an ETag-valued `If-Range` never matches (RFC 9110 §13.1.5) and falls back to the full entity; a date-valued `If-Range` serves 206 only when the file has not been modified since that date. ([#128](https://github.com/lbliii/pounce/issues/128))
- Added an opt-in, bounded, mtime-validated stat cache for the static-file hot path (`StaticMount(stat_cache=True, stat_cache_size=...)`). When enabled, a repeated request revalidates the resolved file with a single `stat()` instead of re-running path resolution, `lstat()`, and the precompressed probes, and invalidates automatically when the file changes (e.g. an SSG rebuild). Default off; path-traversal and symlink defenses still run on every cache miss/insert. ([#130](https://github.com/lbliii/pounce/issues/130))
- Benchmark artifacts (`benchmarks/run_benchmark.py --artifact-output`) now record under-load process telemetry: a new top-level `telemetry` field (mirrored in `benchmarks/artifact-schema.json`) plus per-sample peak RSS, mean/peak CPU%, and worker pids. Peak RSS and CPU are sampled during the load window across the supervisor **and** any forked worker processes, so process-mode runs aggregate child workers instead of measuring the idle supervisor. Best-effort and cross-platform (Linux `/proc` + macOS/BSD `ps`); fields are null when the platform does not expose process telemetry. ([#139](https://github.com/lbliii/pounce/issues/139))
- The `--worker-mode` CLI help now marks `subinterpreter` as `(beta)`, and the server emits a one-line INFO notice at startup when the beta subinterpreter worker mode is actually resolved. The `core-contract` design doc gained an "Observability Name Contract" section enumerating the stable Prometheus metric names, lifecycle event names, and structured/access-log field names. ([#157](https://github.com/lbliii/pounce/issues/157))

### Changed

- Documented that rate-limit and request-queue limits are enforced **per worker**, not per server: the rate limiter is shared only in thread mode (independent copies on process/subinterpreter builds), and the request queue is per worker in every mode. With `workers > 1` the effective aggregate is `limit x workers` (per-IP `rate x workers`, shed depth `max_depth x workers`). Clarified in the backpressure docs, the `core-contract` doc, and the `ServerConfig`/`TokenBucket`/`RequestQueue` docstrings. ([#109](https://github.com/lbliii/pounce/issues/109))
- 103 Early Hints (RFC 8297) now behave consistently across HTTP/1.1, HTTP/2, and HTTP/3. The H1 bridge previously discarded `http.response.start` with status 103 as a silent no-op; it now serializes the informational response (via h11's `InformationalResponse`) and writes it to the wire immediately, before the final response, matching the H2/H3 bridges. The behavior is default-on on all three protocols and does not terminate the request-response cycle, so the final response is still sent afterwards. Early-hint headers are CR/LF-sanitized on every protocol. ([#124](https://github.com/lbliii/pounce/issues/124))
- Static file serving now skips the zero-copy `sendfile` path for bodies smaller than a measured 16 KiB threshold, falling back to a buffered read+write. Below that size the per-response transport detach/re-attach plus pre-send drain cost more than a single write, so tiny assets (e.g. small HTML pages) are served faster. ([#127](https://github.com/lbliii/pounce/issues/127))
- The default `--reload` watch set now covers static-site authoring files — `.md`, `.html`, `.css`, `.js`, and `.svg` — in addition to Python and config sources, so editing content, templates, or styles triggers a reload without `--reload-include`. The watcher also scans the directories behind configured `static_files` mounts, so assets served from outside the working directory reload too. `.py` watching is unchanged; use `--reload-include` for extensions outside the default set. ([#132](https://github.com/lbliii/pounce/issues/132))
- OpenTelemetry request spans now use low-cardinality span names (the HTTP method, or `{method} {route}` when a route template is provided) instead of the raw request path, and emit stable OTel HTTP semantic-convention attributes (`http.request.method`, `url.path`, `url.scheme`, `server.address`, `server.port`, `http.response.status_code`, `http.response.body.size`) in place of the deprecated pre-1.20 names. ([#135](https://github.com/lbliii/pounce/issues/135))
- Document the JSON access-log line as a stability contract (exact key set, value types, and `req_id` policy) and emit the request id **in full** in both `json` and `text` modes, so the access-log `req_id` matches the `X-Request-ID` response header byte-for-byte (previously truncated to 8/12 chars, which broke exact correlation and disagreed between formats). ([#138](https://github.com/lbliii/pounce/issues/138))
- `pounce bench` now clearly labels its plain-text output as a **local snapshot**, not a governed benchmark artifact, and points to `benchmarks/run_benchmark.py --artifact-output` (the authoritative pipeline that emits artifacts following `benchmarks/artifact-schema.json`). The CLI snapshot can no longer be mistaken for citable benchmark evidence. ([#142](https://github.com/lbliii/pounce/issues/142))
- `ServerConfig` now documents stability tiers: `worker_mode="subinterpreter"` and the rate-limit, request-queue, introspection, HTTP/3, and observability (`otel_*`/`sentry_*`/`metrics_*`) knobs are marked **beta**, while host/port/workers/timeouts/limits/log_* stay **stable**. `pounce config schema` surfaces the beta tier as an `x-stability` annotation per field (and an `x-stability-values` note on the `worker_mode` subinterpreter value); field names and the field set are unchanged. The `subinterpreter-workers` and `core-contract` design docs were reconciled to the beta wording. ([#157](https://github.com/lbliii/pounce/issues/157))
- Internal type and exception hygiene cleanup: added a typed `ServerConfig.from_mapping()` factory that owns the single cast from an untyped merged-config mapping (e.g. the `dict[str, Any]` returned by config-file loading) to the keyword-only constructor, so the CLI no longer needs per-call-site `type: ignore` comments when building config. Removed stale `type: ignore`/`ty: ignore` comments across `src/pounce/` (only genuinely-required ones remain) and restructured the banned PEP 758 parenless multi-`except` form (`except A, B:`) into single-type or base-class `except` clauses. No runtime behavior change. ([#164](https://github.com/lbliii/pounce/issues/164))
- Test runs now apply a default per-test timeout (`--timeout=60 --timeout-method=thread`) so a hung socket or drain test fails fast with a per-test traceback instead of stalling the whole job. The local `test-fast` task still overrides with a tighter `--timeout=10`. ([#166](https://github.com/lbliii/pounce/issues/166))

### Fixed

- Static file serving no longer crashes the worker with an uncaught `BlockingIOError` (EAGAIN) when a slow or disconnecting client fills the kernel send buffer mid-transfer. The zero-copy sendfile path now uses `loop.sendfile()`, which handles non-blocking-socket back-pressure through the selector instead of a raw `os.sendfile` loop in an executor thread. Client disconnects are treated as a clean abort rather than a 500. ([#72](https://github.com/lbliii/pounce/issues/72))
- SyncWorker now honors the drain signal inside its keep-alive loop: once draining (graceful reload / shutdown), the in-flight request still completes but the response carries `Connection: close` and the keep-alive loop exits, so the worker becomes idle well before `reload_timeout` instead of serving new requests on a draining worker. ([#100](https://github.com/lbliii/pounce/issues/100))
- The sync execution path now emits a bounded, actionable `503 Service Unavailable` (with `Connection: close` and `Retry-After`) for *new* connections that arrive during drain: the `AcceptDistributor` stops enqueuing once draining and answers late arrivals with a 503 so the shared connection queue cannot accumulate orphaned connections at shutdown. ([#101](https://github.com/lbliii/pounce/issues/101))
- `graceful_reload` (SIGHUP) in sync (thread) mode now rebuilds the `AsyncPool` bound to the reimported app before spawning the new worker generation, so streaming and WebSocket handoffs run the new code after a reload instead of silently serving the pre-reload app (split-brain). ([#102](https://github.com/lbliii/pounce/issues/102))
- The subinterpreter IIC bridge no longer hangs on SIGTERM drain: it now polls `ctrl_queue` for shutdown on every tick and bounds the idle wait by `shutdown_timeout`, so a worker with a long-lived connection (SSE/WebSocket/slow client) still exits within the timeout and the supervisor reports a clean stop. ([#103](https://github.com/lbliii/pounce/issues/103))
- Hardened graceful drain under load across every worker mode on free-threaded 3.14t (the cross-mode SIGTERM-under-load proof, #104):

  - **async (thread) workers** now keep accepting during the bounded full-shutdown drain window and answer brand-new connections with a clean `503` instead of leaving them unanswered in the listen backlog (a silent drop / hung connection).
  - **sync workers** now *serve* in-flight requests that were accepted into the shared queue before drain began rather than resetting them — closing such a socket after the client had already sent its request RST it; in-flight requests now complete. Streaming/WebSocket handoffs made by a draining sync worker are no longer orphaned: the `AsyncPool` outlives the workers' drain and processes the final handoffs before retiring.
  - **subinterpreter workers** fully retire their per-worker executor thread pool (bounded by `shutdown_timeout`) before the interpreter is closed, removing an interpreter-teardown race that could intermittently crash the process during drain on a free-threaded build.

  ([#104](https://github.com/lbliii/pounce/issues/104))
- Process/fork worker mode now terminates and reaps worker processes on shutdown. Fork-context workers are `ForkProcess` instances, which are not instances of `multiprocessing.Process` (the default-context class), so the supervisor's process-vs-thread branch never fired: forked workers were treated as un-killable threads, survived `SIGTERM`, and the parent hung at exit. The supervisor now branches on `multiprocessing.process.BaseProcess`, so process workers receive `SIGTERM`/`SIGKILL` and no orphan worker processes remain. A new GIL-build (Python 3.14) CI lane and process-mode integration tests exercise this path end-to-end. ([#105](https://github.com/lbliii/pounce/issues/105))
- Brought WebSocket-over-HTTP/2 (RFC 8441 Extended CONNECT) to parity with the HTTP/1.1 WebSocket path. The H2 WS handler now enforces `websocket_max_message_size` (a WebSocket `1009` close plus an `RST_STREAM` on an oversize inbound message), guards `websocket.send` against send-before-`accept` / send-after-`close`, negotiates permessage-deflate from the CONNECT headers, and implements the `websocket.http.response.start` / `.body` reject path. The `200` acceptance headers are now deferred until the app accepts so the negotiated `Sec-WebSocket-Extensions` and subprotocol can be echoed. ([#115](https://github.com/lbliii/pounce/issues/115))
- Negotiate the permessage-deflate `client_max_window_bits` / `server_max_window_bits` (and `*_no_context_takeover`) parameters from the client's `Sec-WebSocket-Extensions` offer via wsproto's RFC 7692 negotiation, echoing the agreed parameters back instead of a hardcoded bare `permessage-deflate` token. This also fixes a latent bug where the compression extension was constructed but never accepted, leaving it disabled so no WebSocket compression actually occurred. ([#116](https://github.com/lbliii/pounce/issues/116))
- Deliver exactly one `websocket.disconnect` after a clean client close, instead of a spurious `1006` following a normal closure. ([#117](https://github.com/lbliii/pounce/issues/117))
- Remove the HTTP `method` key from the WebSocket ASGI scope, fixing WebSocket routing under Litestar and matching the ASGI WebSocket scope shape. ([#118](https://github.com/lbliii/pounce/issues/118))
- Sanitize CR/LF in sync-bridge response headers (parity with the async/H2/H3 paths) so an app-supplied malformed header is cleaned rather than crashing and dropping the connection. ([#120](https://github.com/lbliii/pounce/issues/120))
- Stop advertising the unimplemented `http.response.push` ASGI extension on HTTP/3 scopes (extension honesty; matches the HTTP/2 bridge). ([#121](https://github.com/lbliii/pounce/issues/121))
- Honor `compression_min_size` on the async HTTP/1.1, HTTP/2, and HTTP/3 bridges so sub-threshold single-shot response bodies are sent uncompressed, matching the synchronous fast path. ([#123](https://github.com/lbliii/pounce/issues/123))
- After a 413 body-limit rejection on HTTP/2, the server now sends `RST_STREAM` (ENHANCE_YOUR_CALM) so the client learns the upload was refused and stops sending. Previously the 413 only half-closed the outbound direction: the inbound half stayed open, the client could keep streaming body, and the protocol layer kept re-crediting its flow-control window for bytes the server discarded. Resetting the stream also drops it from HTTP/2 bookkeeping, so in-flight DATA frames no longer surface as body events or get flow-control-acknowledged. The HTTP/3 path now makes a best-effort, API-agnostic QUIC stop-sending/reset attempt after its 413; when the underlying QUIC library exposes no outbound-reset API it degrades gracefully without regressing drain behavior. ([#125](https://github.com/lbliii/pounce/issues/125))
- Return `416 Range Not Satisfiable` (with `Content-Range: bytes */<size>`) for unsatisfiable byte ranges, clamp ranges that extend past end-of-file, and cap/coalesce multi-range requests to bound response amplification. ([#129](https://github.com/lbliii/pounce/issues/129))
- Honor `Accept-Encoding` q-values when selecting precompressed (`.gz`/`.zst`) static assets (so e.g. `gzip;q=0` disables gzip) and always emit `Vary: Accept-Encoding` when content-encoding negotiation occurs. ([#131](https://github.com/lbliii/pounce/issues/131))
- OpenTelemetry spans created just before a worker stops are now flushed on worker shutdown instead of being dropped, so short-lived requests near shutdown are no longer silently lost (the `BatchSpanProcessor` only exports on its schedule-delay timer and the SDK does not flush on process exit). ([#133](https://github.com/lbliii/pounce/issues/133))
- Remove the unused `inject_trace_context` helper and correct the OpenTelemetry docstrings that implied outbound trace-context propagation that never ran at runtime. ([#136](https://github.com/lbliii/pounce/issues/136))
- `http_requests_total` now records the real HTTP method label instead of always reporting `method="unknown"`. ([#137](https://github.com/lbliii/pounce/issues/137))
- `pounce info` now writes diagnostics to stdout (so `pounce info | cat` and `pounce info --output-format json | jq` are non-empty), reports the auto-detected worker model, and includes the install path and detected frameworks across text, JSON, and pretty output. ([#156](https://github.com/lbliii/pounce/issues/156))
- Restore the `pounce.testing.TestServer` docstring, which was nulled because a `__test__ = False` statement preceded it. ([#159](https://github.com/lbliii/pounce/issues/159))

### Security

- `X-Forwarded-For` is now resolved by trusted-hop count (`forwarded_for_trusted_hops`, default 1) — the client IP is taken that many positions from the right of the chain — so a client-supplied leftmost value can no longer spoof the perceived client IP feeding rate limiting, allow/deny lists, and audit logging. ([#108](https://github.com/lbliii/pounce/issues/108))
- Bound the per-IP rate-limiter bucket map with a hard tracked-IP cap (`rate_limit_max_tracked_ips`, default 100,000) plus LRU eviction and count- and idle-triggered cleanup, closing a memory-exhaustion DoS from a flood of unique source IPs (e.g. a wide IPv6 source range). ([#110](https://github.com/lbliii/pounce/issues/110))
- Sanitize WebSocket rejection response headers and coerce the rejection status to an integer, closing a CRLF header-injection vector on the WebSocket reject path (parity with the HTTP/1.1, HTTP/2, and HTTP/3 response paths). ([#114](https://github.com/lbliii/pounce/issues/114))
- Reject requests with duplicate `Host` headers in the sync fast HTTP/1.1 parser (`POUNCE_PARSE_DUPLICATE_HOST` -> 400), closing a request-smuggling / routing-desync gap and matching the async (h11) path. ([#119](https://github.com/lbliii/pounce/issues/119))

## [0.7.1] — 2026-05-23

### Added

- Added checked-in Bengal and Chirp local benchmark artifacts plus benchmark
  runner tests so performance claims can trace to workload, platform, and command
  metadata.

### Changed

- Added public contract guardrails for API/config/CLI/docs parity, documented
  protocol proof status, and aligned `run(config=...)` typing with
  `ServerConfig`.
- Hardened release proof by adding steward guidance, benchmark artifact policy,
  public-claim ledgers, protocol proof updates, and docs checks that keep public
  claims aligned with shipped behavior.

### Fixed

- Moved HTTP/1 static-file sendfile framing under protocol ownership so range
  responses, streaming fallbacks, and ASGI send paths share consistent completion
  behavior.

---

## [0.7.0] — 2026-05-09

Release-readiness hardening for protocol correctness, operator UX, config discovery, and
production-shaped benchmark coverage.

### Added

- Added `pounce config schema`, `pounce config show`, and `pounce init` for discoverable configuration, redacted resolved-config inspection, and project scaffolding.
- Added the opt-in `/_pounce/info` introspection endpoint with allowlist redaction and public-bind warnings.
- Added Bengal-shaped static-site and Chirp/LB Sonic-shaped forum benchmark workloads for representative static, tenant, form POST, SSE, middleware-style header, and lifespan-state coverage.
- Added real-server middleware coverage for pre-request short-circuiting, post-response headers, exception handling, and non-HTTP scope bypass.
- Added Railway deployment guidance for platform TLS, `$PORT`, health checks, proxy trust, and drain-window alignment.

### Changed

- Re-enabled the ruff S110 lint gate and added CI coverage for unannotated broad exception suppression.
- Adopted modern Python 3.14+ patterns across leaf modules, including frozen handoff dataclasses, PEP 695 aliases, match/case conversions, and stable-shape TypedDicts.
- Updated the introspection auth ADR to match the shipped `/_pounce/info` implementation and warning policy.
- Fixed sync-worker graceful reload proof and clarified lifecycle docs around reload signaling.

### Fixed

- Reject oversized HTTP/2 and HTTP/3 request bodies with 413 behavior instead of delivering empty or truncated bodies to ASGI apps.
- Validate required HTTP/2 and HTTP/3 pseudo-headers, duplicate pseudo-headers, and Host/`:authority` conflicts before building tenant-facing scopes.
- Keep single-worker startup hook exceptions nonfatal, matching Worker-based paths for strict ASGI apps that reject unknown Pounce scopes.
- Avoid acquiring a process `fork` context for thread workers and remove invalid `worker_mode='thread'` remediation from troubleshooting.
- Skip response compression when HTTP/2, HTTP/3, sync ASGI, or sync-app responses already include `Content-Encoding`.
- Rewrite trusted proxy authority consistently across HTTP/1.1, HTTP/2, HTTP/3, and WebSocket ASGI scopes.
- Negotiate WebSocket `permessage-deflate` only when the client offers it.
- Harden free-threaded leaf-module behavior: per-worker request queues, RFC 9218 H2 priority scheduling, CRLF response-framer guard, rate-limiter snapshot cleanup, IPv6/UNIX socket support, subinterpreter timeout wiring, and lower-impact correctness/performance cleanups.

---

## [0.6.0] — 2026-04-13

Subinterpreter workers, RFC 9842 compression dictionaries, sendfile, framework compat tests, and 60+ fixes.

### Added

- Add 48 integration tests proving compatibility with FastAPI, Starlette, Django, and Litestar. All tests run through real Pounce workers — no mocks. Includes shared test infrastructure with proper ASGI lifespan handling.
- Added subinterpreter worker mode (`--worker-mode subinterpreter`) using Python 3.14's `concurrent.interpreters` (PEP 734). Each worker runs in a dedicated subinterpreter — thread-like performance with process-like isolation, all in one process.
- Adopt [Towncrier](https://towncrier.readthedocs.io/) for changelog management. Fragments in `changelog.d/` are compiled into `CHANGELOG.md` at release time. CI enforces a fragment for every PR that touches `src/pounce/`.
- Adopt bengal-zoomies 0.3.1: real QUIC client-mode integration tests, QPACK dynamic table compression (`http3_qpack_max_table_capacity`), and server-side 0-RTT policy control (`http3_zero_rtt_enabled`) with `ZeroRttAccepted`/`ZeroRttRejected` event handling.
- RFC 9842 Compression Dictionary Transport — shared zstd dictionaries for `dcz` content-encoding, `Available-Dictionary` / `Use-As-Dictionary` header negotiation, and built-in dictionary serving at `/.well-known/compression-dictionary/`.
- Zero-copy ``os.sendfile()`` for static file serving on non-TLS connections, RFC 7233 multipart range requests, and TOML config file support (``pounce.toml`` / ``[tool.pounce]`` in ``pyproject.toml``).

### Changed

- Bump milo-cli to 0.2.2 and kida-templates to 0.6.0. Picks up `get_env()` singleton cache fix (122 µs → 125 ns), kida for-loop variable binding correctness fix, faster template compilation, and cleaner command dispatch internals.
- Split `_apply_integrations()` god method into 7 focused private methods and polish hot paths: single-pass H3 header filtering, early-exit WebSocket upgrade detection, module-level debug constants.

### Fixed

- Fixed 28 Python 2 `except A, B:` handlers across 12 files that silently failed to catch the second exception type. Lifespan startup failures are now logged instead of silently swallowed. Worker crashes include full tracebacks. Startup/shutdown hook errors promoted from DEBUG to WARNING. ASGI protocol errors now include request method and path. `max_header_size` config now flows to the fast H1 parser (was hardcoded at 16KB). Config typos suggest similar valid keys. Port-in-use errors suggest diagnostic commands. Added `health_check_path` validation and CORS wildcard startup warning.
- Fixed 33 Python 2 exception syntax errors across 15 files that would crash on import in Python 3.14t. Fixed H3 bridge losing ``:authority`` header, crashing on SSE+compression, and encoding ``raw_path`` incorrectly. Static file serving now honors the ``cache_control`` config field and includes ``Vary: Accept-Encoding`` for precompressed responses. Aligned ``serve`` and ``check`` CLI defaults. Added ``startup_timeout`` validation, early mutual-exclusion checks, and branding params to config files. Improved error logging for parse errors, connection close reasons, and H3 TLS failures.
- Fixed CLI config precedence so explicit args always override TOML values even when matching the default. Added exponential backoff to worker restart to prevent tight crash-restart loops. CORS and security header middleware now skip headers already set by the app. Static file serving now allows ``.well-known/`` paths per RFC 8615. Fixed incorrect middleware docstring example.
- Fixed worker threads/processes hanging indefinitely on shutdown when keep-alive, WebSocket, or SSE connections were still open. The worker now applies `shutdown_timeout` to `server.wait_closed()` and calls `abort_clients()` to force-close lingering transports.
- Hardened subinterpreter workers: fixed socket FD leak on bootstrap failure, upgraded silent lifespan state drops to warnings, improved factory app error messages with chained exceptions. Added 16 new tests covering memory isolation, IIC protocol edge cases, race conditions (shutdown during reload, rapid reloads, crash during drain), and config round-trip validation.
- Safe HSTS default (opt-in instead of always-on), middleware signature validation with clear errors, post-header exception logging, ASGI bridge rejection of invalid message types, distinct ETags for compressed variants per RFC 7232, deprecated config alias support (``reload_dir`` → ``reload_dirs``), CLI ``request_timeout``/``startup_timeout`` passthrough, ``check`` command signage validation, and fair ``max_connections`` remainder distribution across workers.

### Security

- Fix 12 security issues: broken exception syntax in 3 files, CRLF injection in proxy headers and request IDs, unenforced `max_headers` and `websocket_max_message_size` limits, weak TLS cipher suite, world-writable UDS socket, and incomplete security middleware headers.

---

## [0.5.1] — 2026-04-06

Patch release: fork-context fix for process workers and dependency updates.

### Changed

- **Dependency updates** — Bump all runtime and dev dependencies to latest versions. Adopt milo-cli 0.1.1 with built-in `--completions`, `--mcp`, `--verbose`, `--quiet`, `--no-color`, `--dry-run` flags and PyPI version checking. (#28)
- **Startup banner redesign** — Flatten banner layout, suppress per-worker lines in pretty mode, collapse shutdown output, simplify Ready line. (#28)
- **Version notice template** — New `version_notice.kida` template for branded PyPI update notices; demote redundant TLS/H3/ALPN log lines to debug. (#28)

### Fixed

- **Fork context for process workers** — Explicitly use the `"fork"` multiprocessing context so ASGI apps containing closures (from middleware wrappers or framework decorators) are inherited via the forked address space instead of being pickled. Fixes startup crashes on platforms where `"fork"` is available (including macOS) when the default `"spawn"` method cannot serialize the app callable. On Windows, where `"fork"` is unavailable, use thread workers instead. (#29)

---

## [0.5.0] — 2026-04-03

Elm Architecture lifecycle, milo-cli adoption, bench command, and modern Python 3.14t patterns.

### Added

- **`pounce bench` CLI command** — Standardized benchmarking with wrk integration and formatted result tables. (#26)
- **Lifecycle events API** — Public API for lifecycle events (`ConnectionOpened`, `ResponseCompleted`, etc.) enabling external observability and metrics hooks. (#26)
- **Hypothesis fuzzing** — 27 property-based tests across all protocol parsers for deeper correctness coverage. (#26)
- **`DisplayConfig`** — Configurable signage modes and startup display resolution for branded server output. (#25)
- **`pounce info` command** — System diagnostic panel showing Python version, GIL state, installed dependencies, and detected frameworks. (#21)
- **`pounce check` command** — Pre-flight validator for app import, port availability, TLS config, and server configuration. (#21)
- **Branded tracebacks** — Crash reports rendered through kida templates instead of raw Python stack traces. (#21)
- **milo-cli integration** — Replace argparse with milo's CLI class for subcommands, MCP server, `llms.txt`, and type-driven parsing. Branded kida templates for all server lifecycle output — startup banner, ready/shutdown/reload phases, worker events, access logs, and error display. (#20)

### Changed

- **Elm Architecture lifecycle** — Replace 16 procedural lifecycle output functions with a centralized Store + Reducer + Render Middleware pattern. Server, supervisor, and reload dispatch typed actions instead of calling render functions directly. (#22)
- **Modern Python 3.14t patterns** — `Final` annotations on module-level constants, `StrEnum` for `Phase`/`WorkerMode`/`WorkerExecutionMode`, `kw_only=True` on `ServerConfig` and lifecycle dataclasses, `TCPWorker` Protocol for the supervisor contract. (#23)
- **Stricter linting** — Expanded ruff rules (`S`, `A`, `T20`, `DTZ`, `FBT`) and stricter ty type checker configuration. (#23)
- **Single-pass `_classify_request()`** — Replaces 4–5 separate header scans per sync-worker request, fuses content-length tracking, removes redundant `.lower()` on pre-lowered headers. (#24)
- **Reduced static file syscalls** — Static file stat calls reduced from ~7 to ~3 per request. (#24)
- **CI improvements** — Added `cancel-in-progress` and `--maxfail` to CI pipeline for faster feedback. (#18)

### Fixed

- **Content-Length preservation** — Only strip Content-Length from response headers when compressing; preserve app-provided value otherwise. (#24)
- **HTTP/1.0 keep-alive** — Track `Connection` header presence so HTTP/1.0 keep-alive is honoured correctly. (#24)
- **Deployment docs** — Replace broken symlinks with proper site pages containing YAML frontmatter. (#19)

### Docs

- Deep audit of all site docs, internal docs, and roadmap — removed false claims (brotli, sendfile, phantom CLI flags), fixed types and defaults, corrected thread-safety advice, rewrote roadmap with competitive positioning. (#17)
- Narrative docs leading with the performance story: 3 µs parser, rolling reload, AcceptDistributor, competitive comparison tables. (#26)
- nogil-patterns.md with 10 reusable free-threading patterns for Python 3.14t. (#26)

---

## [0.4.0] — 2026-03-25

First-class testing API, graceful shutdown overhaul, thread-safety fixes, and documentation sync.

### Added

- **Testing API** — `pounce.testing.TestServer` runs a real pounce server in a background thread for tests. Supports context manager (`with TestServer(app) as server:`), async context manager, and a `serve()` async helper. Exposes `.url`, `.host`, `.port`, `.is_running`. Auto-registered `pounce_server` pytest fixture via `pytest11` entry point — install pounce and the fixture is available automatically. (#15)
- **`Server.bound_addr`** — Public property exposing the server's bound `(host, port)` tuple after startup, used by `TestServer` for ephemeral port discovery. (#15)
- **Server startup readiness signal** — Internal `threading.Event` set when the server is ready to accept connections, enabling reliable startup synchronization in `TestServer`. (#15)

### Changed

- **Graceful shutdown** — `shutdown_timeout` is applied per worker (TCP and H3 worker threads/processes join in parallel) instead of a single monotonic deadline shared across all joins. AcceptDistributor and AsyncPool each use up to `shutdown_timeout` independently. Full shutdown calls `start_draining()` on thread-mode workers so new connections receive 503 while draining. Thread workers that outlive the join are logged accurately (cannot SIGTERM a thread); process workers still get SIGTERM/SIGKILL. (#12)
- **Worker executor teardown** — Per-worker `ThreadPoolExecutor.shutdown()` runs via `run_in_executor` on a dedicated one-thread pool (not the loop default executor being torn down), wrapped in `asyncio.wait_for` so the event loop is not blocked indefinitely by stuck sync handlers. (#12)
- **Logging TTY detection** — Log formatting respects `sys.stderr.isatty()` instead of unconditionally applying TTY-style output. (#12)

### Fixed

- **Thread-safe connection counter** — `Worker._active_connections` now uses `threading.Lock` for atomic increment/decrement, fixing a race condition under concurrent access on free-threaded Python. (#14)
- **Single-pass header filter** — ASGI bridge response send path filters hop-by-hop headers in a single pass instead of multiple iterations. (#14)

### Docs

- Synced all docs (README, ARD, FEATURES, PRD, site pages) with actual codebase — removed stale claims, corrected protocol descriptions, updated architecture diagrams. (#13)

---

## [0.3.1] — 2026-03-19

Public error type exports for downstream consumers.

### Added

- Re-export `PounceError`, `LifespanError`, `TLSError`, `SupervisorError`, and `ReloadError` from `pounce` top-level package so downstream packages (e.g. chirp) can `from pounce import PounceError` instead of reaching into private `pounce._errors`

---

## [0.3.0] — 2026-03-17

Multi-worker sync performance — matching uvicorn at 30k req/s, pure Python.

### Added

- **Fast HTTP/1.1 parser** — `_fast_h1.py` replaces h11 on the sync worker hot path. Direct bytes parsing (~3 µs/req vs ~22 µs for h11) with full safety checks: method validation, header size limits (16 KiB), null byte/control character injection rejection, duplicate Content-Length detection, Content-Length + Transfer-Encoding conflict detection (RFC 7230 §3.3.3 request smuggling prevention)
- **Shared header utility** — `_headers.py` consolidates 7 copies of `_get_header` scattered across worker, sync_worker, async_pool, and handler modules into a single `get_header()` function
- **Shared request pipeline** — `_request_pipeline.py` provides `prepare_request()`, `negotiate_compressor()`, `log_request()`, and `is_trusted_peer()` — shared between Worker and SyncWorker for feature parity and code deduplication
- **TCP_NODELAY** — Set on accepted connections in `accept_distributor.py` for lower latency

### Changed

- **Middleware classification cached** — `MiddlewareStack.__init__` now classifies middleware once via `inspect.signature` instead of per-request, eliminating repeated reflection overhead
- **`ConnectionClosed` → `ConnectionCompleted`** — Lifecycle event renamed for clarity (`lifecycle.py`, `metrics.py`). The protocol-level `ConnectionClosed` in `protocols/_base.py` is unchanged
- **`trusted_hosts` type** — Changed from `tuple[str, ...]` to `frozenset[str]` for O(1) lookup. Added `trusted_hosts_wildcard: bool` flag computed in `__post_init__` to avoid per-request `"*" in trusted_hosts` checks
- **Single-pass header scanning** — `asgi/bridge.py` response send path now detects Content-Length and Transfer-Encoding in a single pass instead of separate `any()` calls
- **OpenTelemetry optimizations** — `_otel.py` pre-instantiates `TraceContextTextMapPropagator` at module level and filters to only trace headers (`traceparent`, `tracestate`) before conversion
- **Static file header extraction** — `_static.py` extracts `if-none-match`, `range`, and `accept-encoding` in a single pass over request headers
- **Shared socket for thread workers** — `net/listener.py` `create_listeners()` gains `shared=True` parameter; thread workers share one socket fd instead of using SO_REUSEPORT (avoids macOS distribution issues)
- **Server orchestrator refactored** — `server.py` simplified lifecycle state machine
- **Supervisor simplified** — `supervisor.py` streamlined worker spawning and health monitoring
- **Sync worker performance** — `sync_worker.py` major refactor for throughput parity with uvicorn

---

## [0.2.2] — 2026-03-12

### Added

- **Sync worker mode** — `SyncWorker` for blocking I/O request-response workloads. On Python 3.14t, runs in threads with true parallelism. One request at a time per thread, no asyncio. Streaming and WebSocket requests hand off to an async pool. CLI: `--worker-mode auto|sync|async` (default: auto — sync on 3.14t, async on GIL)
- **CPU affinity** — Pin each worker to a dedicated CPU core (Linux only). Reduces cache thrashing. CLI: `--cpu-affinity`
- **Per-worker ThreadPoolExecutor** — `executor_threads_per_worker` config prevents executor contention when multiple workers share one process (3.14t thread mode). 0 = auto-size
- **Response frame templates** — Fused sync path with `recv_into` buffer and `sendmsg` scatter-gather for lower overhead
- **Sync ASGI bridge** — `call_asgi_sync()` and `SyncApp` protocol for sync-style ASGI dispatch without asyncio
- **Async pool** — `AsyncPool` for streaming/WebSocket handoff from sync workers
- **Accept distributor** — Kernel-level connection distribution for multi-worker sync mode
- **Documentation** — Performance guide (`docs/about/performance.md`), thread-safety guide (`docs/about/thread-safety.md`)

### Changed

- **Scope building** — Optimized: cached ASGI version, tuple-based structures, deduplicated target split
- **Project metadata** — Updated description and keywords; Homepage/Documentation URLs point to lbliii.github.io

### Removed

- **httptools backend** — `pounce[fast]` extra removed. HTTP/1.1 parsing is h11-only (pure Python, free-threading compatible). httptools used Limited API C extensions incompatible with free-threaded Python.

---

## [0.2.1] — 2026-03-06

### Changed

- **HTTP/3 backend: aioquic → zoomies** — Replace aioquic with zoomies for HTTP/3 support. zoomies is sans-I/O, free-threading-native, and compatible with Python 3.14t. aioquic uses Limited API C extensions that do not work with free-threaded Python. `pounce[h3]` now installs `bengal-zoomies>=0.1.1` instead of `aioquic>=1.3.0`. 0-RTT is disabled until zoomies exposes it.

---

## [0.2.0] — 2026-02-13

Security hardening, production features, observability, and developer experience.

### Added

#### Security Hardening

- **Proxy header validation** — `_proxy.py` validates and applies `X-Forwarded-For`,
  `X-Forwarded-Proto`, and `X-Forwarded-Host` headers only from trusted peers
  (`ServerConfig.trusted_hosts`). Untrusted proxy headers are silently stripped to
  prevent IP spoofing. Supports H1 and H2 bridges
- **CRLF response header sanitization** — `_sanitize_headers()` in the ASGI bridge
  strips `\r` and `\n` characters from all response header names and values before
  serialization. Prevents header injection attacks from ASGI apps. Active on both
  HTTP/1.1 and HTTP/2
- **Slowloris protection** — `header_timeout` (default: 10s) limits the time to receive
  complete request headers. Uses a separate timeout from `keep_alive_timeout` for the
  initial header read vs inter-request idle period. CLI: `--header-timeout`
- **Narrowed exception handling** — Replaced broad `except Exception` and
  `contextlib.suppress(Exception)` blocks in worker with specific exception types
  (`OSError`, `ConnectionError`, `h11.LocalProtocolError`). Prevents silent swallowing
  of unexpected errors
- **HEAD compression guard** — Compression is disabled for HEAD responses to preserve
  the `Content-Length` header (compressor would mismatch sizes)
- **Bodyless response guard** — Compression is disabled for 204 and 304 responses
  (RFC 9110 §6.4.1) to prevent compressor flush bytes from producing a body

#### Network Completeness

- **Unix domain socket support** — `ServerConfig.uds` for UDS binding, with stale
  socket cleanup on startup and shutdown. All workers share a single UDS fd.
  CLI: `--uds /run/pounce.sock`. `net/listener.py` implements `_bind_unix_socket()`
  and `cleanup_unix_socket()`
- **Streaming body size enforcement** — `max_request_size` is now enforced for chunked
  and streaming request bodies (not just Content-Length). Applies to both H1 (via
  `_run_with_body_reader`) and H2 (per-stream byte tracking)
- **UDS peername handling** — Worker correctly handles Unix socket peername (string path
  or empty) instead of assuming a `(host, port)` tuple
- **503 backpressure response** — When `max_connections` is reached, new connections
  receive `503 Service Unavailable` with `Retry-After: 5` instead of silent close

#### Observability

- **Request ID generation** — `_request_id.py` generates UUID4 hex IDs for every
  request. Trusted proxies' `X-Request-ID` headers are honoured. IDs are injected into
  the ASGI scope (`scope["extensions"]["request_id"]`), response headers (`X-Request-ID`),
  and access logs (text and JSON). Works across H1 and H2
- **Built-in health endpoint** — `_health.py` responds to `GET` at
  `ServerConfig.health_check_path` (e.g. `/health`) before ASGI dispatch. Returns JSON
  with status, uptime, worker ID, and active connections. Excluded from access logs.
  CLI: `--health-check-path /health`
- **Prometheus metrics** — `metrics.py` provides `PrometheusCollector` implementing
  `LifecycleCollector`. Tracks `http_requests_total`, `http_request_duration_seconds`
  (histogram), `http_connections_active`, `http_requests_in_flight`, and
  `http_bytes_sent_total`. Thread-safe via `threading.Lock`. Export in Prometheus text
  exposition format via `collector.export()`
- **Built-in `/metrics` endpoint** — Configurable Prometheus scrape endpoint
  (`ServerConfig.metrics_path`, default `/metrics`) with zero external dependencies
- **Access log request IDs** — Text format appends `[<12-char-id>]`; JSON format
  includes full `request_id` field

#### Static File Serving

- **`_static.py`** — Pre-compressed files (`.gz`, `.zst`),
  ETags, and range requests. Configurable via `ServerConfig.static_files`,
  `static_precompressed`, `static_cache_control`

#### Middleware & Extensibility

- **Server-level middleware** — `ServerConfig.middleware` accepts a list of ASGI3
  middleware callables applied before the app
- **ASGI lifespan state sharing** — Lifespan state propagated to worker scopes for
  spec-compliant shared app state

#### Graceful Operations

- **Zero-downtime graceful reload** — SIGHUP triggers rolling worker restart with
  connection draining. `reload_timeout` configurable
- **Connection draining** — Enhanced graceful shutdown with `shutdown_timeout` for
  Kubernetes and orchestration platforms

#### WebSocket & Protocol

- **WebSocket permessage-deflate** — RFC 7692 compression for WebSocket connections.
  `ServerConfig.websocket_compression` (default: True)

#### Developer Experience

- **Development error pages** — `_debug.py` provides rich HTML tracebacks with syntax
  highlighting (Rosettes), local variables, and request context. Production-safe
  (`debug=False` returns plain 500)
- **Hot reload utilities** — `_hot_reload.py` for in-process module reimport without
  full process restart. `ServerConfig.reload_include`, `reload_dirs` for configurable
  file watching

#### Production Integrations

- **OpenTelemetry** — `_otel.py` native distributed tracing with OTLP export.
  `ServerConfig.otel_endpoint`, `otel_service_name`
- **Sentry** — `_sentry.py` optional error tracking. `sentry_dsn`, `sentry_environment`,
  `sentry_release`
- **Per-IP rate limiting** — `_rate_limiter.py` token bucket algorithm.
  `rate_limit_enabled`, `rate_limit_requests_per_second`, `rate_limit_burst`
- **Request queueing** — `_request_queue.py` bounded queue with load shedding (503).
  `request_queue_enabled`, `request_queue_max_depth`

#### Lifecycle & Logging

- **Structured lifecycle logging** — `lifecycle_logging` config for connection/request
  events with correlation IDs. `log_slow_requests_threshold` for slow request detection

#### H1/H2 Feature Parity

- All security and observability features wired for both HTTP/1.1 and HTTP/2 handlers

#### Tests

- New test modules: `test_request_id`, `test_health`, `test_proxy`, `test_security`,
  `test_metrics`, `test_metrics_endpoint`, `test_h2_bridge`, `test_listener_uds`,
  `test_bridge`, `test_static`, `test_middleware`, `test_graceful_reload`, `test_hot_reload`,
  `test_connection_draining`, `test_debug_error_pages`, `test_lifecycle_logging`,
  `test_lifespan_state`, `test_otel`, `test_rate_limiter`, `test_request_queue`,
  `test_sentry`, `test_websocket_compression`
- Integration tests for static files, WebSocket compression, lifespan state

---

## [0.1.0] — 2026-02-09

Initial release of Pounce — a free-threading-native ASGI server for Python 3.14t.

### Added

#### Configurable Reload Watch

- `ServerConfig.reload_include` — extra file extensions to watch beyond the built-in set
  (`.py`, `.yaml`, `.toml`, etc.). Pass a tuple of extensions like `(".html", ".css", ".md")`
  to trigger reloads on non-Python file changes
- `ServerConfig.reload_dirs` — extra directories to watch alongside the current working
  directory. Useful when templates or static assets live outside the project root
- CLI flags: `--reload-include ".html,.css,.md"` and `--reload-dir ./templates` (repeatable)
- Extensions without a leading dot are auto-prefixed (e.g. `"html"` becomes `".html"`)
- `_reload.py` functions (`_should_watch`, `_snapshot`, `detect_changes`, `watch_for_changes`)
  accept an `extensions` / `extra_extensions` parameter for runtime customization
- `parse_extensions()` and `parse_dirs()` helpers extracted in `_cli.py` for testability

#### Hot Reload with Module Reimport

- `reimport_app()` in `_importer.py` clears project-local modules from `sys.modules`,
  deletes stale `.pyc` bytecode caches, and calls `importlib.invalidate_caches()` before
  reimporting — code changes on disk take effect without a full process restart
- Single-worker and multi-worker reload paths both reimport when `app_path` is provided
- `Server` and `Supervisor` accept `app_path: str | None` to enable reimport on reload
- `_clear_local_modules()` resolves paths with `os.path.realpath()` for macOS symlink safety

#### Connection Lifecycle Events

- Structured, immutable event types for every stage of a connection's lifecycle:
  `ConnectionOpened`, `RequestStarted`, `ResponseCompleted`, `RequestFailed`,
  `ConnectionClosed` — all frozen dataclasses with nanosecond monotonic timestamps
- `LifecycleCollector` protocol — any object with a `record(event)` method can receive
  lifecycle events. `NoopCollector` (default) discards events with zero overhead.
  `BufferedCollector` stores events in a thread-safe deque for inspection
- `Server` and `Supervisor` now accept an optional `lifecycle_collector` parameter and
  forward it to every `Worker` they spawn. This enables external systems (e.g. Purr's
  `StackCollector`) to receive connection-level telemetry from all workers through a
  single collector instance
- Events are designed for aggregation and observability, not logging — use them to build
  latency distributions, connection counts, error rate dashboards, or full-stack event
  traces

#### Per-Worker Lifecycle Scopes

- Worker sends `pounce.worker.startup` scope to the ASGI app before accepting connections,
  and `pounce.worker.shutdown` after closing — both run on the worker's own event loop so
  async resources (httpx clients, DB pools) bind to the correct loop
- Timeout protection: 30s startup, 10s shutdown — apps that don't recognise the scope type
  time out gracefully instead of hanging
- `_worker_lifecycle_receive` returns `http.disconnect` immediately so apps that route
  unknown scopes to their HTTP handler unblock quickly
- If startup hook fails, the worker does not accept connections (prevents serving with
  uninitialised state); shutdown hook failure is non-fatal
- `tests/unit/test_worker_lifecycle.py` — 6 tests covering startup/shutdown delivery,
  ordering, startup failure, shutdown failure, and unknown-scope handling

#### ASGI 3.0 Compliance Suite

- `tests/integration/test_asgi_compliance.py` — 41 tests validating pounce against the
  ASGI 3.0 HTTP Connection Scope and Lifespan specs: scope completeness, all HTTP methods,
  header lowercasing, path decoding, query strings, request body protocol, response
  streaming, keep-alive, Connection: close, error handling, lifespan lifecycle

---

**Phase 4: It's Fast** — performance optimization, correctness fixes, benchmark infrastructure.

#### POST Request Body Reading (Correctness Fix)

- Worker now reads POST/PUT/PATCH request bodies correctly. Restructured `_handle_request`
  to collect body events from the initial h11 parse batch and, for bodies spanning multiple
  socket reads, runs a concurrent body reader task alongside the ASGI app
- Removed xfail markers from `test_post_body_echo` and `test_large_body`
- Added tests for PUT body, streaming multi-chunk body

#### App Factory Support

- `pounce "myapp:create_app()"` works end-to-end — the importer already supported factory
  detection; CLI, integration tests, and example app now verify the full pipeline
- Added `examples/factory_app.py` demonstrating the factory pattern

#### Optional httptools Backend (`pounce[fast]`)

- `protocols/h1_httptools.py` — C-accelerated HTTP/1.1 parser implementing the same
  `ProtocolHandler` interface as `H1Protocol` (h11). Uses httptools callbacks for parsing
  and hand-crafted response serialization for speed
- Worker auto-detects httptools at import time; `pip install pounce[fast]` is the opt-in
- Full unit test suite for the httptools backend (skips when not installed)
- `pyproject.toml` adds `fast` optional extra: `httptools>=0.6`

#### Benchmark Suite

- `benchmarks/run_benchmark.py` — reproducible benchmark runner that starts pounce, drives
  load with wrk or hey, captures results as structured JSON, prints markdown summary table
- Comparison mode: `--compare` runs the same workload against uvicorn
- Workloads: hello-world (overhead), JSON (serialize), POST echo (body reading)
- Dedicated benchmark apps in `benchmarks/apps/`

#### Profiling Infrastructure

- `benchmarks/profile_hotpath.sh` — wraps py-spy for flame graph generation under load
- `benchmarks/profile_memory.py` — RSS tracking with optional tracemalloc integration

#### Hot-Path Optimizations

- Pre-computed ASGI spec dict constant (avoid per-request dict allocation)
- Bodyless fast-path receive: skip asyncio.Queue for GET/HEAD requests
- Write coalescing: head + first body chunk combined into single write for responses < 16KB
- Single-pass header lookup for compression negotiation
- Skip empty body writes (avoid zero-length syscalls)

#### CI

- `.github/workflows/ci.yml` — GitHub Actions pipeline: lint (ruff check + format), type
  check (ty), and tests on a 2x2 matrix (ubuntu/macos x Python 3.14/3.14t). Includes GIL
  status verification on free-threaded builds. 15-minute timeout per the py-free-threading
  CI guide

### Changed

- Removed `from __future__ import annotations` from all 43 source, test, example, and
  benchmark files — not needed on Python 3.14 (PEP 563 import is a no-op)
- Registered `timeout` pytest marker in `pyproject.toml` (silences 6 warnings)

---

**Phase 3: It's Complete** — full protocol support, TLS, WebSocket, HTTP/2, modern HTTP features.

#### TLS Termination

- `net/tls.py` — `create_tls_context()` for stdlib `ssl.SSLContext` with secure defaults
  (TLSv1.2+, no compression), ALPN protocol advertisement (`h2`, `http/1.1`), optional
  `truststore` integration for system certificate stores
- `is_tls_configured()` helper for conditional context creation
- CLI flags: `--ssl-certfile`, `--ssl-keyfile`
- `TLSError` added to error hierarchy
- Startup banner shows `tls: enabled` when active

#### WebSocket Protocol

- `protocols/ws.py` — `WSProtocol` sans-I/O wrapper around wsproto for server-side
  WebSocket framing. Manual `101 Switching Protocols` HTTP response construction
  (wsproto 1.x expects HTTP upgrade handled externally)
- `build_ws_accept_key()` for RFC 6455 `Sec-WebSocket-Accept` computation
- `build_101_response()` for raw HTTP upgrade response bytes
- `asgi/ws_bridge.py` — `build_ws_scope()`, `create_ws_receive()`, `create_ws_send()`
  for full ASGI WebSocket lifecycle (`websocket.connect`, `websocket.accept`,
  `websocket.send`, `websocket.close`)
- New event types: `WebSocketConnected`, `WebSocketDataReceived`, `WebSocketDisconnected`

#### HTTP/2 Protocol

- `protocols/h2.py` — `H2Connection` sans-I/O wrapper around the h2 library. Stream
  multiplexing, per-stream event types (`H2RequestReceived`, `H2BodyReceived`,
  `H2StreamReset`, `H2GoAway`, `H2WindowUpdated`, `H2WebSocketRequest`), flow control,
  GOAWAY handling
- `asgi/h2_bridge.py` — `build_h2_scope()`, `create_h2_receive()`, `create_h2_send()`
  for per-stream ASGI dispatch with concurrent stream tasks
- ALPN negotiation in worker: `selected_alpn_protocol() == "h2"` → H2 connection handler
- `SETTINGS_ENABLE_CONNECT_PROTOCOL` for RFC 8441 WebSocket over HTTP/2

#### Protocol Negotiation

- Worker dynamically branches connections based on ALPN result (H2) or HTTP/1.1 upgrade
  headers (WebSocket), falling through to standard HTTP/1.1 keep-alive loop
- `_is_websocket_upgrade()` helper: detects `Connection: Upgrade` + `Upgrade: websocket`

#### WebSocket over HTTP/2 (RFC 8441)

- Extended CONNECT detection in `H2Connection.receive_data()`: `:method = CONNECT` +
  `:protocol = websocket` emits `H2WebSocketRequest` event
- `_handle_h2_websocket_stream()` in worker manages WS framing within H2 streams

#### Priority Signals (RFC 9218)

- `_priority.py` — `parse_priority()` for `Priority` header parsing (urgency 0-7,
  incremental boolean), `StreamPriority` dataclass, `PriorityScheduler` min-heap for
  urgency-based DATA frame scheduling

#### 103 Early Hints

- H2 ASGI bridge: `status == 103` in `http.response.start` sends informational headers
  without marking response as started (allows multiple early hints before final response)
- H1 ASGI bridge: silently skips `status == 103` (browser support inconsistent over H1)

#### Dev Reload

- `_reload.py` — file watcher with polling: `_snapshot()`, `detect_changes()`,
  `watch_for_changes()` with configurable interval and stop event
- Excludes `__pycache__`, `.git`, `.venv`, `node_modules`, etc.
- Watches `.py`, `.yaml`, `.toml`, `.json`, `.cfg`, `.ini` extensions
- Single-worker mode: restart loop (shutdown → recreate socket → restart asyncio)
- Multi-worker mode: `Supervisor.restart_workers()` drains all workers, clears shutdown
  event, respawns fresh workers
- CLI flag: `--reload`
- `ReloadError` added to error hierarchy
- Startup banner shows `reload: enabled` when active

#### Keep-Alive Tuning

- `max_requests_per_connection` config field (0 = unlimited): enforced in the HTTP/1.1
  keep-alive loop — closes connection after N requests
- CLI flags: `--keep-alive-timeout`, `--max-requests-per-connection`
- Config validation: `keep_alive_timeout > 0`, `max_requests_per_connection >= 0`
- Startup banner shows non-default keep-alive and max-requests values

#### Package Wiring

- `protocols/__init__.py` — re-exports `WSProtocol`, `H2Connection`, all H2 event types
- `asgi/__init__.py` — re-exports WS and H2 bridge functions
- `net/__init__.py` — re-exports `create_tls_context`, `is_tls_configured`

#### Tests (408 passing — unit + integration + compliance)

- TLS: context creation, secure defaults, ALPN, missing cert handling, truststore
- WebSocket: `WSProtocol` framing, `build_ws_accept_key`, `build_101_response`,
  `build_ws_scope`, `_is_websocket_upgrade` header detection
- HTTP/2: `H2Connection` init, request/response lifecycle, multiplexed streams,
  stream reset, GOAWAY
- Priority Signals: `parse_priority`, `PriorityScheduler` urgency ordering
- Dev Reload: `_snapshot`, `detect_changes`, file creation/modification/deletion,
  exclude patterns
- Compression: updated for Brotli exclusion (GIL-incompatible on 3.14t)
- Config: validation for `keep_alive_timeout` and `max_requests_per_connection`
- Supervisor: `restart_workers()` event clearing and worker joining
- CLI: Phase 3 flag parsing (TLS, reload, keep-alive, max-requests)
- Package exports: Phase 3 protocol, ASGI, net, and error exports
- Error hierarchy: `TLSError` and `ReloadError`

---

**Phase 2: It Scales** — multi-worker mode with automatic GIL detection.

#### Runtime Detection

- `_runtime.py` — `is_gil_enabled()` wrapping `sys._is_gil_enabled()` with safe fallback
  for Python < 3.13; `detect_worker_mode()` returning `"thread"` (nogil) or `"process"`
  (GIL); `default_worker_count()` from `os.cpu_count()`

#### Supervisor

- `supervisor.py` — `Supervisor` class that spawns N workers as `threading.Thread` (on
  nogil / 3.14t) or `multiprocessing.Process` (on GIL builds). Health monitoring via
  watchdog loop (1s interval), crash detection and automatic restart with budget (max 5
  restarts per 60s window), graceful shutdown coordination via `threading.Event`, per-worker
  connection limit calculation, SIGINT/SIGTERM signal forwarding

#### Worker Enhancements

- External `threading.Event` shutdown bridge — supervisor sets a threading event, the
  worker's `_bridge_shutdown` task polls it every 250ms and bridges to asyncio via
  `loop.call_soon_threadsafe`
- Per-worker connection backpressure — rejects connections when at capacity
- Worker ID for log differentiation (`pounce.worker.0`, `pounce.worker.1`, etc.)
- Thread-safe `shutdown()` method using `call_soon_threadsafe`

#### Network

- `create_listeners(config, count)` — multi-socket creation strategy: per-worker
  independent sockets with `SO_REUSEPORT` on Linux (kernel-level distribution), shared
  socket fallback on macOS (single fd, all workers accept)

#### Server Orchestration

- Single-worker fast path (`workers=1`) — skips supervisor entirely, no overhead
- Multi-worker path delegates to `Supervisor` for lifecycle management
- ASGI lifespan runs once in main thread before workers spawn
- Startup banner now shows GIL status (`nogil` / `GIL`) and worker mode
- Socket deduplication on cleanup for shared-fd safety

#### Configuration

- `workers=0` auto-detect semantics via `resolve_workers()` (defaults to `os.cpu_count()`)
- `__post_init__` validation for workers (>= 0) and port (0-65535)
- CLI `--workers 0` for auto-detect with updated help text

#### Error Hierarchy

- `SupervisorError` — worker spawn failures, crash-restart exhaustion
- `WorkerError` — worker-level failures reported to supervisor

#### Benchmarks

- `benchmarks/hello_app.py` — minimal ASGI app for throughput benchmarking
- `benchmarks/sse_app.py` — SSE streaming app for stress testing
- `benchmarks/test_throughput.py` — automated throughput scaling benchmark (single-worker
  baseline ~6-7k req/s, multi-worker validated via shared-socket workers)
- `benchmarks/test_memory.py` — thread vs process RSS comparison (thread workers use
  shared interpreter, ~3MB delta for 4 workers)
- `benchmarks/test_sse_stress.py` — SSE stress test: 100 concurrent streams held 10s,
  ~20k events delivered, RSS growth < 3MB (no memory leak)
- `benchmarks/test_chirp_compat.py` — chirp App compatibility verification (chirp hello-world
  served through pounce Worker without modification)
- `benchmarks/README.md` — instructions for wrk/hey benchmarking

#### Tests (253 + 7 benchmark tests, all passing)

- Unit tests for runtime detection: GIL state, worker mode, CPU count fallback
- Unit tests for supervisor: init, mode detection, socket validation, shutdown, spawn/stop,
  respawn budget, restart window pruning, per-worker connection limits
- Unit tests for listener multi-socket: create_listeners, strategy detection, SO_REUSEPORT
  vs shared, count validation
- Unit tests for worker: external shutdown bridge, internal shutdown, worker ID, backpressure
- Integration tests for multi-worker: concurrent requests across workers, graceful shutdown,
  worker liveness, supervisor mode reporting
- Integration tests for server: _close_sockets deduplication, shared-fd handling
- Updated conftest and test_server to use explicit `worker_id=0`
- Updated package export tests for Phase 2 modules

---

**Phase 1: It Runs** — the minimal viable ASGI server.

#### Primitives

- `_errors.py` — `PounceError` hierarchy with HTTP status code mapping: `ParseError`
  (400), `TimeoutError` (408), `LimitError` (413/431), `AppError` (500), `LifespanError`
  (500)
- `_timing.py` — `monotonic_ns()`, `elapsed_ms()` clock utilities; `ServerTiming` builder
  for the `Server-Timing` HTTP header
- `_importer.py` — resolve `"module:attribute"` and `"module:factory()"` strings to ASGI
  callables with clear error messages
- `_compression.py` — `Accept-Encoding` negotiation (zstd > gzip > identity, respects
  q-values), per-request `ZstdCompressor` (stdlib `compression.zstd`) and `GzipCompressor`
  (stdlib `zlib`) instances
- `_types.py` — ASGI 3.0 type aliases: `Scope`, `Receive`, `Send`, `ASGIApp`
- `config.py` — `ServerConfig` frozen dataclass with bind address, timeouts, limits,
  compression, `root_path`, `server_timing`, access log, and h11 tuning fields

#### Protocol Layer

- `protocols/_base.py` — `ProtocolHandler` runtime-checkable Protocol; typed event
  dataclasses: `RequestReceived`, `BodyReceived`, `ConnectionClosed`, `Upgraded`;
  `ProtocolEvent` union type
- `protocols/h1.py` — sans-I/O HTTP/1.1 handler wrapping h11: request parsing, response
  serialization, keep-alive cycling, malformed-input detection

#### ASGI Bridge

- `asgi/bridge.py` — `build_scope()` (HTTP scope from protocol events + config),
  `create_receive()` (async body stream from queue), `create_send()` (streaming-first
  writes with optional compression and Server-Timing injection)
- `asgi/lifespan.py` — `run_lifespan()` async context manager: startup/shutdown events,
  failure handling, timeout, graceful no-lifespan fallback

#### Network and Worker

- `net/listener.py` — socket creation with `SO_REUSEADDR`/`SO_REUSEPORT`, non-blocking
  bind, clear error messages for EADDRINUSE/EACCES
- `logging.py` — stdlib logging configuration; structured access log format:
  `{client} - "{method} {path} HTTP/1.1" {status} {bytes} {duration}ms`
- `worker.py` — asyncio event loop accepting connections through the full pipeline:
  parse → scope → negotiate compression → ASGI app → response → access log. Keep-alive
  cycling, error responses (400/500), configurable timeouts

#### Server and CLI

- `server.py` — full lifecycle orchestration: CONFIG → BIND → LIFESPAN → SERVE → SHUTDOWN.
  Signal handling (SIGINT/SIGTERM), startup banner with version/URL/workers/features
- `_cli.py` — `pounce myapp:app` CLI via argparse: `--host`, `--port`, `--workers`,
  `--log-level`, `--root-path`, `--no-compression`, `--server-timing`, `--no-access-log`
- `__init__.py` — public API: `pounce.run()`, `ServerConfig`, ASGI type re-exports

#### Package Wiring

- `protocols/__init__.py` — re-exports `H1Protocol`, `ProtocolHandler`, all event types
- `asgi/__init__.py` — re-exports `build_scope`, `create_receive`, `create_send`,
  `run_lifespan`
- `net/__init__.py` — re-exports `create_listener`
- Top-level `__init__.py` — re-exports `ASGIApp`, `Scope`, `Receive`, `Send`

#### Tests (188 passing)

- Unit tests for all primitives: errors, timing, importer, protocol events, config
- Unit tests for H1 protocol: parsing, serialization, keep-alive, malformed input
- Unit tests for compression: negotiation, roundtrip, browser Accept-Encoding strings
- Unit tests for ASGI bridge: scope construction, streaming send, compression/timing injection
- Unit tests for lifespan: happy path, failure, no-lifespan apps, shutdown timeout
- Unit tests for listener: socket properties, non-blocking, reuseaddr
- Unit tests for logging: format correctness
- Unit tests for package exports: all `__init__.py` re-exports verified
- Integration tests for worker: hello world, echo, streaming, error handling, malformed input
- Integration tests for server: start/respond lifecycle, lifespan events
- Integration tests for CLI: parser defaults/overrides, invalid app handling, public API imports
- Shared `conftest.py` with lifespan-aware test apps and `start_worker`/`send_raw_request` helpers

#### Infrastructure

- Project scaffolding: `pyproject.toml` with ruff, ty, pytest, poe task runner
- `py.typed` PEP 561 marker
- `_Py_mod_gil = 0` free-threading declaration

[0.5.1]: https://github.com/lbliii/pounce/releases/tag/v0.5.1
[0.5.0]: https://github.com/lbliii/pounce/releases/tag/v0.5.0
[0.4.0]: https://github.com/lbliii/pounce/releases/tag/v0.4.0
[0.3.1]: https://github.com/lbliii/pounce/releases/tag/v0.3.1
[0.3.0]: https://github.com/lbliii/pounce/releases/tag/v0.3.0
[0.2.2]: https://github.com/lbliii/pounce/releases/tag/v0.2.2
[0.2.1]: https://github.com/lbliii/pounce/releases/tag/v0.2.1
[0.2.0]: https://github.com/lbliii/pounce/releases/tag/v0.2.0
[0.1.0]: https://github.com/lbliii/pounce/releases/tag/v0.1.0
