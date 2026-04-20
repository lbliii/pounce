"""Tests for ``pounce._introspect`` — built-in ``/_pounce/info`` endpoint.

Mirrors ``tests/unit/test_health.py`` for the response-shape assertions.
Adds Sprint 5 invariants:

- Allowlist coverage: every ``REDACT_TO_BOOL`` field appears as ``<name>_set``,
  the raw secret-bearing field name does not appear verbatim. This is the
  fail-closed guarantee the redaction layer is supposed to provide.
- Non-loopback bind warning: ``Server._warn_if_introspection_public`` emits
  the catalogued ``POUNCE_CONFIG_INTROSPECTION_PUBLIC`` code; loopback +
  loopback stays silent.
"""

from __future__ import annotations

import json
import logging

import pytest

from pounce._config_schema import INFO_ALLOWLIST
from pounce._introspect import build_introspect_response
from pounce.config import ServerConfig


@pytest.fixture
def cfg() -> ServerConfig:
    """A ServerConfig with introspection on; secret-bearing fields populated.

    The secret-bearing fields are set so the redaction guarantee is
    *meaningfully* tested — if a field is None the check that its raw value
    doesn't leak is vacuously true.
    """
    return ServerConfig(
        host="127.0.0.1",
        port=8000,
        introspection_enabled=True,
        ssl_certfile="/etc/secret/cert.pem",
        ssl_keyfile="/etc/secret/key.pem",
        sentry_dsn="https://abc123@sentry.example.com/1",
        otel_endpoint="https://otel.internal/v1/traces",
        trusted_hosts=frozenset({"upstream.local"}),
    )


class TestBuildIntrospectResponse:
    def test_returns_200_status(self, cfg: ServerConfig) -> None:
        status, _headers, _body = build_introspect_response(
            config=cfg, worker_id=0, active_connections=0
        )
        assert status == 200

    def test_content_type_json(self, cfg: ServerConfig) -> None:
        _, headers, _ = build_introspect_response(
            config=cfg, worker_id=0, active_connections=0
        )
        header_dict = dict(headers)
        assert header_dict[b"content-type"] == b"application/json"

    def test_content_length_matches_body(self, cfg: ServerConfig) -> None:
        _, headers, body = build_introspect_response(
            config=cfg, worker_id=0, active_connections=0
        )
        header_dict = dict(headers)
        assert int(header_dict[b"content-length"]) == len(body)

    def test_no_cache_header(self, cfg: ServerConfig) -> None:
        _, headers, _ = build_introspect_response(
            config=cfg, worker_id=0, active_connections=0
        )
        header_dict = dict(headers)
        assert b"no-cache" in header_dict[b"cache-control"]

    def test_body_has_three_top_level_sections(self, cfg: ServerConfig) -> None:
        _, _, body = build_introspect_response(
            config=cfg, worker_id=2, active_connections=7
        )
        payload = json.loads(body)
        assert set(payload.keys()) == {"runtime", "worker", "config"}

    def test_runtime_section_exposes_python_and_gil(self, cfg: ServerConfig) -> None:
        _, _, body = build_introspect_response(
            config=cfg, worker_id=0, active_connections=0
        )
        payload = json.loads(body)
        runtime = payload["runtime"]
        assert "python_version" in runtime
        assert "gil_enabled" in runtime
        assert isinstance(runtime["gil_enabled"], bool)
        assert runtime["worker_mode"] == cfg.worker_mode
        assert runtime["uptime_seconds"] >= 0

    def test_worker_section_threads_identity(self, cfg: ServerConfig) -> None:
        _, _, body = build_introspect_response(
            config=cfg, worker_id=3, active_connections=11
        )
        payload = json.loads(body)
        assert payload["worker"] == {"worker_id": 3, "active_connections": 11}


