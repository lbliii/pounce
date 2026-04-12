---
title: Display & Signage
description: Application banner, startup display, and signage modes
weight: 4
---

# Display & Signage

Pounce can show an optional application banner at startup with your app's name, version, and tagline. Controlled via `DisplayConfig`.

## Configuration

```python
from pounce.config import ServerConfig
from pounce.display import DisplayConfig

config = ServerConfig(
    display=DisplayConfig(
        name="My App",
        tagline="Hypermedia-native dashboard",
        version="1.2.0",
        signage="minimal",
    ),
)
```

### DisplayConfig Fields

| Field | Purpose |
|---|---|
| `name` | Application title (no app block shown if unset) |
| `tagline` | One-liner under the name |
| `version` | Shown next to the name |
| `lines` | Extra static lines in the app header |
| `signage` | `full` (default), `minimal`, or `off` |

### Signage Modes (TTY only)

| Mode | Behavior |
|---|---|
| `full` | App block + full pounce banner with sections |
| `minimal` | App header box + one compact pounce line |
| `off` | No pretty banner, `logger.info` lines only |

`log_format=json` ignores signage and always emits structured JSON.

## Precedence (highest first)

1. **CLI**: `--app-name`, `--app-tagline`, `--app-version`, `--signage`
2. **Environment**: `POUNCE_APP_NAME`, `POUNCE_APP_TAGLINE`, `POUNCE_APP_VERSION`, `POUNCE_SIGNAGE`
3. **ServerConfig.display**
4. **pyproject.toml**: `[tool.pounce.display]`
5. **App hook**: `app.__pounce_display__` (dict or zero-arg callable)

## pyproject.toml

```toml
[tool.pounce.display]
name = "My App"
tagline = "Does useful things"
version = "1.0.0"
signage = "minimal"
```

Discovery walks parent directories for `pyproject.toml`, or use `POUNCE_APP_PYPROJECT` for an explicit path.

## JSON Startup Line

When `log_format=json` and `display.name` is set:

```json
{"app": {"name": "My App", "tagline": "...", "version": "1.2.0"}}
```

Optional fields are omitted when unset.
