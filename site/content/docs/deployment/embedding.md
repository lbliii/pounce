---
title: Framework Embedding
description: Minimum production contract for frameworks that launch Pounce directly
draft: false
weight: 37
lang: en
type: doc
tags: [deployment, frameworks, serverconfig, lifecycle, proxy]
keywords: [embedding, ServerConfig, Server, app_path, lifecycle, trusted_hosts]
category: guide
---

Frameworks can serve their ASGI app through `pounce.run()` or construct
`pounce.server.Server` directly. Direct construction is useful when a framework
has a fused sync callable, a lifecycle collector, or its own CLI—but it creates
an adapter contract that must stay aligned with Pounce.

The goal is not to copy every `ServerConfig` field into a framework's public
configuration. The goal is to make every production-critical difference an
intentional, documented policy instead of an accidental Pounce default.

## Prefer A Frozen Config Pass-Through

The smallest drift-resistant integration accepts a pre-built `ServerConfig`:

```python
from pounce import ServerConfig
from pounce.server import Server


def serve_framework_app(
    app,
    *,
    server_config: ServerConfig,
    app_path: str | None = None,
    sync_app=None,
    lifecycle_collector=None,
) -> None:
    Server(
        server_config,
        app,
        app_path=app_path,
        sync_app=sync_app,
        lifecycle_collector=lifecycle_collector,
    ).run()
```

`ServerConfig` is frozen shared state. Build it once before workers start; do
not mutate a framework config object and expect running workers to follow.

If a framework maps its own config fields into `ServerConfig`, keep that
mapping in one function and test both the framework's programmatic launcher and
CLI launcher against it.

## Minimum Decision Surface

“Decision” means forward a user value, accept a complete `ServerConfig`, or
document a deliberate framework-owned fixed policy.

| Area | Pounce surface | Why an embedding framework must decide |
|---|---|---|
| Bind and workers | `host`, `port`, `uds`, `workers`, `worker_mode` | Determines listener ownership and whether workers are threads, processes, async workers, or subinterpreters. |
| Application identity | `Server(..., app_path=...)` | Required for subinterpreter import and for reload paths that reimport application code. A live callable alone is not importable in another interpreter. |
| Worker hooks | `worker_startup_failure` | Generic ASGI compatibility defaults to `ignore`; frameworks with required worker hooks normally need `shutdown` so readiness cannot succeed after hook failure. |
| Lifecycle bounds | `startup_timeout`, `shutdown_timeout`, `reload_timeout` | Bounds deploy readiness, graceful shutdown, and generation rotation. Platform drain windows must be longer than Pounce shutdown bounds. |
| Connection timeouts | `header_timeout`, `keep_alive_timeout`, `request_timeout`, `write_timeout` | Preserve the state-specific header, request-body, between-request idle, and blocked-output controls independently. HTTP/3 output liveness uses `http3_idle_timeout`. |
| Request envelope | `max_request_size`, `max_header_size`, `max_headers` | Pounce validates bytes before the framework sees them. A larger framework body limit is ineffective if Pounce retains its smaller default. |
| Capacity | `backlog`, `max_connections`, `executor_threads_per_worker` | Bounds queued connections, live connections, and blocking work offloaded from async handlers. |
| Proxy authority | `trusted_hosts`, `forwarded_for_trusted_hops`, `root_path` | Controls trusted client IP, scheme, host authority, and subpath routing before framework middleware runs. |
| TLS and protocols | `ssl_certfile`, `ssl_keyfile`, `http3_enabled` and protocol extras | Defines whether Pounce or an edge owns TLS and which optional protocol handlers can be selected. |
| Operator surfaces | health/readiness, metrics, introspection, logging, lifecycle collector | A framework may own these endpoints, but collisions, redaction, and disabled Pounce paths must be deliberate. |

Feature-specific fields—static files, middleware, compression dictionaries,
Sentry, OpenTelemetry, rate limiting, and request queueing—only need adapter
surface when the framework advertises that Pounce-owned feature.

## Constructor Inputs Are Part Of The Contract

`ServerConfig` is not the entire embedding API:

- `app_path` is the import identity for subinterpreters and code reimport.
- `sync_app` enables the fused sync path for frameworks that implement Pounce's
  sync callable contract. Omitting it is valid, but gives up that path.
- `lifecycle_collector` connects framework/operator lifecycle evidence without
  changing the ASGI app.

If a framework cannot supply `app_path`, it should reject
`worker_mode="subinterpreter"` with an actionable error rather than silently
falling back or starting an unusable worker.

## Framework-Owned Endpoints

A framework may own `/health`, `/ready`, metrics, or introspection. In that
case, set the overlapping Pounce endpoint to disabled and test the framework's
equivalent behavior:

- readiness turns non-200 when drain begins and before process termination;
- health and readiness paths do not collide with user routes silently;
- metrics names and paths are stable if publicly documented;
- runtime/config inspection remains redacted and off by default when exposed.

“The framework owns it” is a valid policy. Leaving the Pounce field at its
default without documenting the replacement is not.

## Adapter Proof Checklist

At minimum, run these through the real framework launcher and a real Pounce
socket:

1. A request below and above the configured body limit.
2. Trusted and untrusted forwarded authority, including the configured hop
   count.
3. Required worker startup success and failure before readiness.
4. SIGTERM drain bounded by the configured shutdown timeout.
5. Every supported worker mode; reject unsupported modes before binding.
6. Programmatic and CLI launchers produce the same `ServerConfig` policy.

Snapshot tests of a constructor call are useful drift guards, but lifecycle,
protocol, and proxy claims still need observable worker/socket tests.

## Chirp Audit Snapshot

The issue that produced this guide audited Chirp `main` at commit
`9ada3ba4b26ed37fbfde0ef69b60c3897830d3d3` on 2026-07-08.

Already aligned:

- `worker_mode`, `metrics_path`, `keep_alive_timeout`, `request_timeout`, and
  `write_timeout`;
- `trusted_proxies` → `trusted_hosts` plus
  `forwarded_for_trusted_hops`;
- TLS, logging, backpressure, WebSocket, OpenTelemetry, and Sentry settings;
- Chirp auth rate limiting uses the Pounce-normalized ASGI client rather than
  reparsing raw `X-Forwarded-For`.

Machine-verified gaps:

- Chirp's 16 MiB `max_request_body_size` is not passed to Pounce, whose 1 MiB
  default rejects larger requests before Chirp sees them.
- Chirp pins Pounce 0.8.2 and rejects sync worker hooks using the old Pounce 0.7
  lifecycle limitation; Pounce [#244](https://github.com/lbliii/pounce/issues/244)
  and [#245](https://github.com/lbliii/pounce/issues/245) now provide sync hooks
  and fail-loud startup on main.
- `worker_startup_failure`, `startup_timeout`, `shutdown_timeout`,
  `header_timeout`, and `executor_threads_per_worker` are not adapter inputs.
- Chirp passes a live app without `app_path` and deliberately rejects
  subinterpreter mode pending an import contract.

The downstream work is tracked in
[Chirp #627](https://github.com/lbliii/chirp/issues/627). This is a dated audit
snapshot; use Chirp's current source and issue state for the live answer.

## See Also

- [[docs/deployment/production|Production]] — General production setup
- [[docs/deployment/railway|Railway]] — Edge/origin topology and proxy trust
- [[docs/configuration/server-config|ServerConfig]] — Complete field reference
- [Core contract](https://github.com/lbliii/pounce/blob/main/docs/design/core-contract.md) — Canonical ownership and proof rules
