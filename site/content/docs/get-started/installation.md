---
title: Installation
description: Install Pounce and optional protocol extras
draft: false
weight: 10
lang: en
type: doc
tags: [installation, setup]
keywords: [install, pip, uv, extras, h2, websocket, tls]
category: onboarding
---

## Prerequisites

- **Python 3.14+** (free-threading build recommended for thread-based workers)

:::{note}
Pounce works on both GIL and free-threading builds. On GIL builds, multi-worker mode uses processes instead of threads — same API, same config.
:::

## Install

:::{tab-set}
:::{tab-item} uv
```bash
uv add bengal-pounce
```
:::

:::{tab-item} pip
```bash
pip install bengal-pounce
```
:::

:::{tab-item} From Source
```bash
git clone https://github.com/lbliii/pounce.git
cd pounce
uv sync --group dev
```
:::
:::{/tab-set}

## Optional Extras

Pounce ships with one dependency (`h11` for HTTP/1.1). Additional protocols are optional extras:

| Extra | Provides | Install |
|-------|----------|---------|
| `h2` | HTTP/2 support (stream multiplexing, priority signals) | `pip install bengal-pounce[h2]` |
| `ws` | WebSocket support (including WS over H2) | `pip install bengal-pounce[ws]` |
| `tls` | TLS termination via truststore | `pip install bengal-pounce[tls]` |
| `fast` | C-accelerated HTTP/1.1 via httptools | `pip install bengal-pounce[fast]` |
| `full` | All protocols (h2 + ws + tls) | `pip install bengal-pounce[full]` |

```bash
# Install with all protocol support
uv add "bengal-pounce[full]"
```

## Verify

```bash
pounce --help
```

You should see the CLI help output with available options. If you see a `pounce: command not found` error, ensure your Python scripts directory is on your `PATH`.

## Next Steps

- [[docs/get-started/quickstart|Quickstart]] — Serve your first app
- [[docs/configuration/cli|CLI Reference]] — All command-line options
