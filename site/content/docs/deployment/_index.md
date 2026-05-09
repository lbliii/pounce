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

Deploy Pounce in production. Start with **Production** for the essentials, then tune **Workers**, enable **Compression**, and layer on **Observability** and **Security** as needed.

:::{cards}
:columns: 2
:gap: medium

:::{card} Production
:icon: shield
:link: ./production
:description: Hardening, reverse proxy, and scaling patterns
Running Pounce in production environments.
:::{/card}

:::{card} Workers
:icon: cpu
:link: ./workers
:description: Configuring worker count, thread vs process mode
Tuning parallelism for your workload.
:::{/card}

:::{card} Railway
:icon: cloud
:link: ./railway
:description: PORT, health checks, platform TLS, and deploy drains
Deploying Pounce on Railway public networking.
:::{/card}

:::{card} Compression
:icon: minimize-2
:link: ./compression
:description: Zstd, gzip, and content negotiation
Zero-dependency compression with Python 3.14 stdlib.
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
:description: Health checks, metrics, OpenTelemetry, Sentry
Monitoring, tracing, and error tracking for production.
:::{/card}

:::{card} Server Lifecycle
:icon: refresh-cw
:link: ./lifecycle
:description: Graceful reload, hot deploy, and shutdown
Zero-downtime operations with connection draining.
:::{/card}

:::{card} Backpressure
:icon: shield-off
:link: ./backpressure
:description: Rate limiting and request queueing
Per-client and global load protection.
:::{/card}

:::{/cards}
