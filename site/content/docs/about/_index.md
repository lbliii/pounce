---
title: About
description: Architecture, performance, thread model, and comparison guidance for the Pounce Python ASGI server
draft: false
weight: 20
lang: en
type: doc
tags: [architecture, design, about]
keywords: [python asgi server, architecture, performance, thread-safety, uvicorn alternative]
category: explanation
icon: info

cascade:
  type: doc
---

:::{cards}
:columns: 2
:gap: medium

Pounce is an ASGI server with a worker model tailored to Python 3.14t. This section
explains how it works, where it fits, and how to compare it to more familiar servers.

:::{card} Architecture
:icon: layers
:link: ./architecture
:description: How Pounce's server, supervisor, and worker layers fit together
The internal design of the server pipeline.
:::{/card}

:::{card} Performance
:icon: activity
:link: ./performance
:description: Benchmarks and what makes Pounce fast
Streaming-first design and threading performance.
:::{/card}

:::{card} Thread Safety
:icon: shield
:link: ./thread-safety
:description: How Pounce handles shared state across workers
Frozen configuration, per-request state, zero synchronization.
:::{/card}

:::{card} Comparison
:icon: bar-chart-2
:link: ./comparison
:description: When Pounce fits, when to choose alternatives, and how it compares to Uvicorn deployments
Architecture and deployment guidance for choosing the right server.
:::{/card}

:::{card} FAQ
:icon: help-circle
:link: ./faq
:description: Frequently asked questions
Common questions and answers.
:::{/card}

:::{card} Ecosystem
:icon: layers
:link: ./ecosystem
:description: The Bengal stack
All seven projects in the reactive Python stack.
:::{/card}

:::{/cards}
