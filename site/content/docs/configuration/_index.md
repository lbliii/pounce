---
title: Configuration
description: ServerConfig, CLI options, TLS, and display settings
draft: false
weight: 40
lang: en
type: doc
tags: [configuration, settings, cli, tls]
keywords: [configuration, settings, serverconfig, cli, tls, ssl, display]
category: reference
icon: sliders

cascade:
  type: doc
---

:::{cards}
:columns: 2
:gap: medium

:::{card} ServerConfig
:icon: settings
:link: ./server-config
:description: The frozen dataclass that controls everything
All configuration fields, defaults, and validation rules.
:::{/card}

:::{card} CLI Reference
:icon: terminal
:link: ./cli
:description: Command-line options for the pounce command
All flags and arguments.
:::{/card}

:::{card} TLS
:icon: lock
:link: ./tls
:description: Setting up TLS termination
Certificate configuration and ALPN for HTTP/2.
:::{/card}

:::{card} Display & Signage
:icon: monitor
:link: ./display
:description: Application banner and startup display modes
Name, version, tagline, and signage layout.
:::{/card}

:::{/cards}
