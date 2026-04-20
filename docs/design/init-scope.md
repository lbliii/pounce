# ADR: `pounce init` Scope

**Status**: Accepted
**Date**: 2026-04-20
**Epic**: [vibe-coding-epic.md](../plans/vibe-coding-epic.md) — Sprint 0.4
**Decider**: Sprint 0 design task

## Context

Sprint 3 adds `pounce init` — a scaffolding command that drops a minimal working project in the current directory. The question for Sprint 0: **should `init` be framework-aware?**

Options:

- **A. Vanilla ASGI only.** One flavor: an `async def app(scope, receive, send)` that returns "hello from pounce."
- **B. Framework-aware.** `pounce init --framework fastapi|starlette|django` generates framework-specific scaffolds.
- **C. Interactive wizard.** Prompt the user for framework + feature choices.

## Decision

**Vanilla-only in Sprint 3.** Defer framework flavors to a separate epic if and only if demand is demonstrated.

### What `pounce init` produces

```
app.py         # async def app(scope, receive, send)
pounce.toml    # commented template of every ServerConfig default
.gitignore     # __pycache__/, .pounce/
```

### What `pounce init` does NOT produce

- No `pyproject.toml` (the user's project may already have one; we don't guess)
- No virtualenv, no dependency install (that's `uv`'s job, not `pounce init`'s)
- No framework code
- No README, no license, no CI config

### Safety rails

- Refuse if any of the three files exist. Require `--force` to overwrite.
- On refusal, list which files would collide, with an actionable suggestion: *"pass `--force` to overwrite, or move the existing files first."*

## Why Vanilla-Only

### Pounce's job is to serve ASGI, not to compete with framework scaffolds

Each major framework already has first-class scaffolding:

- FastAPI: `fastapi dev` creates skeletons, and its docs lead with a Starlette-style inline example.
- Starlette: `starlette` examples are themselves the scaffold.
- Django: `django-admin startproject` is a well-loved multi-decade-old scaffold with per-app structure, settings module, etc.
- Litestar: `litestar init` exists.

If `pounce init --framework django` produced something, it would be a third-party opinion on Django project layout — inferior to `django-admin startproject` and a maintenance liability for pounce.

### The value of `pounce init` is "prove pounce works in this directory," not "scaffold a web app"

An agent or human running `pounce init && pounce serve --app app:app` in a fresh dir has proven:

1. `pounce` is installed and callable.
2. `pounce.toml` parses.
3. The ASGI contract is intact end-to-end.
4. The user can now copy their *real* app.py over and it will serve.

That is enough. Opinionated framework scaffolds would dilute this value by adding framework-specific dependencies, idioms, and version pins that pounce has no business shipping.

### Maintenance cost of framework awareness is high and asymmetric

| Dimension                     | Vanilla-only           | Framework-aware                                       |
|-------------------------------|------------------------|-------------------------------------------------------|
| Frameworks to track           | 0                      | ≥4 (FastAPI, Starlette, Django, Litestar)             |
| Version-pin updates per year  | 0                      | ~8–16 (each framework ~2–4 minor releases/year)       |
| Breaking-change surface       | 0                      | Every framework's scaffold conventions can change     |
| Test matrix expansion         | 1 scaffold             | N scaffolds × M framework versions                    |
| User pain if we lag a version | None                   | "Your scaffold is outdated" issues                    |

We already run a 48-integration-test matrix against the four frameworks. Adding scaffolds would double that surface for no gain proportional to the effort.

### "Init" typically scales down, not up

Best-in-class `init` commands (`npm init`, `cargo init`, `uv init`, `gh repo create`) all do *one opinionated thing* and leave the rest to the ecosystem. They don't try to scaffold app structure — they scaffold the package wrapper. `pounce init` follows the same pattern: scaffold the pounce wrapper (`pounce.toml` + a stub app), nothing else.

## Alternatives Considered

### B. Framework-aware from day one

**Pros**: Better "zero to FastAPI+pounce running" experience.
**Cons**: See above — maintenance asymmetry, duplicates framework scaffolds, opinion we don't need to own.
**Rejected.**

### C. Interactive wizard (`--interactive` flag)

**Pros**: Explicit choice, no surprises.
**Cons**: Wizards slow agents down (they can't answer prompts non-interactively without `--yes` machinery), add UI code, and produce the same scaffold choices we'd rather not own.
**Rejected.** If demand ever emerges, a `--framework` flag is simpler than interactive mode.

### Minimal variant: no `.gitignore`

**Pros**: Maximally conservative.
**Cons**: Every user needs it, shipping it costs nothing, forgetting to `.gitignore __pycache__` is a universal Python beginner mistake.
**Kept.** Include `.gitignore`.

### Expansion option: `pounce init --example=websocket-chat`

Pick from the 21 existing `examples/` as a scaffold source.

**Deferred.** Interesting, non-blocking. Revisit after Sprint 3 ships if agents ask for it.

## Generated File Specs

### `app.py`

```python
"""Minimal ASGI app. Replace me with your real app.

Run:
    pounce serve --app app:app
"""

async def app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain; charset=utf-8")],
    })
    await send({
        "type": "http.response.body",
        "body": b"hello from pounce\n",
    })
```

Deliberate choices:

- Pure ASGI, no framework. Runs against every version of pounce.
- Comment at top says "Replace me." Explicit that this is scaffolding.
- Single-file app — no package structure forced on the user.

### `pounce.toml`

Generated from the Sprint 2.1 `pounce config schema --format toml-template` output. Every field commented out with its default value and a one-line comment. User uncomments what they need.

### `.gitignore`

```
__pycache__/
*.pyc
.pounce/
```

Three lines. No license or branding. User can extend.

## Consequences

### Positive

- Zero ongoing maintenance burden from framework churn.
- Agent workflow: `pounce init && pounce serve` proves pounce works in two commands.
- Clean upgrade path: if a user later adopts FastAPI, they just replace `app.py`.

### Negative

- No "batteries-included FastAPI start" out of `pounce init`. Acceptable tradeoff — they can install FastAPI themselves and point `pounce.toml` at it.

### Neutral

- If user demand proves strong, a follow-up `pounce init --framework=X` is easy to add on top of the vanilla baseline (it's additive).

## References

- `npm init`, `cargo init`, `uv init` — prior art for minimal scaffolds.
- `django-admin startproject` — counter-example: framework-owned scaffold, appropriately scoped to the framework.
- `examples/` — pounce already has 21 examples; that directory is the right home for "bigger" starting points, not `init`.
