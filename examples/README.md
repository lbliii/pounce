# Pounce Examples

Example code and prototypes for pounce features.

## HTTP/3 Prototype

**Status:** Conceptual prototype for Phase 5c (Q2 2026)

**File:** `http3_prototype.py`

This prototype demonstrates how HTTP/3/QUIC support would be integrated into pounce using the [aioquic](https://github.com/aiortc/aioquic) library.

### Requirements

```bash
pip install aioquic
```

### Generate TLS Certificate

HTTP/3 (QUIC) requires TLS 1.3. Generate a self-signed certificate for testing:

```bash
openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes
```

### Run the Prototype

```bash
python examples/http3_prototype.py
```

### Test with Chrome

```bash
google-chrome --enable-quic --origin-to-force-quic-on=localhost:4433 https://localhost:4433
```

Or with curl (if built with HTTP/3 support):

```bash
curl --http3 https://localhost:4433
```

### What It Demonstrates

- UDP socket binding for QUIC
- ALPN negotiation for HTTP/3 (`h3`)
- HTTP/3 request/response handling
- ASGI scope creation for HTTP/3
- Integration with aioquic library

### Limitations

This is a **conceptual prototype**, not a production-ready implementation:

- No worker supervision
- No graceful shutdown
- No connection pooling
- No performance optimization
- No comprehensive error handling

The full implementation in Phase 5c will integrate HTTP/3 into the existing worker architecture with proper supervision, lifecycle management, and production-grade features.

### Architecture

```
┌─────────────────────┐
│  ASGI Application   │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  H3ServerProtocol   │  ← HTTP/3 request/response
│  (aioquic wrapper)  │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   QuicConnection    │  ← QUIC transport (aioquic)
│   (RFC 9000)        │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│    UDP Socket       │  ← Network I/O
└─────────────────────┘
```

### See Also

- [HTTP/3 Roadmap](../docs/design/http3-roadmap.md) — Full architectural design and implementation plan
- [aioquic documentation](https://aioquic.readthedocs.io/) — Library reference
- [RFC 9114: HTTP/3](https://www.rfc-editor.org/rfc/rfc9114.html) — Protocol specification
