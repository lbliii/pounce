# HTTP/3 Support Roadmap and Design

**Status:** Implemented — zoomies backend (March 2026)
**Date:** February 2026 (updated March 2026)
**Author:** Phase 5b Implementation Team

**Implementation note:** HTTP/3 is implemented using [zoomies](https://github.com/lbliii/zoomies), a free-threading-native sans-I/O QUIC/HTTP/3 library. aioquic was replaced because it uses Limited API C extensions incompatible with Python 3.14 free-threaded.

**Current-state note:** Sections below preserve the February 2026 aioquic
research and original deferral recommendation. They are historical context, not
the active implementation plan or a Pounce production-readiness claim. Current
HTTP/3 work should follow [core-contract.md](core-contract.md),
[protocol-proof-ledger.json](protocol-proof-ledger.json), and
[../plans/ironclad-bengal-chirp.md](../plans/ironclad-bengal-chirp.md). The
remaining HTTP/3 gates are reload/drain behavior, transport documentation,
representative workload proof, and reproducible benchmarks before broad
production claims.

## Executive Summary

This document originally evaluated HTTP/3/QUIC support for pounce and provided
an architectural design and implementation roadmap. The original recommendation
was to defer full HTTP/3 implementation to Phase 5c while laying groundwork in
Phase 5b. That recommendation is now historical because HTTP/3 has been
implemented with zoomies.

**Key Findings:**

- Historical aioquic research found a mature standards-compliant library, but
  Pounce no longer uses aioquic and this is not a Pounce support guarantee.
- ✅ **Browser support** is universal (Chrome, Firefox, Safari, Edge all support HTTP/3 by default)
- ✅ **Performance gains** are significant on mobile/high-latency networks (12-52% improvement)
- ⚠️ **Architectural complexity** is high (UDP vs TCP, different worker model, ALPN negotiation)
- ⚠️ **Adoption priority** is lower than completing representative
  Bengal/Chirp proof and reload/drain gates
- ✅ **Current integration path** uses zoomies, not aioquic

**Historical recommendation:** Complete Phase 5b first, then implement HTTP/3 in
Phase 5c with `protocols/h3.py` and UDP worker support. That sequence is closed:
HTTP/3 now exists through zoomies. Current work should use the current-state
note above instead.

## Technical Background

### What is HTTP/3?

**HTTP/3** is the third major version of HTTP, standardized in RFC 9114 (June 2022). Unlike HTTP/1.1 and HTTP/2 (which run over TCP), HTTP/3 runs over **QUIC** (RFC 9000), a UDP-based transport protocol.

**Key differences:**

| Feature | HTTP/2 (TCP) | HTTP/3 (QUIC/UDP) |
|---------|-------------|-------------------|
| **Transport** | TCP | UDP |
| **Handshake** | TCP + TLS (2 RTT) | QUIC (1 RTT, 0-RTT on resume) |
| **Head-of-line blocking** | Yes (TCP level) | No (stream independence) |
| **Connection migration** | No | Yes (IP/port changes) |
| **Multiplexing** | Streams over 1 TCP conn | Streams over 1 QUIC conn |
| **Loss recovery** | TCP retransmission | QUIC packet-level ACK |

### Why HTTP/3 Matters

**Performance benefits:**
- **12-52% faster** on mobile/unstable networks ([Cloudflare benchmarks](https://blog.cloudflare.com/http-3-vs-http-2/))
- **45% faster connection establishment** (50ms RTT test, [DebugBear](https://www.debugbear.com/blog/http3-vs-http2-performance))
- **30% latency reduction** on mobile (2025 Akamai report, [The New Stack](https://thenewstack.io/http-3-in-the-wild-why-it-beats-http-2-where-it-matters-most/))
- **88% improvement** under high packet loss ([Performance Comparison](https://arxiv.org/html/2409.16267v2))

**Real-world adoption:**
- **Google:** Serving HTTP/3 since 2020 across all services
- **Meta:** Facebook and Instagram use HTTP/3
- **Cloudflare:** Enabled on entire global network
- **25%+ of web traffic** now uses HTTP/3 (rising rapidly)

### QUIC Protocol Overview

**QUIC** (Quick UDP Internet Connections) is a transport-layer protocol developed by Google, now standardized in RFC 9000/9001.

**Architecture:**
```
┌─────────────────┐
│   HTTP/3 (H3)   │  Application layer
├─────────────────┤
│   QUIC (RFC     │  Transport layer (replaces TCP + TLS)
│   9000/9001)    │  - Connection management
│                 │  - Stream multiplexing
│                 │  - Encryption (TLS 1.3 integrated)
│                 │  - Congestion control
│                 │  - Loss recovery
├─────────────────┤
│      UDP        │  Network layer
└─────────────────┘
```

**Key features:**
- **TLS 1.3 built-in** — encryption is mandatory, not optional
- **0-RTT resumption** — resume previous connections with zero round trips
- **Connection migration** — survive IP/port changes (mobile network switches)
- **Independent streams** — no head-of-line blocking across streams
- **Flexible congestion control** — pluggable algorithms (NewReno, BBR, Cubic)

## Current State Analysis

### aioquic Library

**[aioquic](https://github.com/aiortc/aioquic)** is the leading pure-Python QUIC and HTTP/3 implementation.

**Status (2026):**
- ✅ **Version:** 1.3.0 (latest on PyPI, actively maintained)
- ✅ **Standards compliance:** RFC 9000 (QUIC v1), RFC 9369 (QUIC v2), RFC 9114 (HTTP/3)
- ✅ **Testing:** Regularly tested for interoperability against other QUIC implementations
- ✅ **Production users:** Hypercorn, dnspython, mitmproxy, Web Platform Tests
- ✅ **License:** BSD 3-Clause (permissive, compatible with pounce's MIT license)

**Architecture:**
- **Sans-I/O design** ("bring your own I/O") — separates protocol logic from I/O
- **asyncio integration** — provides `QuicConnectionProtocol` for asyncio event loops
- **Minimal dependencies** — pure Python, no native extensions (portable)
- **TLS 1.3 implementation** — built-in cryptography (via cryptography library)

**API example:**
```python
from aioquic.asyncio import QuicConnectionProtocol, serve
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import HeadersReceived, DataReceived

class H3Protocol(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._http = H3Connection(self._quic)

    def quic_event_received(self, event):
        # Process QUIC events
        for h3_event in self._http.handle_event(event):
            if isinstance(h3_event, HeadersReceived):
                # Handle HTTP/3 request
                pass
```

### Browser Compatibility

**Universal support** across all modern browsers (as of 2026):

| Browser | Support | Version |
|---------|---------|---------|
| **Chrome** | ✅ Full | 87+ (April 2020) |
| **Firefox** | ✅ Full | 88+ (May 2021) |
| **Safari** | ✅ Full | 16.4+ (March 2023) |
| **Edge** | ✅ Full | 87+ (April 2020) |
| **Opera** | ✅ Full | 73+ |

**ALPN negotiation:**
- Browsers send `Alt-Svc: h3=":443"; ma=2592000` header
- Client requests `h3` via ALPN during TLS handshake
- Server accepts `h3`, falls back to `h2` or `http/1.1` if not supported

**Browser compatibility sources:**
- [Can I Use: HTTP/3](https://caniuse.com/http3)
- [Browser support for HTTP/3 QUIC](https://mybyways.com/blog/browser-support-for-http3-quic)

### Existing ASGI Servers

**Hypercorn** is currently the **only** ASGI server with HTTP/3 support:

```bash
pip install hypercorn[h3]  # Installs aioquic dependency
hypercorn --quic-bind 0.0.0.0:443 myapp:app
```

**Implementation notes:**
- Uses aioquic for QUIC/HTTP/3
- Separate UDP socket for QUIC (in addition to TCP sockets for H1/H2)
- ALPN negotiation to select protocol
- Certificate required (TLS 1.3 mandatory for QUIC)

**Competitive gap:**
- Uvicorn: ❌ No HTTP/3 support
- Gunicorn: ❌ No HTTP/3 support
- Hypercorn: ✅ HTTP/3 supported
- **pounce:** ⏳ Planned for Phase 5c

## Architectural Design

### High-Level Architecture

HTTP/3 requires a **fundamentally different worker model** because QUIC uses **UDP** instead of TCP:

```
┌─────────────────────────────────────────────────────────────┐
│                       Supervisor                             │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐            │
│  │  Worker 1  │  │  Worker 2  │  │  Worker N  │            │
│  │            │  │            │  │            │            │
│  │ TCP Socket │  │ TCP Socket │  │ TCP Socket │  (HTTP/1.1,│
│  │ (H1/H2)    │  │ (H1/H2)    │  │ (H1/H2)    │   HTTP/2)  │
│  └────────────┘  └────────────┘  └────────────┘            │
│                                                              │
│  ┌────────────┐  ┌────────────┐  ┌────────────┐            │
│  │  Worker 1  │  │  Worker 2  │  │  Worker N  │            │
│  │            │  │            │  │            │            │
│  │ UDP Socket │  │ UDP Socket │  │ UDP Socket │  (HTTP/3)  │
│  │ (H3)       │  │ (H3)       │  │ (H3)       │            │
│  └────────────┘  └────────────┘  └────────────┘            │
└─────────────────────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
   TCP Port 443         UDP Port 443  (Alt-Svc advertised)
   (HTTP/1.1, HTTP/2)   (HTTP/3)
```

### Key Architectural Questions

#### 1. Can we use the same Worker.run() loop?

**Answer: No** — UDP requires a different approach than TCP's `accept()` model.

**TCP (current):**
```python
async def _serve(self):
    server = await asyncio.start_server(self._handle_connection, sock=self._sock)
    await self._async_shutdown.wait()
```

**UDP (HTTP/3):**
```python
async def _serve_h3(self):
    # Create QUIC server
    protocol = await create_datagram_endpoint(
        lambda: H3ServerProtocol(self._app, self._config),
        sock=self._udp_sock,
    )
    await self._async_shutdown.wait()
```

**Recommendation:** Create separate `H3Worker` class that inherits from `Worker` but overrides `_serve()` and `_handle_connection()`.

#### 2. How does SO_REUSEPORT work with UDP?

**Answer: Same as TCP** — multiple workers can bind to the same UDP port with `SO_REUSEPORT`.

**Behavior:**
- Kernel distributes incoming UDP packets across workers
- Hash-based distribution (source IP/port → worker)
- Connections stick to same worker (QUIC connection IDs)

**Code (similar to TCP binding):**
```python
udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
udp_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
udp_sock.bind((host, port))
```

#### 3. What's the overhead of aioquic vs native QUIC?

**Answer: Acceptable for most workloads** — pure Python adds ~20-30% overhead vs native implementations (like quiche/lsquic), but benefits from portability and ease of integration.

**Performance comparison:**
- **Native (quiche, C):** ~100k req/s per core
- **aioquic (Python):** ~70k req/s per core
- **Overhead:** ~30%, but still far exceeds typical web app needs

**Mitigation:**
- Free-threading (Python 3.13t) will improve aioquic performance
- CPU-bound work should be in application code, not server
- For ultra-high-throughput (>50k req/s), native alternatives exist

#### 4. Is H3 priority scheme different from H2?

**Answer: Yes** — HTTP/3 uses **RFC 9218 extensible priorities** instead of HTTP/2's dependency tree.

**HTTP/2 priorities:**
- Stream dependencies (tree structure)
- Weights (1-256)
- Complex to implement correctly

**HTTP/3 priorities:**
- **Urgency:** 0-7 (0 = highest, 7 = lowest)
- **Incremental:** boolean (incremental rendering)
- Simpler, more flexible

**Example:**
```
:authority: example.com
:method: GET
:path: /
priority: u=0, i  # Urgency 0, incremental
```

**Implementation:** aioquic handles priority parsing; pounce can expose via ASGI scope.

### Proposed Architecture

#### File Structure

```
src/pounce/
├── protocols/
│   ├── h1.py          # Existing HTTP/1.1
│   ├── h2_protocol.py # Existing HTTP/2
│   └── h3.py          # NEW: HTTP/3 protocol (Phase 5c)
├── worker.py          # Current TCP worker
├── h3_worker.py       # NEW: UDP/QUIC worker (Phase 5c)
├── server.py          # Server orchestration
└── supervisor.py      # Worker supervision
```

#### Integration Points

**1. Server startup:**
```python
# server.py
def run(self):
    # Existing TCP socket for H1/H2
    tcp_sock = self._bind_tcp_socket(self._config.host, self._config.port)

    # NEW: UDP socket for H3 (if enabled)
    if self._config.http3_enabled:
        udp_sock = self._bind_udp_socket(self._config.host, self._config.port)
        # Spawn H3 workers
        self._supervisor.spawn_h3_workers(udp_sock)

    # Spawn TCP workers (existing)
    self._supervisor.spawn_workers(tcp_sock)
```

**2. ALPN negotiation (TLS):**
```python
# TLS context (already exists, needs update)
ssl_context.set_alpn_protocols(["h3", "h2", "http/1.1"])
#                                ^^^^ NEW: advertise HTTP/3
```

**3. Alt-Svc header:**
```python
# Advertise HTTP/3 availability in HTTP/1.1 and HTTP/2 responses
response_headers.append((b"alt-svc", b'h3=":443"; ma=2592000'))
```

#### H3Worker Class

```python
class H3Worker:
    """Worker for HTTP/3 (QUIC/UDP) connections.

    Similar to Worker but uses datagram protocol instead of stream protocol.
    Handles QUIC connection management and HTTP/3 request/response cycles.
    """

    async def _serve(self):
        """Start QUIC server and handle connections."""
        # Create QUIC connection protocol
        transport, protocol = await self._loop.create_datagram_endpoint(
            lambda: H3ServerProtocol(
                app=self._app,
                config=self._config,
                worker_id=self._worker_id,
                lifecycle=self._lifecycle,
            ),
            sock=self._sock,  # UDP socket
        )

        await self._async_shutdown.wait()
        transport.close()
```

#### H3 Protocol Handler

```python
from aioquic.asyncio import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import HeadersReceived, DataReceived

class H3ServerProtocol(QuicConnectionProtocol):
    """HTTP/3 protocol implementation using aioquic."""

    def __init__(self, app, config, worker_id, lifecycle):
        super().__init__()
        self._app = app
        self._config = config
        self._http = H3Connection(self._quic)
        self._streams = {}  # stream_id -> ASGIScope

    def quic_event_received(self, event):
        """Handle QUIC events and translate to HTTP/3."""
        for h3_event in self._http.handle_event(event):
            if isinstance(h3_event, HeadersReceived):
                self._handle_request(h3_event)
            elif isinstance(h3_event, DataReceived):
                self._handle_data(h3_event)

    async def _handle_request(self, event):
        """Convert HTTP/3 request to ASGI scope and call app."""
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "3",
            "method": ...,
            "path": ...,
            "headers": ...,
            # ... (similar to H2 scope creation)
        }

        await self._app(scope, receive, send)
```

### Configuration

```python
@dataclass(frozen=True, slots=True)
class ServerConfig:
    # ... existing fields ...

    # HTTP/3 configuration (Phase 5c)
    http3_enabled: bool = False  # Enable HTTP/3/QUIC support
    http3_max_connections: int = 10_000  # Max concurrent QUIC connections
    http3_idle_timeout: float = 30.0  # QUIC idle timeout (seconds)
    http3_max_stream_data: int = 1_048_576  # 1 MB per stream
```

## Implementation Challenges

### 1. UDP Socket Management

**Challenge:** UDP is connectionless — no `accept()` loop.

**Solution:** Use `asyncio.create_datagram_endpoint()` with `QuicConnectionProtocol`:
```python
transport, protocol = await loop.create_datagram_endpoint(
    lambda: H3ServerProtocol(...),
    sock=udp_sock,
)
```

**Worker distribution:** Kernel distributes UDP packets via `SO_REUSEPORT` (same as TCP).

### 2. QUIC Connection State

**Challenge:** QUIC maintains per-connection state (unlike stateless UDP).

**Solution:** aioquic's `QuicConnection` manages state:
- Connection IDs (CID) for routing
- Packet number spaces
- Stream states
- Congestion control state

**Memory overhead:** ~10-20 KB per QUIC connection (vs ~2 KB for TCP).

### 3. TLS 1.3 Requirement

**Challenge:** QUIC requires TLS 1.3 (TLS 1.2 not supported).

**Status:** ✅ Pounce already supports TLS 1.3 (via `ssl_certfile`/`ssl_keyfile`).

**Additional requirements:**
- Certificate must have valid SAN (Subject Alternative Name)
- Private key must be accessible
- ALPN must include `h3`

### 4. Alt-Svc Discovery

**Challenge:** Browsers need to discover HTTP/3 availability.

**Solution:** Send `Alt-Svc` header in HTTP/1.1 and HTTP/2 responses:
```
Alt-Svc: h3=":443"; ma=2592000
```

**Caching:** Browsers cache `Alt-Svc` for 30 days (`ma=2592000`).

### 5. Connection Migration

**Challenge:** QUIC allows connections to survive IP/port changes (mobile networks).

**Impact:** Minimal — aioquic handles migration internally via connection IDs.

**Worker routing:** Kernel may route migrated connection to different worker (acceptable).

### 6. 0-RTT Resumption

**Challenge:** 0-RTT allows replay attacks (non-idempotent requests).

**Mitigation:** Disable 0-RTT for POST/PUT/DELETE (only allow for GET):
```python
if scope["method"] in ["POST", "PUT", "DELETE"] and is_0rtt:
    # Reject or wait for 1-RTT confirmation
    raise ProtocolError("0-RTT not allowed for non-idempotent methods")
```

### 7. Testing and Debugging

**Challenge:** HTTP/3 is harder to debug than HTTP/1.1 (binary protocol over UDP).

**Tools:**
- **Wireshark:** QUIC dissector (supports decryption with SSLKEYLOGFILE)
- **Chrome DevTools:** Network panel shows `h3` protocol
- **curl:** `curl --http3 https://example.com` (requires HTTP/3 build)
- **aioquic examples:** `examples/http3_client.py` for testing

**Logging:** Log QUIC connection events (connection established, migration, timeout).

## Performance Analysis

### Benchmarks from Literature

**Connection establishment (50ms RTT):**
- HTTP/2 (TCP + TLS): ~100-150ms (2 RTT)
- HTTP/3 (QUIC): ~55-75ms (1 RTT)
- **Improvement:** 45% faster ([DebugBear](https://www.debugbear.com/blog/http3-vs-http2-performance))

**Time to First Byte (mobile network):**
- HTTP/2: 201ms
- HTTP/3: 176ms
- **Improvement:** 12.4% ([Cloudflare](https://blog.cloudflare.com/http-3-vs-http-2/))

**High packet loss (5% loss, 50ms RTT):**
- HTTP/2: 2.5s page load
- HTTP/3: 1.4s page load
- **Improvement:** 44% ([Request Metrics](https://requestmetrics.com/web-performance/http3-is-fast/))

**Mobile latency reduction:**
- **30% reduction** on mobile networks (2025 Akamai report, [The New Stack](https://thenewstack.io/http-3-in-the-wild-why-it-beats-http-2-where-it-matters-most/))

### When HTTP/3 Excels

✅ **Use HTTP/3 when:**
- **Mobile users** (frequent network switches, packet loss)
- **High-latency networks** (intercontinental, satellite)
- **Unstable connections** (WiFi congestion, cellular handoffs)
- **Connection resumption** (0-RTT for repeat visitors)

⚠️ **HTTP/3 neutral or slower:**
- **Low-latency, stable networks** (datacenter-to-datacenter)
- **Large file downloads** (>1 MB) — TCP congestion control is well-tuned
- **Localhost/LAN** — minimal benefit, slight overhead

### Expected pounce Performance

**Assumptions:**
- aioquic pure-Python implementation (~30% slower than native)
- Free-threading improves parallelism for UDP packet processing
- Typical web app workload (JSON APIs, SSR HTML)

**Estimated throughput:**
- **HTTP/1.1 (current):** ~100k req/s per core
- **HTTP/2 (current):** ~90k req/s per core
- **HTTP/3 (aioquic):** ~60-70k req/s per core

**Bottleneck:** Likely application code, not server (for most apps).

## Roadmap

### Current State — Zoomies Implementation

**Status:** Implemented as optional, limited-parity HTTP/3 support.

Current work is not an aioquic implementation project. The active HTTP/3 tasks
are evidence and contract hardening:

- Reload and drain behavior under H3 traffic.
- Transport documentation for TLS, UDP listeners, and deployment expectations.
- Representative Bengal/Chirp workload proof where H3 is in scope.
- Reproducible benchmarks with environment, Python build, workload, variance,
  and caveats.
- Public wording aligned with the protocol proof ledger.

### Future Optimization

**Optional enhancements:**
- [ ] HTTP/3 connection pooling and reuse metrics
- [ ] Custom QUIC congestion control (BBR)
- [ ] Advanced stream prioritization
- [ ] Performance tuning for high-throughput scenarios

## Recommendation

### Current Recommendation: Harden Proof Before Stronger Claims

HTTP/3 is already implemented through zoomies. Do not use the historical
Phase 5b/5c deferral language as an active task plan. The useful next work is to
prove and document the limited-parity support boundary:

1. Exercise H3 reload/drain behavior under representative traffic.
2. Keep transport and deployment docs explicit about TLS, UDP, and platform
   limitations.
3. Publish only benchmark numbers tied to reproducible artifacts.
4. Keep protocol feature tables aligned with the proof ledger.

## Prototype Considerations

This section is historical aioquic research. It is retained because it explains
the original evaluation path, but it is not an implementation recipe for current
Pounce HTTP/3 work.

### Minimal Viable Prototype

**Goal:** Validate aioquic integration and performance assumptions.

**Scope:**
```python
# examples/http3_prototype.py
from aioquic.asyncio import serve
from aioquic.h3.connection import H3Connection
from aioquic.h3.events import HeadersReceived, DataReceived

async def app(scope, receive, send):
    """Simple ASGI app for testing."""
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [(b"content-type", b"text/plain")],
    })
    await send({
        "type": "http.response.body",
        "body": b"Hello from HTTP/3!",
    })

class H3Protocol(QuicConnectionProtocol):
    # ... (basic implementation)

# Run server
await serve(
    "0.0.0.0",
    4433,
    configuration=QuicConfiguration(is_client=False),
    create_protocol=lambda *args, **kwargs: H3Protocol(app, *args, **kwargs),
)
```

**Testing:**
```bash
# Chrome with HTTP/3
google-chrome --enable-quic --origin-to-force-quic-on=localhost:4433 https://localhost:4433

# curl with HTTP/3 (if built with HTTP/3 support)
curl --http3 https://localhost:4433
```

**Metrics to collect:**
- Requests per second (compare to HTTP/2)
- Memory usage per connection
- Connection establishment time
- CPU usage under load

### Performance Validation

**Test scenarios:**
1. **Baseline:** HTTP/2 on localhost (low latency)
2. **Simulated mobile:** 50ms RTT, 1% packet loss (via `tc` on Linux)
3. **High load:** 10k concurrent connections
4. **Connection reuse:** 0-RTT resumption

**Tools:**
- `wrk` or `hey` for load testing
- `tc` (traffic control) for network simulation
- Chrome DevTools for browser-level metrics

## References

### Standards

- [RFC 9000: QUIC - A UDP-Based Multiplexed and Secure Transport](https://www.rfc-editor.org/rfc/rfc9000.html)
- [RFC 9001: Using TLS to Secure QUIC](https://www.rfc-editor.org/rfc/rfc9001.html)
- [RFC 9114: HTTP/3](https://www.rfc-editor.org/rfc/rfc9114.html)
- [RFC 9218: Extensible Prioritization Scheme for HTTP](https://www.rfc-editor.org/rfc/rfc9218.html)

### Libraries and Tools

- [aioquic on GitHub](https://github.com/aiortc/aioquic) — Python QUIC and HTTP/3 implementation
- [aioquic documentation](https://aioquic.readthedocs.io/) — API reference and examples
- [Hypercorn HTTP/3 support](https://github.com/pgjones/hypercorn) — ASGI server with aioquic

### Performance Studies

- [Cloudflare: HTTP/3 vs HTTP/2 Performance](https://blog.cloudflare.com/http-3-vs-http-2/)
- [DebugBear: HTTP/3 vs HTTP/2 Performance](https://www.debugbear.com/blog/http3-vs-http2-performance)
- [The New Stack: HTTP/3 in the Wild](https://thenewstack.io/http-3-in-the-wild-why-it-beats-http-2-where-it-matters-most/)
- [Request Metrics: HTTP/3 is Fast!](https://requestmetrics.com/web-performance/http3-is-fast/)

### Browser Support

- [Can I Use: HTTP/3 protocol](https://caniuse.com/http3)
- [Browser support for HTTP/3 QUIC](https://mybyways.com/blog/browser-support-for-http3-quic/)

### Educational Resources

- [Getting Started with HTTP/3 in Python](https://abibeh.medium.com/getting-started-with-http-3-in-python-7f89ae3fbdc5) — Medium tutorial
- [Cloudflare: HTTP/3 - the past, present, and future](https://blog.cloudflare.com/http3-the-past-present-and-future/)

## Conclusion

HTTP/3 represents a significant evolution in web protocols with performance
benefits for mobile and high-latency networks. The aioquic foundation described
above is historical research; Pounce's current HTTP/3 support boundary is the
zoomies implementation and the parity gates named at the top of this document.

**For Pounce:** HTTP/3 remains optional and limited-parity until the proof above
is complete.

---

**Next Steps:**
1. Keep the protocol proof ledger current.
2. Add reload/drain proof for H3 where production claims require it.
3. Validate performance assumptions with reproducible benchmark artifacts.
4. Keep public docs scoped to optional, limited-parity support until those gates
   close.
