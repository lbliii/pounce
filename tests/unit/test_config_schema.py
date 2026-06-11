"""Tests for :mod:`pounce._config_schema`.

Covers three contracts:

1. ``build_schema`` emits a JSON Schema Draft 2020-12 document with
   deterministic (sorted) keys. A field-count snapshot guards against
   accidentally dropping fields from the generator.
2. ``INFO_ALLOWLIST`` covers every non-skipped ``ServerConfig`` field.
   Adding a field without classifying it is a CI failure — this is the
   fail-closed contract from the Sprint 0.3 ADR.
3. ``redacted_config_view`` never emits the raw value of a
   ``REDACT_TO_BOOL`` field, even when the value contains a secret-ish
   substring.
"""

from __future__ import annotations

import json

from pounce._config_schema import (
    _BETA_FIELD_PREFIXES,
    INFO_ALLOWLIST,
    _field_stability,
    assert_allowlist_covers_config,
    build_schema,
    build_toml_template,
    redacted_config_view,
)
from pounce.config import ServerConfig


class TestBuildSchema:
    def test_root_shape(self) -> None:
        schema = build_schema()
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["title"] == "ServerConfig"
        assert schema["additionalProperties"] is False

    def test_properties_sorted(self) -> None:
        schema = build_schema()
        keys = list(schema["properties"])
        assert keys == sorted(keys)

    def test_known_shapes(self) -> None:
        p = build_schema()["properties"]
        assert p["host"] == {"type": "string", "default": "127.0.0.1"}
        assert p["port"] == {"type": "integer", "default": 8000}
        assert p["compression"] == {"type": "boolean", "default": True}
        assert p["log_level"]["enum"] == [
            "critical",
            "debug",
            "error",
            "info",
            "warning",
        ]
        # Nullable fields carry both type + null
        assert p["ssl_certfile"]["type"] == ["string", "null"]

    def test_callable_fields_dropped(self) -> None:
        p = build_schema()["properties"]
        # From _IIC_SKIP_FIELDS
        assert "middleware" not in p
        assert "access_log_filter" not in p

    def test_field_count_snapshot(self) -> None:
        """If this number changes, something was added to ServerConfig.

        Intentional additions: update the snapshot AND extend INFO_ALLOWLIST.
        """
        assert len(build_schema()["properties"]) == 73

    def test_output_is_valid_json(self) -> None:
        # Round-tripping catches any non-serializable default values.
        schema = build_schema()
        encoded = json.dumps(schema, sort_keys=False, default=str)
        assert json.loads(encoded) == schema


class TestStabilityTiers:
    """Stability tier surfacing (issue #157).

    Every ServerConfig field has a stability tier, and beta-tier fields carry
    an ``x-stability`` annotation in the generated schema. Stable fields must
    NOT carry it (they are the production-safe surface).
    """

    def test_every_field_has_a_classification(self) -> None:
        # Fail-closed: a new field is classified by _field_stability (stable by
        # default, beta if it matches a known beta prefix). The function must
        # return a known tier for every schema field.
        for name in build_schema()["properties"]:
            assert _field_stability(name) in {"stable", "beta"}, name

    def test_beta_fields_are_annotated(self) -> None:
        props = build_schema()["properties"]
        # Representative beta fields gain x-stability=beta and a note.
        for name in ("rate_limit_enabled", "request_queue_max_depth", "sentry_dsn"):
            assert props[name].get("x-stability") == "beta", name
            assert "beta" in props[name].get("description", ""), name

    def test_all_beta_prefix_fields_annotated(self) -> None:
        props = build_schema()["properties"]
        for name, prop in props.items():
            if name.startswith(_BETA_FIELD_PREFIXES):
                assert prop.get("x-stability") == "beta", name

    def test_stable_fields_have_no_stability_annotation(self) -> None:
        props = build_schema()["properties"]
        # Core stable knobs must stay clean — they are the contract surface.
        for name in ("host", "port", "workers", "log_level", "request_timeout"):
            assert "x-stability" not in props[name], name

    def test_worker_mode_marks_subinterpreter_beta(self) -> None:
        wm = build_schema()["properties"]["worker_mode"]
        # The field is stable, but the subinterpreter VALUE is beta.
        assert wm.get("x-stability-values", {}).get("subinterpreter") == "beta"
        assert "subinterpreter" in wm.get("description", "")
        # auto/sync/async remain offered without a beta value marker.
        assert "x-stability" not in wm


