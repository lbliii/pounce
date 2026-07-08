# ADR: `/_pounce/info` Endpoint — Field Redaction Allowlist

**Status**: Accepted
**Date**: 2026-04-20
**Epic**: [vibe-coding-epic.md](../plans/vibe-coding-epic.md) — Sprint 0.3
**Decider**: Sprint 0 design task

## Context

Sprint 4 adds an opt-in `/_pounce/info` endpoint returning JSON runtime state for an agent or human debugging a live pounce instance. The endpoint exposes a redacted view of `ServerConfig` alongside live counters.

A leak from this endpoint is severe: pounce runs in production, the endpoint might be reachable through misconfigured proxies, and agent-composed tooling will increasingly call it. Fail-closed design is non-negotiable.

## Decision

**Fail-closed allowlist**, not denylist. Every `ServerConfig` field is explicitly classified as `EXPOSE`, `REDACT`, or `REDACT_TO_BOOL`. New fields default to `REDACT` — they do not appear in `/info` until someone deliberately adds them to the allowlist.

```python
# Sentinel for fields that exist but their *value* is replaced with a boolean
# indicating whether the field is set. E.g. ssl_certfile -> {"ssl_certfile_set": true}.
REDACT_TO_BOOL = object()
```

### Classification Rules

- **EXPOSE** — Value is safe in logs, tickets, screenshots. Integers, booleans, enum strings, non-path strings without user data.
- **REDACT_TO_BOOL** — Existence is useful signal for debugging ("is TLS configured?"), but the value is a path/secret/identity. Emit as `<name>_set: true|false`.
- **REDACT** — Field is omitted entirely. Default for anything that is, or could plausibly contain: filesystem paths, credentials, user hostnames, DSNs, arbitrary user-controlled strings that could leak app topology.

## Allowlist

Classification for every non-internal field in `ServerConfig` as of 2026-04-20 (schema prototype enumerates 67 fields; entries below cover each):

### Bind & workers — EXPOSE

| Field                         | Classification    | Note                                              |
|-------------------------------|-------------------|---------------------------------------------------|
| `host`                        | REDACT_TO_BOOL    | May be internal hostname                          |
| `port`                        | EXPOSE            |                                                   |
| `workers`                     | EXPOSE            |                                                   |
| `backlog`                     | EXPOSE            |                                                   |
| `worker_mode`                 | EXPOSE            | Enum: auto/sync/async/subinterpreter              |
| `cpu_affinity`                | EXPOSE            |                                                   |
| `executor_threads_per_worker` | EXPOSE            |                                                   |

### Timeouts & limits — EXPOSE

All timeouts and limits are numeric and non-sensitive.

| Field                            | Classification |
|----------------------------------|----------------|
| `keep_alive_timeout`             | EXPOSE         |
| `request_timeout`                | EXPOSE         |
| `write_timeout`                  | EXPOSE         |
| `header_timeout`                 | EXPOSE         |
| `startup_timeout`                | EXPOSE         |
| `shutdown_timeout`               | EXPOSE         |
| `max_request_size`               | EXPOSE         |
| `max_header_size`                | EXPOSE         |
| `max_headers`                    | EXPOSE         |
| `max_connections`                | EXPOSE         |
| `max_requests_per_connection`    | EXPOSE         |
| `h11_max_incomplete_event_size`  | EXPOSE         |
| `reload_timeout`                 | EXPOSE         |

### Logging — mostly EXPOSE

| Field                | Classification    | Note                             |
|----------------------|-------------------|----------------------------------|
| `access_log`         | EXPOSE            |                                  |
| `log_level`          | EXPOSE            | Enum                             |
| `log_format`         | EXPOSE            | Enum                             |
| `lifecycle_logging`  | EXPOSE            |                                  |
| `log_slow_requests_threshold` | EXPOSE   |                                  |
| `app_name`           | REDACT_TO_BOOL    | User-controlled string           |
| `app_tagline`        | REDACT_TO_BOOL    | User-controlled string           |
| `app_version`        | EXPOSE            | Version strings are typically OK |
| `signage`            | EXPOSE            | Enum                             |