class TestRedactionInvariants:
    """Fail-closed: secret-bearing fields are never echoed verbatim."""

    def test_redact_to_bool_fields_appear_as_suffix_set(self, cfg: ServerConfig) -> None:
        _, _, body = build_introspect_response(
            config=cfg, worker_id=0, active_connections=0
        )
        payload = json.loads(body)
        config_view = payload["config"]
        for name, classification in INFO_ALLOWLIST.items():
            if classification == "REDACT_TO_BOOL":
                # The redacted indicator must be present; the raw key must not.
                assert f"{name}_set" in config_view, (
                    f"REDACT_TO_BOOL field {name!r} missing from response"
                )
                assert name not in config_view, (
                    f"REDACT_TO_BOOL field {name!r} leaked its raw key"
                )

    def test_secret_values_never_appear_in_body(self, cfg: ServerConfig) -> None:
        _, _, body = build_introspect_response(
            config=cfg, worker_id=0, active_connections=0
        )
        text = body.decode("utf-8")
        # Any secret-bearing value set on the fixture must not surface.
        for needle in (
            cfg.ssl_certfile,
            cfg.ssl_keyfile,
            cfg.sentry_dsn,
            cfg.otel_endpoint,
            "upstream.local",  # trusted_hosts entry
        ):
            assert needle is not None
            assert needle not in text, (
                f"secret-bearing value {needle!r} leaked in introspect body"
            )

    def test_no_omitted_field_appears(self, cfg: ServerConfig) -> None:
        # Any ServerConfig field that's neither EXPOSE nor REDACT_TO_BOOL must
        # be entirely omitted. The allowlist is fail-closed today, so this
        # asserts the property continues to hold for any future skipped field.
        import dataclasses

        from pounce.config import _IIC_SKIP_FIELDS

        _, _, body = build_introspect_response(
            config=cfg, worker_id=0, active_connections=0
        )
        config_view = json.loads(body)["config"]
        for f in dataclasses.fields(cfg):
            if f.name.startswith("_") or f.name in _IIC_SKIP_FIELDS:
                continue
            if INFO_ALLOWLIST.get(f.name) is None:
                assert f.name not in config_view
                assert f"{f.name}_set" not in config_view


class TestPublicBindWarning:
    """Server._warn_if_introspection_public emits the catalogued code only when needed."""

    def _server_with(self, **cfg_kwargs: object) -> object:
        from pounce.server import Server

        async def _app(scope: dict, receive: object, send: object) -> None:
            return None

        config = ServerConfig(**cfg_kwargs)  # type: ignore[arg-type]
        return Server(config, _app)  # type: ignore[arg-type]

    def test_silent_when_disabled(self, caplog: pytest.LogCaptureFixture) -> None:
        srv = self._server_with(introspection_enabled=False, host="0.0.0.0")
        with caplog.at_level(logging.WARNING, logger="pounce"):
            srv._warn_if_introspection_public()  # type: ignore[attr-defined]
        assert "POUNCE_CONFIG_INTROSPECTION_PUBLIC" not in caplog.text

    def test_silent_on_full_loopback(self, caplog: pytest.LogCaptureFixture) -> None:
        srv = self._server_with(
            introspection_enabled=True, host="127.0.0.1", introspection_bind="127.0.0.1"
        )
        with caplog.at_level(logging.WARNING, logger="pounce"):
            srv._warn_if_introspection_public()  # type: ignore[attr-defined]
        assert "POUNCE_CONFIG_INTROSPECTION_PUBLIC" not in caplog.text

    def test_warns_on_public_host(self, caplog: pytest.LogCaptureFixture) -> None:
        srv = self._server_with(
            introspection_enabled=True, host="0.0.0.0", introspection_bind="127.0.0.1"
        )
        with caplog.at_level(logging.WARNING, logger="pounce"):
            srv._warn_if_introspection_public()  # type: ignore[attr-defined]
        assert "POUNCE_CONFIG_INTROSPECTION_PUBLIC" in caplog.text

    def test_warns_on_public_introspection_bind(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        srv = self._server_with(
            introspection_enabled=True, host="127.0.0.1", introspection_bind="0.0.0.0"
        )
        with caplog.at_level(logging.WARNING, logger="pounce"):
            srv._warn_if_introspection_public()  # type: ignore[attr-defined]
        assert "POUNCE_CONFIG_INTROSPECTION_PUBLIC" in caplog.text


class TestServerConfigDefaults:
    """The new fields have safe defaults and validation."""

    def test_defaults_are_safe(self) -> None:
        cfg = ServerConfig()
        assert cfg.introspection_enabled is False
        assert cfg.introspection_bind == "127.0.0.1"
        assert cfg.introspection_path == "/_pounce/info"

    def test_path_must_start_with_slash(self) -> None:
        with pytest.raises(ValueError, match="introspection_path"):
            ServerConfig(introspection_path="info")

    def test_bind_must_be_non_empty(self) -> None:
        with pytest.raises(ValueError, match="introspection_bind"):
            ServerConfig(introspection_bind="")
