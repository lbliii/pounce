# ADR: `/_pounce/info` — Auth and Bind Model

**Status**: Accepted, amended to match shipped implementation
**Date**: 2026-04-20
**Amended**: 2026-05-09
**Epic**: [vibe-coding-epic.md](../plans/vibe-coding-epic.md) — Sprint 0.5
**Decider**: Sprint 0 design task

## Context

Sprint 4 adds `/_pounce/info`, an opt-in JSON endpoint exposing pounce's runtime state (redacted per [ADR: /info redaction](info-endpoint-redaction.md)). The question here is orthogonal to *what we expose*: **who gets to reach the endpoint?**

Constraints:

- The endpoint must be usable by agents/humans debugging a live server.
- It must not become a footgun that leaks operational state to the public internet by default.
- Pounce has no general auth stack. Adding one would be overreach — reverse proxies already solve auth at scale.
- Pounce users range from single-binary hobby deployments (no proxy) to large fleets behind L7 load balancers.

## Decision

Four layered defaults, each fail-closed:

1. **Introspection is disabled by default.** `introspection_enabled: bool = False` in `ServerConfig`. No endpoint is registered when off.
2. **When enabled, the endpoint is served by the main application listener at `introspection_path`.** The shipped implementation reuses the worker dispatch path next to health checks instead of opening a second listener.
3. **Public reachability emits a startup `WARNING` log line** pointing at the troubleshooting entry. Public reachability means either the main `host` or `introspection_bind` is non-loopback while introspection is enabled.
4. **No token auth is added.** If you need auth, put the endpoint behind your reverse proxy.

### Resulting config surface

```python
# ServerConfig additions
introspection_enabled: bool = False
introspection_bind: str = "127.0.0.1"     # warning policy input, not a listener
introspection_path: str = "/_pounce/info"
```

### Resulting user experience

```toml
# pounce.toml — minimal safe enable
[tool.pounce]
introspection_enabled = true
# (other fields default: loopback-only, path /_pounce/info)
```

```bash
# Agent on the same host
curl http://127.0.0.1:8000/_pounce/info
```

```toml
# pounce.toml — explicit public exposure (triggers warning)
[tool.pounce]
introspection_enabled = true
introspection_bind = "0.0.0.0"      # WARNING emitted at startup
```

## Why These Four Layers

### Layer 1: off by default

Feature flags for observability endpoints must default off. The cost of a leak is asymmetric: turning it on takes seconds for someone who wants it; leaking by default can hurt someone who didn't know it existed. Metrics (`metrics_enabled`) and health check (`health_check_path`) already follow this rule.

### Layer 2: loopback-only default

Most debugging use cases are local: the agent running `curl` is on the same host as pounce, or is port-forwarded in. Loopback is enough. For remote access users can either SSH-tunnel or explicitly bind publicly — both are deliberate actions.

The original ADR selected a separate listener, but the shipped implementation
uses the main worker dispatch path. The operational consequence is simpler
deployment: no second port, no firewall exception, and no startup race for a
side listener. The security consequence is that the endpoint can be reachable
on the public application port when enabled, so Pounce warns if either the main
application bind or `introspection_bind` is non-loopback.

`introspection_bind` remains in the config surface as the explicit policy input
for public exposure warnings, but it does not create a separate socket.

### Layer 3: startup warning on public bind

If a user knowingly enables introspection on a public bind, they accept the risk. But we should make sure they *know* they did it — config files get copied, templated, generated. A visible `WARNING` at startup with a troubleshooting code makes accidental public exposure hard to miss.

Draft warning text:

```
POUNCE_CONFIG_INTROSPECTION_PUBLIC: introspection endpoint enabled with
non-loopback bind. The endpoint exposes runtime state; keep it loopback-only,
disable introspection, or block the path at your reverse proxy.
```

### Layer 4: no token auth

Explicit non-features:

- No `introspection_token` or `introspection_auth_header`.
- No HTTP Basic.
- No endpoint-specific rate limiting. If the endpoint shares a public listener, use the main rate limiter and reverse-proxy controls.

