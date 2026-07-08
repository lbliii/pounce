---
title: Protocols
description: HTTP/1.1, HTTP/2, and WebSocket protocol handling
draft: false
weight: 30
lang: en
type: doc
tags: [protocols, http, websocket]
keywords: [http, http2, http3, websocket, h11, h2, wsproto, quic]
category: explanation
icon: globe

cascade:
  type: doc
---

Pounce supports multiple protocols through a modular handler system. The core ships with HTTP/1.1 via h11. HTTP/2, HTTP/3, and WebSocket are optional extras with protocol-specific support boundaries.
TLS support is also optional and installs with `bengal-pounce[tls]`.

:::{cards}
:columns: 2
:gap: medium

:::{card} HTTP/1.1
:icon: arrow-right
:link: ./http1
:description: Default protocol - h11 (pure Python) plus sync-worker fast parser
The foundation of Pounce's request handling.
:::{/card}

:::{card} HTTP/2
:icon: layers
:link: ./http2
:description: Stream multiplexing, header compression, priority signals
Install with `bengal-pounce[h2]`.
:::{/card}

:::{card} HTTP/3
:icon: radio
:link: ./http3
:description: Optional QUIC/UDP transport, QPACK, 0-RTT policy, TLS required
Install with `bengal-pounce[h3]`.
:::{/card}

:::{card} WebSocket
:icon: message-circle
:link: ./websocket
:description: Full-duplex communication via wsproto
Install with `bengal-pounce[ws]`; WebSocket over HTTP/2 also requires `h2`.
:::{/card}

:::{/cards}

## Protocol Detection

For TLS connections, Pounce uses ALPN (Application-Layer Protocol Negotiation) to select between HTTP/1.1 and HTTP/2. For plain connections, HTTP/1.1 is used by default.

HTTP/3 uses QUIC over UDP and runs on its own UDP listener when enabled with TLS.

WebSocket connections start as HTTP/1.1 (or HTTP/2) and upgrade via the standard upgrade mechanism.
