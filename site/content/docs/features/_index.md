---
title: Features
description: Built-in features beyond core protocol support
weight: 30
---

# Features

Built-in capabilities that ship with pounce -- no external dependencies required (except where noted).

:::{cards}
:columns: 2
:gap: medium

:::{card} Static File Serving
:icon: file
:link: ./static-files
:description: Chunked serving with ETags, range requests, and pre-compression
:::{/card}

:::{card} Middleware System
:icon: layers
:link: ./middleware
:description: ASGI3 middleware stack for request/response transformation
:::{/card}

:::{card} Development Error Pages
:icon: alert-circle
:link: ./error-pages
:description: Rich HTML tracebacks with syntax highlighting (debug mode)
:::{/card}

:::{card} Lifecycle Logging
:icon: activity
:link: ./lifecycle-logging
:description: Structured connection/request events with correlation IDs
:::{/card}

:::{card} WebSocket Compression
:icon: minimize-2
:link: ./websocket-compression
:description: Permessage-deflate compression (RFC 7692)
:::{/card}

:::{/cards}

For protocol support (HTTP/1.1, HTTP/2, WebSocket, HTTP/3), see [[docs/protocols|Protocols]]. For deployment features (compression, observability, backpressure), see [[docs/deployment|Deployment]].
