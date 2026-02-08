---
title: Protocols
description: HTTP/1.1, HTTP/2, and WebSocket protocol handling
draft: false
weight: 30
lang: en
type: doc
tags: [protocols, http, websocket]
keywords: [http, http2, websocket, h11, httptools, h2, wsproto]
category: explanation
icon: globe

cascade:
  type: doc
---

Pounce supports multiple protocols through a modular handler system. The core ships with HTTP/1.1 via h11. HTTP/2 and WebSocket are optional extras.

:::{cards}
:columns: 2
:gap: medium

:::{card} HTTP/1.1
:icon: arrow-right
:link: ./http1
:description: Default protocol — h11 (pure Python) or httptools (C-accelerated)
The foundation of Pounce's request handling.
:::{/card}

:::{card} HTTP/2
:icon: layers
:link: ./http2
:description: Stream multiplexing, header compression, priority signals
Install with `pounce[h2]`.
:::{/card}

:::{card} WebSocket
:icon: message-circle
:link: ./websocket
:description: Full-duplex communication, including WebSocket over HTTP/2
Install with `pounce[ws]`.
:::{/card}

:::{/cards}

## Protocol Detection

For TLS connections, Pounce uses ALPN (Application-Layer Protocol Negotiation) to select between HTTP/1.1 and HTTP/2. For plain connections, HTTP/1.1 is used by default.

WebSocket connections start as HTTP/1.1 (or HTTP/2) and upgrade via the standard upgrade mechanism.
