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

:::{card} Graceful Shutdown
:icon: power
:link: ./graceful-shutdown
:description: Connection draining for Kubernetes
Zero dropped requests during rolling deployments.
:::{/card}

:::{card} Graceful Reload
:icon: refresh-cw
:link: ./graceful-reload
:description: Zero-downtime SIGHUP reload
Rolling worker restart without dropping connections.
:::{/card}

:::{card} Hot Reload
:icon: zap
:link: ./hot-reload
:description: In-process code updates
Deploy new code without connection drops.
:::{/card}

:::{card} OpenTelemetry
:icon: activity
:link: ./opentelemetry
:description: Distributed tracing with OTLP
Native integration for Jaeger, Datadog, Tempo.
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

:::{card} Prometheus Metrics
:icon: bar-chart-2
:link: ./prometheus-metrics
:description: Built-in /metrics endpoint for scraping
Prometheus text format export with zero dependencies.
:::{/card}

:::{card} Rate Limiting
:icon: shield-off
:link: ./rate-limiting
:description: Per-IP token bucket rate limiting
Protect against abusive clients and API abuse.
:::{/card}

:::{card} Request Queueing
:icon: layers
:link: ./request-queueing
:description: Bounded queue with load shedding
Graceful degradation under traffic spikes.
:::{/card}

:::{card} Sentry
:icon: alert-triangle
:link: ./sentry
:description: Error tracking and performance monitoring
Optional Sentry integration for production errors.
:::{/card}

:::{/cards}
