---
title: Deployment
description: Workers, compression, and production configuration
draft: false
weight: 50
lang: en
type: doc
tags: [deployment, production, workers, compression]
keywords: [deployment, production, workers, compression, scaling]
category: how-to
icon: server

cascade:
  type: doc
---

:::{cards}
:columns: 2
:gap: medium

:::{card} Workers
:icon: cpu
:link: ./workers
:description: Configuring worker count, thread vs process mode
Tuning parallelism for your workload.
:::{/card}

:::{card} Compression
:icon: minimize-2
:link: ./compression
:description: Zstd, gzip, and content negotiation
Zero-dependency compression with Python 3.14 stdlib.
:::{/card}

:::{card} Production
:icon: shield
:link: ./production
:description: Hardening, reverse proxy, and scaling patterns
Running Pounce in production environments.
:::{/card}

:::{/cards}
