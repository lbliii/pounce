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

:::{/tab-item}

:::{tab-item} pip

```bash
pip install bengal-pounce
```

:::{/tab-item}

:::{tab-item} From Source

```bash
git clone https://github.com/lbliii/pounce.git
cd pounce
uv sync --group dev
```

:::{/tab-item}
:::{/tab-set}

## Optional Extras

Pounce ships with two required runtime dependencies: `h11` (HTTP/1.1 parsing) and `milo-cli` (the CLI). The request hot path depends only on `h11`. Additional protocols are optional extras:

| Extra | Provides | Install |
|-------|----------|---------|
| `h2` | HTTP/2 support (stream multiplexing, priority signals) | `pip install bengal-pounce[h2]` |
| `ws` | WebSocket support (HTTP/1 WebSocket; WS-over-H2 also requires `h2`) | `pip install bengal-pounce[ws]` |
| `tls` | TLS termination via truststore | `pip install bengal-pounce[tls]` |
| `h3` | HTTP/3 support (QUIC/UDP via bengal-zoomies) | `pip install bengal-pounce[h3]` |
| `full` | All protocol extras (h2 + ws + tls + h3) | `pip install bengal-pounce[full]` |

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

:::{related}
:limit: 3
:section_title: Next Steps
:::
