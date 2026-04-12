---
title: WebSocket Compression
description: Permessage-deflate compression for WebSocket connections
weight: 6
---

# WebSocket Compression

Pounce supports **permessage-deflate** compression ([RFC 7692](https://datatracker.ietf.org/doc/html/rfc7692)) for WebSocket connections, reducing bandwidth by 60-80% for text messages.

## Configuration

Enabled by default. Requires `wsproto`:

```bash
pip install bengal-pounce[ws]
```

```python
from pounce import ServerConfig

config = ServerConfig(
    websocket_compression=True,   # default
    websocket_max_message_size=10_485_760,  # 10 MB default
)
```

To disable (e.g., for already-compressed data):

```python
config = ServerConfig(websocket_compression=False)
```

## How It Works

1. Client sends `Sec-WebSocket-Extensions: permessage-deflate` during handshake
2. Pounce negotiates and includes the extension in the 101 response
3. Compression/decompression is handled transparently at the protocol layer

Your ASGI app sends and receives uncompressed data -- no code changes needed.

## Performance

| Message Type | Uncompressed | Compressed | Savings |
|---|---|---|---|
| JSON (repetitive keys) | 10 KB | 2-3 KB | 70-80% |
| HTML fragments | 5 KB | 1-2 KB | 60-80% |
| Random/binary data | 10 KB | ~10 KB | ~0% |

CPU overhead is < 5% for typical workloads. All modern browsers support permessage-deflate.
