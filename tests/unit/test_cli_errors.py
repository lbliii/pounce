"""Tests for ``pounce._cli._die`` — the CLI's branded error exit path.

Sprint 1 of the vibe-readiness epic plumbs ``code`` and ``doc`` (the
``PounceError`` semantic code and troubleshooting anchor) through the CLI
error printout. These tests pin the contract: ``_die`` accepts both kwargs,
forwards them to the rendering layer, and the rendered output contains the
code so an agent reading stderr can grep for ``POUNCE_*``.
"""

from __future__ import annotations

import io
import sys

import pytest

from pounce import _cli, _output
from pounce._errors import TLSError


def _capture_die(monkeypatch: pytest.MonkeyPatch, **kwargs: object) -> tuple[int, str]:
    """Invoke ``_die`` and return ``(exit_code, stderr_text)``.

    Forces non-pretty mode so the test captures plain-text output rather
    than ANSI-coloured kida output.
    """
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    # Replace stderr so the kida path (if it fires) writes somewhere we read.
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stderr", buf)
    monkeypatch.setattr(buf, "isatty", lambda: False, raising=False)
    monkeypatch.setattr(_output, "_is_pretty", lambda: False)

    with pytest.raises(SystemExit) as exit_info:
        _cli._die(**kwargs)  # type: ignore[arg-type]

    code = exit_info.value.code if isinstance(exit_info.value.code, int) else 1
    return code, buf.getvalue()


class TestDieAcceptsCodeAndDoc:
    """``_die`` accepts ``code`` and ``doc`` kwargs without raising."""

    def test_die_accepts_code_kwarg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        exit_code, stderr = _capture_die(
            monkeypatch,
            message="boom",
            code="POUNCE_TLS_CERT_MISSING",
        )
        assert exit_code == 1
        assert "boom" in stderr
        assert "POUNCE_TLS_CERT_MISSING" in stderr

    def test_die_accepts_doc_kwarg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        exit_code, stderr = _capture_die(
            monkeypatch,
            message="boom",
            doc="docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING",
        )
        assert exit_code == 1
        assert "boom" in stderr
        assert "docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING" in stderr

    def test_die_accepts_all_optional_kwargs_together(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        exit_code, stderr = _capture_die(
            monkeypatch,
            message="cert missing",
            hint="Pass --ssl-certfile=PATH",
            code="POUNCE_TLS_CERT_MISSING",
            doc="docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING",
        )
        assert exit_code == 1
        assert "cert missing" in stderr
        assert "Pass --ssl-certfile=PATH" in stderr

    def test_die_backward_compatible_message_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Existing call sites without code/doc continue to work.
        exit_code, stderr = _capture_die(monkeypatch, message="oops")
        assert exit_code == 1
        assert "oops" in stderr


class TestDieForwardsToOutputError:
    """``_die`` passes ``code`` and ``doc`` through to ``_output.error``."""

    def test_code_forwarded_as_code_kwarg(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        def fake_error(message: str, **kwargs: object) -> None:
            captured["message"] = message
            captured.update(kwargs)

        monkeypatch.setattr(_output, "error", fake_error)
        with pytest.raises(SystemExit):
            _cli._die(
                "boom",
                code="POUNCE_TLS_CERT_MISSING",
                doc="docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING",
            )
        assert captured["message"] == "boom"
        assert captured["code"] == "POUNCE_TLS_CERT_MISSING"
        assert captured["docs_url"] == "docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING"


class TestPounceErrorRoundsTripThroughDie:
    """A ``PounceError`` raised in serve setup surfaces its ``code`` to stderr.

    Pretty mode renders the code via ``error.kida``. The plain-text fallback
    is more terse (no code line today) but the kwarg must always reach the
    renderer — covered by ``test_code_forwarded_as_code_kwarg``.
    """

    def test_pounce_error_has_required_fields(self) -> None:
        # The catch site at _cli.py:278 reads exc.code, exc.hint, exc.doc.
        # This test pins those attributes exist on the canonical subclass.
        err = TLSError(
            "cert missing",
            code="POUNCE_TLS_CERT_MISSING",
            hint="Pass --ssl-certfile=PATH",
            doc="docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING",
        )
        assert err.code == "POUNCE_TLS_CERT_MISSING"
        assert err.hint == "Pass --ssl-certfile=PATH"
        assert err.doc == "docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING"

    def test_pounce_error_with_no_explicit_hint_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # When exc.hint is None, _die's caller falls back to
        # _hint_for_pounce_error. The default-code fallback still surfaces,
        # and exc.doc is auto-derived from the code so the See: line resolves
        # without the raise site having to pass doc= explicitly.
        captured: dict[str, object] = {}

        def fake_error(message: str, **kwargs: object) -> None:
            captured.update({"message": message, **kwargs})

        monkeypatch.setattr(_output, "error", fake_error)
        err = TLSError("plain message")  # uses default_code, no hint, no doc
        with pytest.raises(SystemExit):
            _cli._die(
                str(err),
                hint=err.hint or _cli._hint_for_pounce_error(err),
                code=err.code,
                doc=err.doc,
            )
        assert captured["code"] == "POUNCE_TLS_E"
        assert captured["docs_url"] == "docs/troubleshooting.md#POUNCE_TLS_E"
