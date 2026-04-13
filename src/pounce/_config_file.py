"""TOML configuration file loading for pounce.

Searches for configuration in this order:
1. ``pounce.toml`` in the current working directory
2. ``[tool.pounce]`` section in ``pyproject.toml``

Values from the config file are used as defaults — CLI arguments
always take precedence.

Example ``pounce.toml``::

    host = "0.0.0.0"
    port = 8080
    workers = 4
    log_level = "debug"

    [static_files]
    "/static" = "./public"
    "/assets" = "./dist"

Example ``pyproject.toml``::

    [tool.pounce]
    host = "0.0.0.0"
    port = 8080
    workers = 4

"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any

from pounce.config import ServerConfig

logger = logging.getLogger("pounce.config")

# Fields that cannot be set via TOML (callables, complex objects, internal).
_EXCLUDED_FIELDS = frozenset(
    {
        "access_log_filter",
        "display",
        "middleware",
        "trusted_hosts_wildcard",  # derived from trusted_hosts
        "_VALID_LOG_LEVELS",
        "_VALID_LOG_FORMATS",
        "_VALID_WORKER_MODES",
    }
)

# All valid TOML keys (ServerConfig field names minus excluded).
_VALID_KEYS = frozenset(
    f.name for f in dataclass_fields(ServerConfig) if f.name not in _EXCLUDED_FIELDS
)

# Deprecated aliases: old name → canonical name.
_DEPRECATED_ALIASES: dict[str, str] = {
    "reload_dir": "reload_dirs",
}


def find_config_file(search_dir: Path | None = None) -> Path | None:
    """Find a pounce config file in the given (or current) directory.

    Returns:
        Path to ``pounce.toml`` or ``pyproject.toml`` if found, else None.

    """
    base = search_dir or Path.cwd()

    pounce_toml = base / "pounce.toml"
    if pounce_toml.is_file():
        return pounce_toml

    pyproject = base / "pyproject.toml"
    if pyproject.is_file():
        return pyproject

    return None


def load_config_file(path: Path) -> dict[str, Any]:
    """Load pounce configuration from a TOML file.

    For ``pounce.toml``, the entire file is treated as config.
    For ``pyproject.toml``, reads the ``[tool.pounce]`` section.

    Returns:
        Dict of config key-value pairs (only valid ServerConfig fields).

    Raises:
        ValueError: If the file contains unknown keys.

    """
    with path.open("rb") as f:
        data = tomllib.load(f)

    if path.name == "pyproject.toml":
        data = data.get("tool", {}).get("pounce", {})
        if not data:
            return {}

    return _validate_and_coerce(data, path)


def _validate_and_coerce(data: dict[str, Any], source: Path) -> dict[str, Any]:
    """Validate keys and coerce types to match ServerConfig fields.

    Returns:
        Cleaned dict ready to pass to ServerConfig.

    Raises:
        ValueError: On unknown keys.

    """
    result: dict[str, Any] = {}

    # Rewrite deprecated aliases before validation.
    for old_name, new_name in _DEPRECATED_ALIASES.items():
        if old_name in data:
            if new_name in data:
                msg = (
                    f"Both '{old_name}' and '{new_name}' found in {source}. "
                    f"Remove '{old_name}' (deprecated) and use '{new_name}' instead."
                )
                raise ValueError(msg)
            logger.warning(
                "'%s' is deprecated in config files, use '%s' instead",
                old_name,
                new_name,
            )
            data[new_name] = data.pop(old_name)

    unknown = set(data.keys()) - _VALID_KEYS

    if unknown:
        msg = f"Unknown keys in {source}: {', '.join(sorted(unknown))}"
        raise ValueError(msg)

    # Build a map of field name → field type for coercion
    field_types = {f.name: f.type for f in dataclass_fields(ServerConfig)}

    for key, value in data.items():
        if key in _EXCLUDED_FIELDS:
            continue

        expected_type = field_types.get(key, "")

        # Coerce TOML tables to the expected Python type
        if key == "trusted_hosts" and isinstance(value, list):
            value = frozenset(value)
        elif key == "static_files" and isinstance(value, dict):
            # TOML: {"/static" = "./public"} → dict[str, str]
            value = {str(k): str(v) for k, v in value.items()}
        elif key in ("reload_include", "reload_dirs") and isinstance(value, list):
            value = tuple(value)
        elif "frozenset" in str(expected_type) and isinstance(value, list):
            value = frozenset(value)

        result[key] = value

    return result


def load_config_with_overrides(
    cli_overrides: dict[str, Any],
    *,
    search_dir: Path | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Load config file and merge with CLI overrides.

    CLI overrides always win. Only non-None CLI values are treated as
    overrides (so that unset CLI flags don't mask file values).

    Args:
        cli_overrides: Dict of CLI arg values. Keys with None values are
            treated as "not set" and won't override file config.
        search_dir: Directory to search for config files.
        config_path: Explicit config file path (skips search).

    Returns:
        Merged config dict ready to pass to ServerConfig.

    """
    # Load from file
    file_config: dict[str, Any] = {}
    path = config_path or find_config_file(search_dir)
    if path is not None:
        try:
            file_config = load_config_file(path)
            if file_config:
                logger.info("Loaded config from %s", path)
        except (ValueError, tomllib.TOMLDecodeError) as exc:
            logger.warning("Failed to load %s: %s", path, exc)
            raise

    # Merge: file values are defaults, CLI overrides win
    merged = dict(file_config)
    merged.update({k: v for k, v in cli_overrides.items() if v is not None})

    return merged
