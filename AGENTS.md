# AGENTS.md

Pounce sits on the request path for every app that runs on it. Bugs you introduce here reach the end users of those apps — people who can't see Pounce, can't audit it, and can't defend themselves from what it does. Treat the rules below as safety rules, not style rules.

---

## North star

**Make free-threaded Python worth deploying.** Pounce exists to give 3.14t a production-grade ASGI server. Every decision routes back to that: performance you can measure, compatibility you can trust, correctness under true parallelism. If a change doesn't serve that goal, it isn't worth shipping.

---

## Design philosophy

- **Pure Python is a constraint.** No C extensions. If the fast path needs to get faster, the answer is better Python — not Cython, not a native dep. "Faster than h11 in pure Python" dies the moment we compile something.
- **Frozen config > locks.** `ServerConfig` is immutable and shared. Runtime changes are lifecycle events, not mutations.
- **Sans-I/O protocols.** Parsers and state machines don't touch sockets. Load-bearing for testability.
- **Sharp edges are bugs.** Silent `except`, `type: ignore`, ambiguous flags, unhelpful errors — not taste, bugs. CI catches some (S110); the rest is on you.
- **The sync worker is sync for a reason.** The fast H1 parser lives where latency lives. Don't async-ify it without benchmarks.

---

## Stakes

When you change something in Pounce, the blast radius is:

- **Protocol bugs** (H1/H2/WS/H3) → wire-level corruption, silent data loss, request smuggling. Debuggable only with packet captures. Harm: an end user's session gets crossed with someone else's.
- **Worker lifecycle bugs** → dropped requests mid-reload, zombies, workers that don't drain. Harm: someone's checkout or upload vanishes, and the app dev has to explain why.
- **Free-threaded races** → no GIL safety net. Pounce is the ecosystem's canary for 3.14t — a race we ship normalizes "free-threading is flaky" for everyone.
- **Performance regressions** → the README numbers are load-bearing. A 20% regression in the sync worker makes the project pointless. CI doesn't catch this — you do.

Pounce is beta but shipped. Calibrate accordingly.

---

## Who reads your output

- **Ops** — tired, on-call. They read error messages, config docs, logs.
- **App devs migrating from uvicorn** — want to be done in five minutes. They read tracebacks and `--help`.
- **Contributors** — know ASGI, not our internals. They read protocol code and worker lifecycle.
- **Me (Lawrence)** — read diffs. Put the what in code, the why in the PR.

---

## Escape hatches — stop and ask

Forks where I want a check-in, not a judgment call:

- **New runtime dependency.** "It already does what we need" is the default. Ask.
- **Touching the sync worker hot path** (`_fast_h1.py` and adjacent). Show before/after benchmarks. Can't measure → don't change.
- **Worker-model behavior** (GIL detection, thread vs process, lifecycle state machine). Sketch the change and ask before implementing.
- **Public API change** (`pounce.run`, CLI, `ServerConfig` field names/types). Ask whether the break is worth it.
- **New config option.** Reshape an existing one first. The surface is already 50+ and growing is a smell.
- **Dead code you found.** Flag in the PR, let me decide — it might be load-bearing for a transport or example.
- **Test disagrees with code.** Ask which is authoritative before "fixing" either.
- **Can't reproduce a reported bug.** Stop. Ask for a minimal repro or env dump. Don't guess.
- **Adjacent issues found mid-task.** List in the PR description. Don't fold them in — exception: refactors, where I prefer one bundled PR.

---

## Anti-patterns

Things that look reasonable and are wrong here:

- **C extensions "just for the hot path."** No. The whole point is pure Python.
- **`try: ... except Exception: pass`.** S110 is re-enabled in CI for a reason. If you must swallow, log what and why in one line.
- **`# type: ignore`.** Target is zero. Narrow the type or fix the code. If you have to, own it in the PR.
- **Speculative config options** for "future flexibility." If no one's asking for it, don't add it. Configs are easier to add than to remove.
- **Defensive validation inside internal code.** Validate at the boundary; internal code trusts its callers.
- **Abstractions for hypothetical protocols.** H3 is real; H4 is not.
- **Refactoring during a bug fix.** Separate PR. Exception: the refactor *is* the fix.

---

## Done criteria

A change is done when all of these hold:

- [ ] `make lint` and `make ty` clean. No new `type: ignore` or S110 suppressions.
- [ ] Tests exercise the *interesting* path: both values for a config flag, the failure path for lifecycle changes, malformed input for protocols.
- [ ] Hot-path changes include a benchmark in the PR. "Didn't benchmark" is OK only if you say why.
- [ ] GIL-sensitive? Note what you thought about on 3.14t — shared-mutable state first.
- [ ] Public API changed → CHANGELOG entry, migration note if breaking.
- [ ] Error messages tell the reader what to do next, not just what went wrong.
- [ ] PR description explains *why*. The diff explains what.

"Tests pass" is not "done." Tests pass on broken code all the time.

---

## Review and assimilation

- **I read diff-first, description-second.** Tight diff + clear why merges fast; sprawling diff gets questions.
- **One concern per PR.** If the diff needs section headers, it's two PRs. Exception: refactors renaming a concept across many files — one bundled PR beats review churn.
- **Commit style:** see `git log`. `fix:`/`refactor:`/`deps:`/`release:` prefixes, imperative, body = motivation.
- **Don't trailing-summary me.** If the diff is readable, I can read it.
- **Flag surprises.** Weird test, unused config, unreachable code path — put it in the PR description. Don't fix silently, don't ignore.

---

## When this file is wrong

It will be. Tell me. The worst outcome is that it sits here for a year contradicting how the project actually works. Updates to AGENTS.md are a first-class PR — short, focused, and welcome.

---

## See also

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — setup, feedback loops, and recipes (add a test, add a config field, add an error).
- **[docs/troubleshooting.md](docs/troubleshooting.md)** — error-code catalog, indexed by `POUNCE_*` code.
- **[docs/design/](docs/design/)** — ADRs for the load-bearing decisions (error codes, redaction allowlist, introspection auth, init scope).
