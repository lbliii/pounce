"""
ServerConfig introspection — JSON Schema export and redacted view.

Two entry points, both consumed by the ``pounce config`` subcommands:

- :func:`build_schema` / :func:`build_toml_template` — emit a JSON Schema
  (Draft 2020-12) or commented ``pounce.toml`` template from the
  :class:`~pounce.config.ServerConfig` dataclass. Enables agents and humans
  to discover the config surface without reading source.
- :func:`redacted_config_view` + :data:`INFO_ALLOWLIST` — produce the
  fail-closed redacted view used by ``pounce config show`` and the Sprint 4
  ``/_pounce/info`` endpoint. Every field must appear in the allowlist;
  absent fields are implicitly redacted.

Design notes:

- No new runtime dependencies. Stdlib-only (``dataclasses``, ``typing``,
  ``json``).
- Deterministic output: properties are emitted in sorted order so golden
  snapshots remain stable.
- Internal / callable / opaque fields (the ``_IIC_SKIP_FIELDS`` set in
  ``config.py``) are skipped in both schema and redacted view.
"""

from __future__ import annotations

import dataclasses
import json
import typing
from types import UnionType
from typing import Any, Literal, get_args, get_origin

from pounce.config import _IIC_SKIP_FIELDS, ServerConfig

# ---------------------------------------------------------------------------
# JSON Schema generation
# ---------------------------------------------------------------------------

_JSON_PRIMITIVES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}

#: Stability tiers for ServerConfig fields. Every non-skipped field is either
#: explicitly listed in ``_BETA_FIELD_PREFIXES`` (beta) or treated as stable.
#: ``build_schema`` stamps the beta tier as an ``x-stability`` annotation so
#: ``pounce config schema`` consumers can tell mature knobs from firming ones.
#: This is a stability axis only -- it does not change the field set, names, or
#: redaction (``INFO_ALLOWLIST``). See ``config.py`` docstring and
#: ``docs/design/core-contract.md``. Tracked by issue #157.
_BETA_FIELD_PREFIXES: tuple[str, ...] = (
    "rate_limit_",
    "request_queue_",
    "introspection_",
    "http3_",
    "otel_",
    "sentry_",
    "metrics_",
)

_BETA_STABILITY_NOTE = (
    "beta: behavior, surface, or proof is still firming up -- "
    "pin versions and validate in staging before relying on it"
)


def _field_stability(name: str) -> str:
    """Return the stability tier (``"stable"`` or ``"beta"``) for *name*."""
    if name.startswith(_BETA_FIELD_PREFIXES):
        return "beta"
    return "stable"


def _strip_none(tp: Any) -> tuple[Any, bool]:
    """For ``X | None``, return ``(X, True)``. Else ``(tp, False)``."""
    origin = get_origin(tp)
    if origin is typing.Union or origin is UnionType:
        args = [a for a in get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return tp, False


def _map_type(tp: Any) -> dict[str, Any] | None:
    """Return a JSON Schema fragment for *tp*, or None to drop the field."""
    tp, nullable = _strip_none(tp)

    if tp in _JSON_PRIMITIVES:
        out: dict[str, Any] = {"type": _JSON_PRIMITIVES[tp]}
        if nullable:
            out["type"] = [out["type"], "null"]
        return out

    origin = get_origin(tp)
    args = get_args(tp)

    if origin in (tuple, list, frozenset, set):
        item_type = args[0] if args else Any
        item_schema = _map_type(item_type) if item_type is not Any else {}
        return {"type": "array", "items": item_schema}

    if origin is dict:
        value_type = args[1] if len(args) == 2 else Any
        value_schema = _map_type(value_type) if value_type is not Any else {}
        return {"type": "object", "additionalProperties": value_schema}

    return None


def _default_for(f: dataclasses.Field[Any]) -> Any:
    if f.default is not dataclasses.MISSING:
        val = f.default
    elif f.default_factory is not dataclasses.MISSING:
        val = f.default_factory()
    else:
        return dataclasses.MISSING
    if isinstance(val, (frozenset, set, tuple)):
        val = sorted(val) if all(isinstance(x, str) for x in val) else list(val)
    return val


def _collect_enum_fields(cls: type) -> dict[str, frozenset[str]]:
    """Harvest enum constraints from ``_VALID_<NAME>S`` dataclass fields.

    ``_VALID_LOG_LEVELS`` → ``log_level``.
    """
    out: dict[str, frozenset[str]] = {}
    for f in dataclasses.fields(cls):
        if not f.name.startswith("_VALID_") or not f.name.endswith("S"):
            continue
        if f.default is dataclasses.MISSING:
            continue
        key = f.name.removeprefix("_VALID_").removesuffix("S").lower()
        out[key] = f.default
    return out


def build_schema(cls: type = ServerConfig) -> dict[str, Any]:
    """Build a JSON Schema Draft 2020-12 document for a dataclass.

    The schema is deterministic: properties are sorted alphabetically so
    golden-snapshot tests stay stable across runs.
    """
    hints = typing.get_type_hints(cls)
    enum_fields = _collect_enum_fields(cls)
    properties: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.name.startswith("_") or f.name in _IIC_SKIP_FIELDS:
            continue
        schema = _map_type(hints.get(f.name, f.type))
        if schema is None:
            continue
        default = _default_for(f)
        if default is not dataclasses.MISSING:
            schema["default"] = default
        if f.name in enum_fields:
            schema["enum"] = sorted(enum_fields[f.name])
        if _field_stability(f.name) == "beta":
            schema["x-stability"] = "beta"
            schema["description"] = _BETA_STABILITY_NOTE
        elif f.name == "worker_mode":
            # The field is stable, but the "subinterpreter" value is beta.
            schema["x-stability-values"] = {"subinterpreter": "beta"}
            schema["description"] = (
                'worker_mode="subinterpreter" is beta (PEP 734, limited '
                "lifecycle proof); auto/sync/async are stable"
            )
        properties[f.name] = schema
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://pounce.dev/schemas/server-config.json",
        "title": cls.__name__,
        "description": cls.__doc__.splitlines()[0] if cls.__doc__ else "",
        "type": "object",
        "additionalProperties": False,
        "properties": dict(sorted(properties.items())),
    }


