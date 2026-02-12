# WebSocket Compression

Pounce supports **permessage-deflate** compression for WebSocket connections, reducing bandwidth usage by 60-80% for text messages with repetitive content (JSON, HTML, etc.).

## Configuration

WebSocket compression is **enabled by default**:

```python
from pounce import ServerConfig

config = ServerConfig(
    websocket_compression=True,  # Default: True
    websocket_max_message_size=10_485_760,  # 10 MB (default)
)
```

## How It Works

1. **Negotiation**: When a client requests `Sec-WebSocket-Extensions: permessage-deflate`, pounce automatically negotiates and responds with the same header in the 101 response.

2. **Transparent**: Compression and decompression are handled automatically by `wsproto`. Your ASGI app sends/receives uncompressed data—pounce handles the compression at the protocol layer.

3. **Per-Message**: Each WebSocket message is compressed independently, allowing for efficient streaming and minimal memory overhead.

## Benefits

- **Bandwidth Savings**: 60-80% reduction for text data (JSON, HTML, XML)
- **Lower Latency**: Smaller payloads = faster transmission over slow networks
- **Mobile-Friendly**: Reduces data usage for mobile clients
- **Zero App Changes**: Works transparently with existing ASGI WebSocket apps

## Example

```python
# Your ASGI app doesn't change—compression is automatic
async def websocket_app(scope, receive, send):
    if scope["type"] == "websocket":
        await send({"type": "websocket.accept"})

        # Send a large JSON payload—automatically compressed
        data = {"users": [...]}  # Large JSON object
        await send({"type": "websocket.send", "text": json.dumps(data)})

        await send({"type": "websocket.close"})
```

## Performance

Compression adds minimal CPU overhead (< 5% for typical workloads) while providing significant bandwidth savings:

| Message Type | Uncompressed | Compressed | Ratio |
|-------------|-------------|------------|-------|
| JSON (repetitive keys) | 10 KB | 2-3 KB | 70-80% |
| HTML fragments | 5 KB | 1-2 KB | 60-80% |
| Random data | 10 KB | ~10 KB | 0% |

## Disabling Compression

To disable compression (e.g., for already-compressed data like images or encrypted content):

```python
config = ServerConfig(websocket_compression=False)
```

## Requirements

WebSocket compression requires the `wsproto` library:

```bash
pip install pounce[ws]
```

## Compatibility

Permessage-deflate is widely supported:
- All modern browsers (Chrome, Firefox, Safari, Edge)
- Node.js `ws` library
- Python `websockets` library
- Go `gorilla/websocket`

## Spec Compliance

Implements [RFC 7692](https://datatracker.ietf.org/doc/html/rfc7692) (WebSocket Compression Extensions).