### HTTP — mixed

| Field                  | Classification    | Note                                                |
|------------------------|-------------------|-----------------------------------------------------|
| `server_header`        | REDACT_TO_BOOL    | Users set this to hide that they use pounce         |
| `http2_enabled`        | EXPOSE            | Protocol negotiation policy                         |
| `date_header`          | EXPOSE            |                                                     |
| `root_path`            | REDACT_TO_BOOL    | URL structure hint                                  |
| `compression`          | EXPOSE            |                                                     |
| `compression_min_size` | EXPOSE            |                                                     |
| `server_timing`        | EXPOSE            |                                                     |

### Development flags — EXPOSE

| Field             | Classification |
|-------------------|----------------|
| `debug`           | EXPOSE         |
| `reload`          | EXPOSE         |
| `reload_include`  | REDACT_TO_BOOL | Could reveal source structure |
| `reload_dirs`     | REDACT_TO_BOOL | Paths                          |

### Trust & networking — REDACT_TO_BOOL

| Field                      | Classification    | Note                                      |
|----------------------------|-------------------|-------------------------------------------|
| `trusted_hosts`            | REDACT_TO_BOOL    | Reveals reverse-proxy topology            |
| `trusted_hosts_wildcard`   | EXPOSE            | Boolean; reveals policy stance only       |
| `health_check_path`        | REDACT_TO_BOOL    | Knowing the path enables probe abuse      |
| `uds`                      | REDACT_TO_BOOL    | Filesystem path                           |
| `uds_permissions`          | EXPOSE            | Numeric mode                              |

### TLS — REDACT_TO_BOOL

| Field            | Classification    |
|------------------|-------------------|
| `ssl_certfile`   | REDACT_TO_BOOL    |
| `ssl_keyfile`    | REDACT_TO_BOOL    |

### Static files — REDACT

| Field                      | Classification | Note                                                                        |
|----------------------------|----------------|-----------------------------------------------------------------------------|
| `static_files`             | REDACT_TO_BOOL + count | Emit `{"static_files_configured": N}` — count is useful, paths are not |
| `static_cache_control`     | EXPOSE         |                                                                             |
| `static_precompressed`     | EXPOSE         |                                                                             |
| `static_follow_symlinks`   | EXPOSE         | Security-relevant knob; visibility helps auditing                           |
| `static_index_file`        | EXPOSE         | Almost always `index.html`; low sensitivity                                 |

### WebSocket — EXPOSE

| Field                            | Classification |
|----------------------------------|----------------|
| `websocket_compression`          | EXPOSE         |
| `websocket_max_message_size`     | EXPOSE         |

### Observability — mixed

| Field                             | Classification    | Note                         |
|-----------------------------------|-------------------|------------------------------|
| `metrics_enabled`                 | EXPOSE            |                              |
| `metrics_path`                    | REDACT_TO_BOOL    | Path                         |
| `otel_endpoint`                   | REDACT_TO_BOOL    | May be internal URL          |
| `otel_service_name`               | EXPOSE            |                              |
| `sentry_dsn`                      | REDACT_TO_BOOL    | **Contains credentials**     |
| `sentry_environment`              | EXPOSE            |                              |
| `sentry_release`                  | EXPOSE            |                              |
| `sentry_traces_sample_rate`       | EXPOSE            |                              |
| `sentry_profiles_sample_rate`     | EXPOSE            |                              |

### Rate limiting — EXPOSE

| Field                                 | Classification |
|---------------------------------------|----------------|
| `rate_limit_enabled`                  | EXPOSE         |
| `rate_limit_requests_per_second`      | EXPOSE         |
| `rate_limit_burst`                    | EXPOSE         |
| `request_queue_enabled`               | EXPOSE         |
| `request_queue_max_depth`             | EXPOSE         |

### HTTP/3 — EXPOSE

