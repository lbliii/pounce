---
title: Extending
description: ASGI bridge internals and custom protocol handlers
draft: false
weight: 60
lang: en
type: doc
tags: [extending, internals, asgi, bridge]
keywords: [extending, asgi, bridge, protocol, custom]
category: explanation
icon: puzzle

cascade:
  type: doc
---

:::{cards}
:columns: 2
:gap: medium

:::{card} ASGI Bridge
:icon: link
:link: ./asgi-bridge
:description: How Pounce translates protocol events to ASGI interface
The scope/receive/send bridge layer.
:::{/card}

:::{card} Custom Protocols
:icon: code
:link: ./custom-protocols
:description: The ProtocolHandler protocol and how to extend it
Adding custom protocol support.
:::{/card}

:::{/cards}
