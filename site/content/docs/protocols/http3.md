---
title: HTTP/3
description: QUIC/UDP HTTP serving through the optional bengal-zoomies transport
draft: false
weight: 23
lang: en
type: doc
tags: [protocols, http3, quic, tls]
keywords: [http3, quic, udp, qpack, zero-rtt, tls]
category: reference
---

HTTP/3 support is optional and uses the pure-Python `bengal-zoomies` QUIC stack.
It requires TLS certificate configuration because QUIC includes TLS 1.3 in the
transport handshake.

:::{note}
HTTP/3 has its own support boundary from HTTP/1.1 and HTTP/2 because it uses a
UDP listener and QUIC transport state. Check lifecycle, reload, 0-RTT, limit, and
benchmark notes before treating it as production-equivalent to the TCP paths.
WebSocket over HTTP/3 is not currently supported.
:::

## Install

```bash
pip install "bengal-pounce[h3]"
```

If `bengal-zoomies` is missing, the HTTP/3 path fails with an install hint:
`pip install bengal-pounce[h3]`. `pounce check` also reports the missing
HTTP/3 stack when `http3_enabled` is configured without the extra installed.

## Enable

```python
from pounce import ServerConfig

config = ServerConfig(
    host="0.0.0.0",
    port=8443,
    ssl_certfile="cert.pem",
    ssl_keyfile="key.pem",
    http3_enabled=True,
)
```

Or from the CLI:

```bash
pounce myapp:app --ssl-certfile cert.pem --ssl-keyfile key.pem --http3
```

## Tuning

| Field | Default | Description |
|-------|---------|-------------|
| `http3_enabled` | `False` | Enables the UDP/QUIC listener. |
| `http3_max_connections` | `10_000` | Maximum concurrent QUIC connections. |
| `http3_idle_timeout` | `30.0` | Idle timeout in seconds. |
| `http3_qpack_max_table_capacity` | `0` | QPACK dynamic table capacity. 0 uses static-table-only compression. |
| `http3_zero_rtt_enabled` | `False` | Allows TLS 0-RTT at the transport layer; unsafe methods receive `425 Too Early`. |

## Behavior

- Oversized request bodies are rejected with `413`.
- Malformed or contradictory pseudo-headers are rejected before ASGI scope construction.
- `Alt-Svc` is advertised from HTTP/2 responses when HTTP/3 is enabled.
- The built-in health endpoint and request ID handling match the HTTP/1.1 and HTTP/2 paths.
- Lifespan state is passed into H3 ASGI scopes.
- Reload/drain parity and benchmark artifacts remain explicit proof gates.