| Field                                  | Classification |
|----------------------------------------|----------------|
| `http3_enabled`                        | EXPOSE         |
| `http3_max_connections`                | EXPOSE         |
| `http3_idle_timeout`                   | EXPOSE         |
| `http3_qpack_max_table_capacity`       | EXPOSE         |
| `http3_zero_rtt_enabled`               | EXPOSE         |

### Sprint 4 additions (new fields — see also ADR 0.5)

| Field                        | Classification |
|------------------------------|----------------|
| `introspection_enabled`      | EXPOSE         |
| `introspection_bind`         | REDACT_TO_BOOL |
| `introspection_path`         | REDACT_TO_BOOL |

### Never exposed (`_IIC_SKIP_FIELDS` already drops them)

`access_log_filter`, `compression_dictionaries`, `middleware`, `display` — callables and opaque objects. Cannot be JSON-serialized; not candidates for `/info`.

## Counters (not from `ServerConfig`)

Live fields added alongside the redacted config view:

| Field                | Classification | Source                                         |
|----------------------|----------------|------------------------------------------------|
| `pounce_version`     | EXPOSE         | `pounce.__version__`                           |
| `build_id`           | EXPOSE         | `POUNCE_BUILD_ID`, explicitly public or `null` |
| `python_version`     | EXPOSE         | `sys.version`                                  |
| `python_build`       | EXPOSE         | Implementation, compiler, build, free-threaded capability |
| `gil_enabled`        | EXPOSE         | `sys._is_gil_enabled()`                        |
| `worker_mode`        | EXPOSE         | Configured `ServerConfig.worker_mode` value    |
| `worker_model`       | EXPOSE         | Resolved worker strategy and execution model   |
| `uptime_seconds`     | EXPOSE         | `time.monotonic()` - startup                   |
| `worker_id`          | EXPOSE         | Current worker identity                        |
| `active_connections` | EXPOSE         | Lifecycle state                                |

`POUNCE_BUILD_ID` is the only environment variable read into this response.
Setting it is an explicit request to publish that exact value, so operators
must use a non-secret identifier such as a git SHA or freeze fingerprint. An
unset or empty value is returned as `null`; arbitrary environment variables
remain outside the introspection contract.

## Implementation Notes for Sprint 4.1

- Implement as the module-level `INFO_ALLOWLIST: dict[str, Literal["EXPOSE", "REDACT_TO_BOOL"]]` in `_config_schema.py`. `_introspect.py` consumes the redacted view from that module. Any field absent from this mapping is implicitly redacted (fail-closed).
- Unit test: walk `dataclasses.fields(ServerConfig)`, assert every non-skipped field is in the allowlist. Adding a new `ServerConfig` field without updating the allowlist fails CI.
- The "introspection endpoint might leak X" regression test: construct a `ServerConfig` with canary values (`ssl_certfile="/CANARY/cert.pem"`, `sentry_dsn="https://CANARY@o.ingest.sentry.io/"`, `uds="/tmp/CANARY.sock"`), call `/info`, assert `"CANARY"` appears in no response byte.

## Alternatives Considered

### Denylist

Default-expose, explicitly hide secrets.

**Rejected.** Classic "just one more field" bug pattern. Someone adds `api_proxy_credentials` to `ServerConfig`, forgets the denylist, and we leak credentials by default. Fail-closed reverses the polarity — new fields are invisible until deliberately approved.

### Expose raw TOML config file as-is

Simpler: just read `pounce.toml` and emit it.

**Rejected.** Couples `/info` to file-based config (breaks when config is programmatic), loses runtime-resolved values (`workers=0` → actual core count), and abdicates any secrets hygiene back to the user.

### Expose with a `?include_secrets=true` flag

**Rejected outright.** There is no legitimate use case for exposing TLS private key paths over HTTP. If you want the full config, read the file.

## Consequences

- Adding a `ServerConfig` field is a two-step change: add to dataclass, add to `_config_schema.INFO_ALLOWLIST`. CI rejects the first without the second.
- Agent tooling gets a stable, safe contract to key on.
- In the worst case (endpoint accidentally exposed to the internet), the blast radius is bounded — attacker learns timeouts and feature flags, nothing more.
