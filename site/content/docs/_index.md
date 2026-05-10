---
title: Documentation
description: Documentation for running, deploying, and tuning the Pounce Python ASGI server
draft: false
weight: 10
lang: en
type: doc
keywords: [pounce, python asgi server, documentation, deployment, uvicorn migration]
category: overview

cascade:
  type: doc
---

## Learn Pounce

Pounce is a Python ASGI server for deployments, streaming workloads, and
free-threaded Python. Start with installation, then move into deployment, protocol
support, and migration guides.

:::{cards}
:columns: 2
:gap: medium

:::{card} Get Started
:icon: rocket
:link: ./get-started/
Install Pounce and serve your first ASGI app in minutes.
:::{/card}

:::{card} Protocols
:icon: globe
:link: ./protocols/
HTTP/1.1, HTTP/2, WebSocket, and HTTP/3 — how each protocol layer works.
:::{/card}

:::{card} Configuration
:icon: sliders
:link: ./configuration/
ServerConfig, CLI options, TLS setup, and tuning parameters.
:::{/card}

:::{card} Deployment
:icon: server
:link: ./deployment/
Workers, compression, production hardening, and scaling.
:::{/card}

::::{card} Migrate from Uvicorn
:icon: repeat
:link: ./tutorials/migrate-from-uvicorn
Switch from Uvicorn with minimal CLI and deployment changes.
::::{/card}

:::{/cards}

---

## Go Deeper

:::{cards}
:columns: 2
:gap: medium

:::{card} About
:icon: info
:link: ./about/
Architecture, performance, thread safety, and comparisons.
:::{/card}

:::{card} Extending
:icon: puzzle
:link: ./extending/
ASGI bridge internals, custom protocol handlers.
:::{/card}

:::{card} Tutorials
:icon: book-open
:link: ./tutorials/
Step-by-step guides for common workflows and migrations.
:::{/card}

:::{card} Reference
:icon: file-text
:link: ./reference/
Complete API reference, error codes, and configuration fields.
:::{/card}

:::{/cards}

---

## Troubleshooting

Having issues? Check the [[docs/troubleshooting|troubleshooting guide]] for common problems and solutions.
