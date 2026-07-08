# Deployment Smoke Evidence Format

**Status:** Adopted 2026-07-08

**Applies to:** Pounce deployment recipes and live PaaS smoke runs

A deployment guide is not proof that its platform contract works. Record every
live smoke run in a dated Markdown file using the template below, including
failed and incomplete attempts. A `pass` is valid only when every required
check ran against the named deployment and its raw output is retained.

This format describes evidence; it does not make a platform, production-
readiness, zero-downtime, or performance claim by itself. Public wording must
still follow `docs/design/core-contract.md` and
`docs/design/public-claims.json`.

## Required metadata

- `Status`: `not-run`, `incomplete`, `failed`, or `passed`.
- UTC date/time and operator.
- Git commit, Pounce version, Python version, and GIL state.
- Platform plus project, service, environment, and deployment identifiers.
- Public URL and health/readiness path.
- worker mode and count, platform replica count, and shutdown timeout.
- edge TLS termination and observed origin protocol.
- proxy identity posture: trusted proxy ranges, forwarded-hop count, and which
  forwarded headers were observed. Record policy, never secret values.
- exact smoke command and a durable link or repository path to raw output.

## Required checks

Each row records `pass`, `fail`, or `not-run`, the observed result, and the raw
evidence location.

| Check | Required observation |
|---|---|
| Preflight | `pounce check` succeeds against the deployed configuration without exposing redacted values. |
| Runtime identity | The process reports the expected Python version, GIL state, Pounce version, worker mode, and worker count. |
| Deployment state | The platform reports the named deployment as healthy/active. |
| Readiness | The configured readiness endpoint returns `200` only after startup completes. |
| HTTP methods | `GET` and `HEAD` succeed at the edge with correct body semantics. |
| Edge topology | Record the client-facing protocol, origin-facing protocol, TLS terminator, and observed forwarded headers. |
| Sample traffic | A bounded request sample completes with response-code counts and zero unexplained transport errors. |
| In-flight drain | A slow request started before redeploy or SIGTERM completes according to the documented drain contract. |
| New traffic during drain | Record whether new connections are admitted, rejected explicitly, or disconnected; silent drops fail the check. |
| Shutdown bound | The old process exits within the configured Pounce and platform termination windows. |
| Replacement readiness | The replacement serves traffic only after readiness passes. |
| Logs | Startup, drain, shutdown, and any `POUNCE_*` diagnostics are retained with timestamps and deployment identity. |

Platform-specific recipes may add checks, but they may not delete these rows.
If a platform cannot expose an observation, record `not-run` with the exact
limitation and keep the overall status `incomplete`.

## Record template

```text
# <Platform> Deployment Smoke Record — <UTC date>

Status: not-run | incomplete | failed | passed
Operator:
Started at (UTC):
Finished at (UTC):
Git commit:
Pounce version:
Python version / GIL state:
Platform project / service / environment:
Deployment ID:
Public URL:
Readiness path:
Worker mode / workers / replicas:
Pounce shutdown timeout / platform termination window:
TLS termination / client protocol / origin protocol:
Trusted proxy and forwarded-header posture:
Smoke command:
Raw output path or durable URL:

Checks:
- Preflight:
- Runtime identity:
- Deployment state:
- Readiness:
- GET and HEAD:
- Edge topology:
- Sample traffic and response-code counts:
- In-flight drain:
- New traffic during drain:
- Shutdown bound:
- Replacement readiness:
- Logs and POUNCE_* diagnostics:

Failures, caveats, and follow-up issue links:
Result rationale:
```

## Redaction and retention

Never record environment-variable values, credentials, tokens, cookies,
private keys, DSNs, unredacted proxy allowlists, or private customer data.
Deployment identifiers, commands, timestamps, response summaries, and
redacted logs are evidence and should remain reviewable with the commit that
uses them. If raw output must live outside the repository, use a durable CI or
release artifact URL and record its retention period.

## Review rule

A reviewer can accept `passed` only when the metadata is complete, every
required check is `pass`, raw evidence is reachable, and failures are not
reclassified as caveats. Otherwise the record remains `failed` or
`incomplete`, and public docs must describe it that way.
