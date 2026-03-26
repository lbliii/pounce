---
title: Compression
description: Zstd and gzip content-encoding with zero external dependencies
draft: false
weight: 20
lang: en
type: doc
tags: [compression, zstd, gzip, encoding]
keywords: [compression, zstd, gzip, content-encoding, pep784, accept-encoding]
category: how-to
---

## Overview

Pounce negotiates content-encoding automatically based on the client's `Accept-Encoding` header. Priority order:

1. **zstd** — Best compression ratio, lowest CPU cost (Python 3.14 stdlib, PEP 784)
2. **gzip** — Universal browser support (stdlib `zlib`)
3. **identity** — No compression (fallback)

All compression uses Python standard library modules — zero external dependencies.

## Configuration

Compression is enabled by default:

```bash
# Disabled (compression is enabled by default)
pounce myapp:app --no-compression
```

Programmatically:

```python
import pounce

pounce.run(
    "myapp:app",
    compression=True,
    compression_min_size=500,  # bytes
)
```

## Minimum Size

Responses smaller than `compression_min_size` (default: 500 bytes) are sent uncompressed. Compressing tiny responses adds CPU cost without meaningful size reduction.

## Zstd (PEP 784)

Python 3.14 includes `compression.zstd` in the standard library. Zstd provides:

- **Better ratios** than gzip at similar speeds
- **Lower CPU cost** than gzip at similar ratios
- **Streaming mode** — Pounce compresses response chunks as they're sent

Modern browsers (Chrome, Firefox, Safari) support `zstd` content-encoding.

## Skipped Responses

Compression is automatically skipped for:

- **SSE responses** — `text/event-stream` (streaming requires unbuffered delivery)
- **HEAD requests** — No response body to compress
- **Bodyless status codes** — 1xx, 204, 304 responses
- **Responses below `compression_min_size`** — Default: 500 bytes

::::{note}
WebSocket compression uses a separate system (permessage-deflate via the `websocket_compression` config option) and is not related to content-encoding compression.
::::

## See Also

- [[docs/about/performance|Performance]] — Streaming-first design
- [[docs/configuration/server-config|ServerConfig]] — Compression settings
