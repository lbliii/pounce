# HTTP/3 Support: Current State and Closed Parity Gates

**Status:** Implemented — `bengal-zoomies` backend (optional)
**Updated:** July 2026

HTTP/3 is implemented in Pounce using
[zoomies](https://github.com/lbliii/zoomies) (`bengal-zoomies`), a
free-threading-native sans-I/O QUIC/HTTP/3 library. aioquic was evaluated first
but rejected because it ships Limited API C extensions that are incompatible
with Python 3.14 free-threaded. The original aioquic research is preserved as a
[historical appendix](#appendix-historical-aioquic-research-february-2026) for
decision history only; it is **not** the active design, config schema, or
implementation plan.

The authoritative support boundary is
[core-contract.md](core-contract.md) and
[protocol-proof-ledger.json](protocol-proof-ledger.json). The concise
operator-facing page is `site/content/docs/protocols/http3.md`. This document
must stay secondary to those.

## Current Implementation

HTTP/3 is an **optional** protocol extra. It runs over a UDP
listener with QUIC transport state, separate from the TCP paths used by
HTTP/1.1 and HTTP/2.

- **Install:** `pip install "bengal-pounce[h3]"` plus TLS configuration. QUIC
  embeds TLS 1.3 in its handshake, so a certificate and key are mandatory.
- **Enable:** set `http3_enabled=True` with `ssl_certfile`/`ssl_keyfile`, or
  pass `--http3 --ssl-certfile cert.pem --ssl-keyfile key.pem` to
  `pounce serve`.
- **Missing extra:** if `bengal-zoomies` is absent the HTTP/3 path fails with an
  install hint, and `pounce check` reports the missing stack when
  `http3_enabled` is configured.

### Verified config surface

The current `ServerConfig` HTTP/3 fields (validate against
`src/pounce/config.py`, `_config_file.py`, and `_config_schema.py`):

| Field | Default | Description |
|-------|---------|-------------|
| `http3_enabled` | `False` | Enables the UDP/QUIC listener. |
| `http3_max_connections` | `10_000` | Maximum concurrent QUIC connections. |
| `http3_idle_timeout` | `30.0` | Idle timeout in seconds. |
| `http3_qpack_max_table_capacity` | `0` | QPACK dynamic table capacity; `0` uses static-table-only compression. |
| `http3_zero_rtt_enabled` | `False` | Allows TLS 0-RTT; unsafe methods receive `425 Too Early`. |

### Behavior covered by tests

- Oversized request bodies rejected with `413`.
- Malformed or contradictory pseudo-headers rejected before ASGI scope
  construction.
- `Alt-Svc` advertised from HTTP/2 responses when HTTP/3 is enabled.
- Built-in health endpoint and request ID handling match the TCP paths.
- Lifespan state is passed into H3 ASGI scopes.

Proof lives in `tests/integration/test_h3_integration.py`,
`tests/integration/test_http3.py`, `tests/unit/test_h3_bridge.py`,
`tests/unit/test_h3_handler.py`, `tests/unit/test_h3_worker.py`, and
`tests/unit/test_optional_protocol_diagnostics.py`.

## Closed Parity Gates

Both gates named in `protocol-proof-ledger.json` are closed:

1. **Reload/drain proof** — real-UDP tests exercise generation rotation,
   bounded shutdown, and orphan-thread cleanup. Under-budget streams complete;
   streams exceeding `shutdown_timeout` are cancelled and QUIC closes.
2. **Benchmark artifact** — the real-CLI profile and five-sample local artifact
   record environment, Python build, workload, variance, raw output, telemetry,
   and caveats. The numbers remain a protocol snapshot, not a product target.

WebSocket over HTTP/3 (RFC 9220 Extended CONNECT) is **not supported** and is
not in scope here.

## Confidence Gate

Treat `protocol-proof-ledger.json` as the source of truth for the H3 claim
level. Keep protocol feature tables, public wording, and this document aligned
with the ledger's `status` and explicit lifecycle semantics for `http3`.

---

## Appendix: Historical aioquic Research (February 2026)

> **Superseded historical context.** Everything below is the original
> February 2026 evaluation that recommended deferring HTTP/3 and building on
> aioquic. Pounce does **not** use aioquic; HTTP/3 shipped on `bengal-zoomies`.
> This appendix is retained only so the original decision path is discoverable.
> It is not design guidance, not a config contract, and not a production-
> readiness claim. Git history preserves the full original document if more
> detail is needed.

The original roadmap evaluated QUIC/HTTP/3 support and recommended deferring a
full implementation to a later phase while laying groundwork. That
recommendation is obsolete: HTTP/3 is implemented (see above).

The aioquic-era research concluded:

- **aioquic** (then 1.3.0) was the leading pure-Python QUIC/HTTP/3 library,
  standards-compliant (RFC 9000/9001/9114), BSD-licensed, and sans-I/O. It was
  ultimately rejected for Pounce because its Limited API C extensions are
  incompatible with Python 3.14 free-threaded.
- **Browser support** for HTTP/3 is universal (Chrome 87+, Firefox 88+,
  Safari 16.4+, Edge 87+), discovered via the `Alt-Svc: h3=":443"` header and
  selected through ALPN.
- **Performance** literature reported 12–52% improvements on mobile and
  high-latency or lossy networks (e.g. ~45% faster connection establishment at
  50 ms RTT, ~30% mobile latency reduction). These figures are external
  literature, **not** Pounce benchmarks.
- **Architecture** would require a UDP worker model distinct from the TCP
  `accept()` loop (a separate `H3Worker` with `create_datagram_endpoint`,
  `SO_REUSEPORT` distribution, mandatory TLS 1.3, `Alt-Svc` advertisement,
  RFC 9218 priorities, and 0-RTT replay mitigation for non-idempotent methods).
  The shipped zoomies design supersedes this sketch.
- Throughput estimates (~60–70k req/s/core for pure-Python H3) were
  assumptions, never measured against the zoomies backend.

### References

Standards:

- [RFC 9000: QUIC](https://www.rfc-editor.org/rfc/rfc9000.html)
- [RFC 9001: Using TLS to Secure QUIC](https://www.rfc-editor.org/rfc/rfc9001.html)
- [RFC 9114: HTTP/3](https://www.rfc-editor.org/rfc/rfc9114.html)
- [RFC 9218: Extensible Prioritization Scheme for HTTP](https://www.rfc-editor.org/rfc/rfc9218.html)

Background and performance studies (external literature, not Pounce proof):

- [Cloudflare: HTTP/3 vs HTTP/2 Performance](https://blog.cloudflare.com/http-3-vs-http-2/)
- [DebugBear: HTTP/3 vs HTTP/2 Performance](https://www.debugbear.com/blog/http3-vs-http2-performance)
- [The New Stack: HTTP/3 in the Wild](https://thenewstack.io/http-3-in-the-wild-why-it-beats-http-2-where-it-matters-most/)
- [Can I Use: HTTP/3](https://caniuse.com/http3)
- [aioquic on GitHub](https://github.com/aiortc/aioquic) (evaluated, not used)
