# ADR: `/_pounce/info` — Auth and Bind Model

**Status**: Accepted
**Date**: 2026-04-20
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
2. **When enabled, the endpoint binds to loopback only by default.** A *separate* listener on `introspection_bind: str = "127.0.0.1"` and `introspection_port: int = <configured_port>` — the endpoint does **not** share the main application listener.
3. **Binding to anything non-loopback emits a startup `WARNING` log line** pointing at this ADR.
4. **No token auth is added.** If you need auth, put the endpoint behind your reverse proxy.

### Resulting config surface

```python
# ServerConfig additions (Sprint 4.1)
introspection_enabled: bool = False
introspection_bind: str = "127.0.0.1"     # loopback by default
introspection_port: int = 0                # 0 = same as main port on separate listener; user may override
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
curl http://127.0.0.1:8001/_pounce/info
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

A separate listener (not shared with the main application port) means:

- The endpoint does not appear on the user's public port even if they forget the loopback setting.
- The endpoint path (`/_pounce/info`) does not collide with user routes.
- Firewall rules can isolate it cleanly.

### Layer 3: startup warning on public bind

If a user knowingly sets `introspection_bind = "0.0.0.0"`, they accept the risk. But we should make sure they *know* they did it — config files get copied, templated, generated. A visible `WARNING` at startup with a link to the redaction ADR makes accidental public exposure impossible-to-miss.

Draft warning text:

```
WARNING: /_pounce/info is bound to 0.0.0.0 and reachable from any network
         interface. The endpoint exposes runtime state per docs/design/
         info-endpoint-redaction.md. Place it behind a reverse proxy or
         set introspection_bind="127.0.0.1" to restrict to local access.
```

### Layer 4: no token auth

Explicit non-features:

- No `introspection_token` or `introspection_auth_header`.
- No HTTP Basic.
- No rate limiting on the endpoint (the main `rate_limit_*` settings are enough if the endpoint shares the public listener, which it doesn't by default).

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
**Rejected.**

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
- **Data at risk:** even with the redaction allowlist, an attacker who reaches `/_pounce/info` learns pounce version, Python version, feature flags, timeout values, and live connection counts. This is operational info that aids fingerprinting but contains no credentials or user data.
- **Log hygiene:** access logs for the introspection listener are off by default — we don't need every `curl /_pounce/info` in `nginx`-style logs. `log_level=debug` turns them on.

## Implementation Notes for Sprint 4.1

- The introspection listener is a separate `HTTPServer` / accept loop from the application listener. Do not multiplex via routing on the main listener.
- When `introspection_enabled=False`, no listener is created and the config fields `introspection_bind`/`introspection_port`/`introspection_path` are ignored (test that setting them without `introspection_enabled=True` is a no-op, not an error — backward compat).
- When `introspection_bind` is a loopback literal (`127.0.0.1`, `::1`, `localhost`), no warning. For anything else, emit the warning.
- The warning goes through the standard pounce logger at `WARNING` level. It is a startup emission, not a per-request one.

## Consequences

### Positive

- Safe by default: a user who types `introspection_enabled = true` and nothing else gets a working, local-only endpoint with zero risk of public exposure.
- Deliberate escalation: going public is a conscious act that logs loudly.
- Minimal config surface: 4 new fields, all with sensible defaults.
- No auth system to maintain.

### Negative

- Users wanting remote access without a proxy must bind publicly and warn themselves. Acceptable — if you can't put a proxy in front, you can SSH-tunnel.
- Two listeners when enabled (main + introspection). Small extra resource cost; negligible compared to workers.

### Neutral

- `introspection_port: int = 0` semantics — we interpret 0 as "pick main port + 1" so users who just flip the flag get a sensible port without configuring. Unit test covers collision fallback.
