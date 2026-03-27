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
:description: Health checks, request IDs, Prometheus metrics
Monitoring, tracing, and metrics for production.
:::{/card}

:::{card} Graceful Reload
:icon: refresh-cw
:link: ./graceful-reload
:description: Zero-downtime code reloads via SIGHUP
Rolling restart with automatic worker draining.
:::{/card}

:::{card} Hot Reload
:icon: zap
:link: ./hot-reload
:description: Deploy code without dropping connections
Graceful worker replacement with SO_REUSEPORT.
:::{/card}

:::{card} Graceful Shutdown
:icon: power
:link: ./graceful-shutdown
:description: Connection draining for Kubernetes, Docker, systemd
Production-grade SIGTERM handling.
:::{/card}

:::{card} OpenTelemetry
:icon: radio
:link: ./opentelemetry
:description: Distributed tracing with Jaeger, Datadog, Tempo
Native OTLP export with zero code changes.
:::{/card}

:::{card} Rate Limiting
:icon: shield-off
:link: ./rate-limiting
:description: Per-IP token bucket abuse protection
Built-in 429 responses with burst support.
:::{/card}

:::{card} Request Queueing
:icon: layers
:link: ./request-queueing
:description: Bounded queues with load shedding
Graceful 503 responses when overloaded.
:::{/card}

:::{card} Sentry
:icon: alert-triangle
:link: ./sentry
:description: Error tracking and performance monitoring
Automatic exception capture with request context.
:::{/card}

:::{/cards}
