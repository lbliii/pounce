---
title: Deployment
description: Workers, compression, security, and production configuration
draft: false
weight: 50
lang: en
type: doc
tags: [deployment, production, workers, compression, security, observability]
keywords: [deployment, production, workers, compression, scaling, security, monitoring]
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

:::{card} Security
:icon: lock
:link: ./security
:description: Proxy headers, CRLF protection, request smuggling
Built-in security features for production deployments.
:::{/card}

:::{card} Observability
:icon: activity
:link: ./observability
:description: Health checks, request IDs, Prometheus metrics
Monitoring, tracing, and metrics for production.
:::{/card}

:::{/cards}
