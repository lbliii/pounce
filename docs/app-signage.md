# Application signage and startup banner

Pounce can show an optional **application** block above the server banner, emit the same identity in **JSON** startup logs, and tune layout with **`signage`** (TTY pretty mode only).

## `DisplayConfig`

Set `ServerConfig(display=DisplayConfig(...))` or use environment variables / CLI (see below).

| Field     | Purpose                                              |
| --------- | ---------------------------------------------------- |
| `name`    | Application title; if unset, no app block is shown   |
| `tagline` | One line under the name                              |
| `version` | Shown next to the name                               |
| `lines`   | Extra static lines under the app block (full layout) |
| `signage` | `full`, `minimal`, or `off` (unset merges from sources; resolved default is `full`) |

## Precedence (highest first)

1. CLI: `--app-name`, `--app-tagline`, `--app-version`, `--signage`
2. Environment: `POUNCE_APP_NAME`, `POUNCE_APP_TAGLINE`, `POUNCE_APP_VERSION`, `POUNCE_SIGNAGE`, `POUNCE_APP_PYPROJECT`
3. `ServerConfig.display`
4. `[tool.pounce.display]` in the discovered `pyproject.toml`
5. `app.__pounce_display__` — a `dict` or a zero-argument callable returning a dict (sync only; read once at startup before lifespan)

Unknown keys in dicts are ignored. Invalid `signage` strings are ignored where noted.

## `signage` modes (TTY pretty output only)

| Mode    | Behavior |
| ------- | -------- |
| `full`  | Default. App block (if `name`), then full Pounce banner with sections. |
| `minimal` | App `header_box` (if `name`), then one compact Pounce line. |
| `off`     | No pretty banner; `logger.info` lines only. JSON startup line unchanged. |

`log_format=json` ignores `signage` for the startup line (always emits structured JSON).

## `pyproject.toml`

```toml
[tool.pounce.display]
name = "My App"
tagline = "Does useful things"
version = "1.0.0"
signage = "minimal"
```

Discovery walks parents of the current working directory for `pyproject.toml`, or uses `POUNCE_APP_PYPROJECT` for an explicit path.

## Programmatic example

```python
from pounce.config import ServerConfig
from pounce.display import DisplayConfig
from pounce.server import Server

config = ServerConfig(
    host="0.0.0.0",
    port=8080,
    display=DisplayConfig(
        name="My App",
        tagline="Hypermedia-native dashboard",
        version="1.2.0",
        signage="minimal",
    ),
)
server = Server(config, app)
server.run()
```

## JSON startup line

When `log_format=json` and `display.name` is set, the startup object may include:

```json
"app": {"name": "...", "tagline": "...", "version": "..."}
```

Optional fields are omitted when unset.