Rationale:

1. **Every production pounce deployment already has a reverse proxy.** nginx, Caddy, envoy, ALB — all of them handle auth better than pounce ever will. The right place to authenticate `/_pounce/info` is where you authenticate everything else.
2. **Tokens leak.** In-config tokens end up in screenshots, tickets, `ps aux`. Loopback-only + proxy-layer auth avoids the token-hygiene problem entirely.
3. **Scope creep risk.** Once pounce ships one auth knob, users will ask for "also rate-limit, also allow-list IPs, also mTLS." That's a general auth system, which is not pounce's job.
4. **Loopback-only is stronger than most token checks.** A stolen token is exfiltratable; a loopback-only endpoint requires process-on-the-box access, at which point pounce has bigger problems.

## Alternatives Considered

### Bearer token in config

```toml
[tool.pounce]
introspection_enabled = true
introspection_token = "sk_pounce_..."
```

**Pros**: Works without a reverse proxy.
**Cons**: Tokens-in-config is a leak channel (commits, screenshots, templates). Encourages users to skip the proxy layer where they should be authenticating anyway.
**Rejected.**

### Share the main listener, gate by middleware

Emit on the same port; require `Authorization: Bearer <token>` to reach the `/_pounce/info` path.

**Pros**: One listener to manage.
**Cons**: Couples introspection to pounce's middleware chain (auth bugs become endpoint-exposure bugs); path collision with user routes becomes more likely.
**Partially accepted, without middleware auth.** The endpoint now shares the main listener, but it is dispatched before user routes and before user middleware. Token auth remains rejected.

### Unix socket only

Bind `/_pounce/info` to a Unix domain socket by default.

**Pros**: Strongest isolation — requires filesystem-level access.
**Cons**: Doesn't work on Windows (supported platform). Agents have to use `curl --unix-socket`, breaking naive `curl http://...` muscle memory.
**Rejected**, but stays as an optional alternative: users can set `introspection_bind` to a path starting with `unix:` as a future enhancement.

### Always-on (just accept the risk)

**Rejected outright.** Opt-in is the whole point.

## Security Considerations

- **Threat model in scope:** an attacker on a neighboring network process/container/VM, or accidentally public binding.
- **Out of scope:** a root-level attacker on the same host (already game over), and authenticated reverse-proxy bypass (user's responsibility).
- **Data at risk:** even with the redaction allowlist, an attacker who reaches `/_pounce/info` learns pounce version, Python build identity, an explicitly supplied `POUNCE_BUILD_ID`, feature flags, timeout values, and live connection counts. This is operational info that aids fingerprinting. Operators must never place credentials or user data in `POUNCE_BUILD_ID`.
- **Log hygiene:** the endpoint is served on the main listener, so access-log behavior follows the main `access_log` setting.

## Implementation Notes

- The endpoint is dispatched from `worker.py` next to the built-in health check before the request reaches the ASGI app.
- When `introspection_enabled=False`, no endpoint is registered and `introspection_bind`/`introspection_path` are ignored.
- When the main `host` and `introspection_bind` are loopback literals (`127.0.0.1`, `::1`, `localhost`), no warning. For anything else, emit the warning.
- The warning goes through the standard pounce logger at `WARNING` level. It is a startup emission, not a per-request one.

## Consequences

### Positive

- Off by default: users must opt in before the endpoint exists.
- Deliberate escalation: going public is a conscious act that logs loudly.
- Minimal config surface: 3 fields, all with sensible defaults.
- No auth system to maintain.

### Negative

- Users who enable introspection while binding the main app publicly expose the path unless a reverse proxy blocks or authenticates it. Pounce emits `POUNCE_CONFIG_INTROSPECTION_PUBLIC` for that configuration.
- `introspection_bind` is now a warning-policy field, not a socket bind. The name is historical and should not be expanded without a focused migration plan.

### Neutral

- `introspection_path` may collide with user routing. Built-in dispatch wins while introspection is enabled; users can move the path if needed.
