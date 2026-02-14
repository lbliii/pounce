---
title: About
description: Architecture, performance, and design philosophy behind Pounce
draft: false
weight: 20
lang: en
type: doc
tags: [architecture, design, about]
keywords: [architecture, performance, thread-safety, comparison]
category: explanation
icon: info

cascade:
  type: doc
---

:::{cards}
:columns: 2
:gap: medium

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
Frozen config, per-request state, zero synchronization.
:::{/card}

:::{card} Comparison
:icon: bar-chart-2
:link: ./comparison
:description: When Pounce fits and what it offers
Architecture and deployment guidance.
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
