---
title: Migrate from Uvicorn
description: Switch from Uvicorn to Pounce with minimal changes
draft: false
weight: 10
lang: en
type: doc
tags: [migration, uvicorn, tutorial]
keywords: [migration, uvicorn, switch, replace, comparison]
category: how-to
---

## Overview

If you're running an ASGI app with Uvicorn today, switching to Pounce is straightforward. The CLI syntax and configuration concepts are similar.

## CLI Comparison

```bash
# Uvicorn
uvicorn myapp:app --host 0.0.0.0 --port 8000 --workers 4 --reload

# Pounce
pounce serve --app myapp:app --host 0.0.0.0 --port 8000 --workers 4 --reload
```

## Flag Mapping

| Uvicorn Flag | Pounce Equivalent | Notes |
|--------------|-------------------|-------|
| `--host` | `--host` | Same |
| `--port` | `--port` | Same |
| `--workers` | `--workers` | Pounce uses threads on 3.14t |
| `--reload` | `--reload` | Built-in, no watchfiles needed |
| `--log-level` | `--log-level` | Same levels |
| `--ssl-certfile` | `--ssl-certfile` | Same |
| `--ssl-keyfile` | `--ssl-keyfile` | Same |
| `--root-path` | `--root-path` | Same |
| `--no-access-log` | `--no-access-log` | Same |
| `--factory` | *(automatic)* | Use `myapp:create_app()` syntax |
| `--http h11` | *(default)* | h11 is the default backend |
| `--http httptools` | *(not needed)* | Built-in fast parser handles this |
| `--ws websockets` | `bengal-pounce[ws]` | Uses wsproto instead |
| `--h11-max-incomplete-event-size` | `ServerConfig(h11_max_incomplete_event_size=...)` | Programmatic only (no CLI flag) |

## Programmatic Comparison

```python
# Uvicorn
import uvicorn
uvicorn.run("myapp:app", host="0.0.0.0", port=8000, workers=4)

# Pounce
import pounce
pounce.run("myapp:app", host="0.0.0.0", port=8000, workers=4)
```

## Key Differences

:::{dropdown} Worker model
:icon: cpu

Uvicorn uses processes for parallelism (fork). Pounce uses threads on Python 3.14t. This means:

- **Lower memory** — One copy of the app instead of N
- **Shared state** — Frozen config is shared, not duplicated
- **No fork** — No copy-on-write, no IPC overhead
:::

:::{dropdown} Compression
:icon: archive

Pounce includes built-in compression (zstd + gzip). Uvicorn does not — you'd typically handle this in middleware or a reverse proxy.
:::

:::{dropdown} Server-Timing
:icon: clock

Pounce can inject `Server-Timing` headers automatically (`--server-timing`). Uvicorn has no equivalent.
:::

:::{dropdown} HTTP/2
:icon: globe

Pounce supports HTTP/2 via `bengal-pounce[h2]`. Uvicorn does not support HTTP/2.
:::

## Migration Steps

:::{steps}
:::{step} Install Pounce

```bash
uv add bengal-pounce
```

:::{/step}
:::{step} Replace the run command

```bash
# Before
uvicorn myapp:app --host 0.0.0.0 --workers 4

# After
pounce serve --app myapp:app --host 0.0.0.0 --workers 4
```

:::{/step}
:::{step} Update Dockerfile / systemd

Update deployment configs if applicable.

:::{/step}
:::{step} Test your application

Pounce serves standard ASGI, so any compliant app works without code changes.

:::{/step}
:::{/steps}

## Verify Migration

:::{checklist} Migration Verification
:show-progress:
- [ ] Pounce installed (`pounce --help` works)
- [ ] Run command replaced (uvicorn → pounce)
- [ ] Dockerfile / systemd updated if applicable
- [ ] Application tested and serving correctly
:::{/checklist}

## See Also

- [[docs/get-started/quickstart|Quickstart]] — Getting started with Pounce
- [[docs/about/comparison|When to Use Pounce]] — Architecture and deployment