class TestBuildTomlTemplate:
    def test_template_has_no_section_header(self) -> None:
        # pounce.toml uses top-level keys — see _config_file._VALID_KEYS.
        out = build_toml_template()
        assert "[pounce]" not in out

    def test_every_field_commented(self) -> None:
        out = build_toml_template()
        for name in build_schema()["properties"]:
            assert f"# {name} =" in out, name

    def test_enum_hints(self) -> None:
        out = build_toml_template()
        assert "one of:" in out  # log_level / log_format / worker_mode


class TestRedactionAllowlist:
    def test_allowlist_covers_every_config_field(self) -> None:
        """Fail-closed: every field must be classified. See ADR 0.3."""
        missing = assert_allowlist_covers_config()
        assert missing == [], f"Add these fields to INFO_ALLOWLIST: {missing}"

    def test_known_redactions(self) -> None:
        assert INFO_ALLOWLIST["ssl_certfile"] == "REDACT_TO_BOOL"
        assert INFO_ALLOWLIST["sentry_dsn"] == "REDACT_TO_BOOL"
        assert INFO_ALLOWLIST["uds"] == "REDACT_TO_BOOL"
        assert INFO_ALLOWLIST["host"] == "REDACT_TO_BOOL"

    def test_known_exposures(self) -> None:
        assert INFO_ALLOWLIST["port"] == "EXPOSE"
        assert INFO_ALLOWLIST["workers"] == "EXPOSE"
        assert INFO_ALLOWLIST["debug"] == "EXPOSE"
        assert INFO_ALLOWLIST["log_level"] == "EXPOSE"


class TestRedactedConfigView:
    def test_secrets_never_appear(self) -> None:
        """Canary regression — secret-ish substrings must not appear in output."""
        # ssl_certfile and ssl_keyfile must both be set (ServerConfig invariant);
        # uds and TCP bind are mutually exclusive, so leave uds unset here.
        cfg = ServerConfig(
            ssl_certfile="/CANARY/cert.pem",
            ssl_keyfile="/CANARY/key.pem",
            sentry_dsn="https://CANARY@o.ingest.sentry.io/1",
        )
        view = redacted_config_view(cfg)
        blob = json.dumps(view)
        assert "CANARY" not in blob, view

    def test_redact_to_bool_emits_set_suffix(self) -> None:
        cfg = ServerConfig(
            ssl_certfile="/etc/ssl/cert.pem",
            ssl_keyfile="/etc/ssl/key.pem",
        )
        view = redacted_config_view(cfg)
        # Raw field is omitted; replaced by <name>_set
        assert "ssl_certfile" not in view
        assert view["ssl_certfile_set"] is True
        assert view["ssl_keyfile_set"] is True

    def test_redact_to_bool_is_false_when_unset(self) -> None:
        cfg = ServerConfig()
        view = redacted_config_view(cfg)
        assert view["ssl_certfile_set"] is False
        assert view["uds_set"] is False
        assert view["sentry_dsn_set"] is False

    def test_exposed_fields_pass_through(self) -> None:
        cfg = ServerConfig(port=9001, workers=4, debug=True)
        view = redacted_config_view(cfg)
        assert view["port"] == 9001
        assert view["workers"] == 4
        assert view["debug"] is True

    def test_view_is_sorted(self) -> None:
        view = redacted_config_view(ServerConfig())
        keys = list(view)
        assert keys == sorted(keys)

    def test_unknown_fields_dropped(self) -> None:
        """Fields outside INFO_ALLOWLIST never appear, even if set."""
        # Every key in the view must map either to an allowlist EXPOSE name
        # or to an `<allowlist-name>_set` suffix form.
        view = redacted_config_view(ServerConfig())
        for key in view:
            base = key.removesuffix("_set")
            assert base in INFO_ALLOWLIST, key