def _toml_value(v: Any) -> str:
    """Render a Python default as a TOML literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if v is None:
        return '""'
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    if isinstance(v, dict):
        items = ", ".join(f"{json.dumps(k)} = {_toml_value(val)}" for k, val in v.items())
        return "{" + items + "}"
    return json.dumps(str(v))


def build_toml_template(cls: type = ServerConfig) -> str:
    """Emit a commented TOML template for *cls*, one field per line.

    ``pounce.toml`` puts config keys at the top level (no section header) —
    the loader in ``_config_file.py`` treats the whole file as config.
    Users who want ``[tool.pounce]`` for pyproject.toml can copy the same
    commented lines under that heading.
    """
    schema = build_schema(cls)
    lines: list[str] = [
        f"# {cls.__name__} — generated by `pounce config schema --output-format toml-template`",
        "# Uncomment and edit any line to override the default.",
        "",
    ]
    for name, prop in schema["properties"].items():
        tp = prop.get("type")
        default = prop.get("default", "")
        enum = prop.get("enum")
        note = ""
        if enum is not None:
            note = f"  # one of: {', '.join(enum)}"
        elif isinstance(tp, list) and "null" in tp:
            note = "  # nullable"
        rendered = _toml_value(default) if default != "" else '""'
        lines.append(f"# {name} = {rendered}{note}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Redaction allowlist (Sprint 0.3 ADR)
# ---------------------------------------------------------------------------

Classification = Literal["EXPOSE", "REDACT_TO_BOOL"]

#: Fail-closed allowlist: every non-skipped ServerConfig field must appear
#: here. Fields not listed are implicitly redacted. See
#: ``docs/design/info-endpoint-redaction.md`` for the rationale per field.
INFO_ALLOWLIST: dict[str, Classification] = {
    # Bind & workers
    "host": "REDACT_TO_BOOL",
    "port": "EXPOSE",
    "workers": "EXPOSE",
    "backlog": "EXPOSE",
    "worker_mode": "EXPOSE",
    "worker_startup_failure": "EXPOSE",
    "cpu_affinity": "EXPOSE",
    "executor_threads_per_worker": "EXPOSE",
    # Timeouts & limits
    "keep_alive_timeout": "EXPOSE",
    "request_timeout": "EXPOSE",
    "write_timeout": "EXPOSE",
    "header_timeout": "EXPOSE",
    "startup_timeout": "EXPOSE",
    "shutdown_timeout": "EXPOSE",
    "max_request_size": "EXPOSE",
    "max_header_size": "EXPOSE",
    "max_headers": "EXPOSE",
    "max_connections": "EXPOSE",
    "max_requests_per_connection": "EXPOSE",
    "h11_max_incomplete_event_size": "EXPOSE",
    "reload_timeout": "EXPOSE",
    # Logging
    "access_log": "EXPOSE",
    "log_level": "EXPOSE",
    "log_format": "EXPOSE",
    "lifecycle_logging": "EXPOSE",
    "log_slow_requests_threshold": "EXPOSE",
    # HTTP
    "http2_enabled": "EXPOSE",
    "server_header": "REDACT_TO_BOOL",
    "date_header": "EXPOSE",
    "root_path": "REDACT_TO_BOOL",
    "compression": "EXPOSE",
    "compression_min_size": "EXPOSE",
    "server_timing": "EXPOSE",
    # Development flags
    "debug": "EXPOSE",
    "reload": "EXPOSE",
    "reload_include": "REDACT_TO_BOOL",
    "reload_dirs": "REDACT_TO_BOOL",
    # Trust & networking
    "trusted_hosts": "REDACT_TO_BOOL",
    "trusted_hosts_wildcard": "EXPOSE",
    "forwarded_for_trusted_hops": "EXPOSE",
    "health_check_path": "REDACT_TO_BOOL",
    "uds": "REDACT_TO_BOOL",
    "uds_permissions": "EXPOSE",
    # TLS
    "ssl_certfile": "REDACT_TO_BOOL",
    "ssl_keyfile": "REDACT_TO_BOOL",
    # Static files
    "static_files": "REDACT_TO_BOOL",
    "static_cache_control": "EXPOSE",
    "static_precompressed": "EXPOSE",
    "static_follow_symlinks": "EXPOSE",
    "static_index_file": "EXPOSE",
    # WebSocket
    "websocket_compression": "EXPOSE",
    "websocket_max_message_size": "EXPOSE",
    # Observability
    "metrics_enabled": "EXPOSE",
    "metrics_path": "REDACT_TO_BOOL",
    "otel_endpoint": "REDACT_TO_BOOL",
    "otel_service_name": "EXPOSE",
    "sentry_dsn": "REDACT_TO_BOOL",
    "sentry_environment": "EXPOSE",
    "sentry_release": "EXPOSE",
    "sentry_traces_sample_rate": "EXPOSE",
    "sentry_profiles_sample_rate": "EXPOSE",
    # Rate limiting
    "rate_limit_enabled": "EXPOSE",
    "rate_limit_requests_per_second": "EXPOSE",
    "rate_limit_burst": "EXPOSE",
    "rate_limit_max_tracked_ips": "EXPOSE",
    "request_queue_enabled": "EXPOSE",
    "request_queue_max_depth": "EXPOSE",
    # HTTP/3
    "http3_enabled": "EXPOSE",
    "http3_max_connections": "EXPOSE",
    "http3_idle_timeout": "EXPOSE",
    "http3_qpack_max_table_capacity": "EXPOSE",
    "http3_zero_rtt_enabled": "EXPOSE",
    # Sprint 4 (introspection) — added when those fields exist
    "introspection_enabled": "EXPOSE",
    "introspection_bind": "REDACT_TO_BOOL",
    "introspection_path": "REDACT_TO_BOOL",
}


def _is_set(value: Any) -> bool:
    """Interpret ``REDACT_TO_BOOL`` — True when the field has a meaningful value."""
    if value is None:
        return False
    if isinstance(value, (str, bytes)) and len(value) == 0:
        return False
    return not (isinstance(value, (list, tuple, set, frozenset, dict)) and len(value) == 0)


def redacted_config_view(cfg: ServerConfig) -> dict[str, Any]:
    """Return a redacted dict view of *cfg* suitable for ``/info`` or ``config show``.

    EXPOSE fields keep their values; REDACT_TO_BOOL fields become
    ``<field>_set: bool``; every other field is omitted entirely.
    """
    out: dict[str, Any] = {}
    for f in dataclasses.fields(cfg):
        if f.name.startswith("_") or f.name in _IIC_SKIP_FIELDS:
            continue
        cls = INFO_ALLOWLIST.get(f.name)
        if cls is None:
            continue
        value = getattr(cfg, f.name)
        if cls == "EXPOSE":
            out[f.name] = _coerce_for_json(value)
        else:
            out[f"{f.name}_set"] = _is_set(value)
    return dict(sorted(out.items()))


def _coerce_for_json(value: Any) -> Any:
    """Coerce a ServerConfig value into JSON-serializable form."""
    if isinstance(value, (frozenset, set)):
        return sorted(value) if all(isinstance(x, str) for x in value) else list(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def assert_allowlist_covers_config() -> list[str]:
    """Return a list of ServerConfig fields missing from INFO_ALLOWLIST.

    Used by ``tests/unit/test_config_schema.py`` to guarantee that adding a
    new field to ServerConfig without updating the allowlist fails CI.
    """
    missing: list[str] = []
    for f in dataclasses.fields(ServerConfig):
        if f.name.startswith("_") or f.name in _IIC_SKIP_FIELDS:
            continue
        if f.name not in INFO_ALLOWLIST:
            missing.append(f.name)
    return missing
