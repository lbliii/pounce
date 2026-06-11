"""Tests for ``info_panel`` field parity and the non-TTY (piped) output path.

These cover issue #156: ``pounce info`` previously routed its non-TTY branch
through ``logger.info``, which is silently dropped when no handler is installed
(the common piped/redirected automation case). They also pin the field parity
contract across pretty/text/json modes (install_path, frameworks, worker model).
"""

from __future__ import annotations

import json
import sys

import pytest

from pounce import _output

_BASE_KWARGS = {
    "version": "1.2.3",
    "python_version": "3.14.0",
    "platform_str": "macOS-15.3-arm64",
    "cpu_count": 8,
    "gil_status": "nogil",
    "install_path": "/opt/pounce/src/pounce",
    "deps": [
        {"name": "HTTP/2 (h2)", "installed": True, "version": "4.1.0", "hint": "pip install x"},
        {"name": "WebSocket (wsproto)", "installed": False, "version": "", "hint": "pip install y"},
    ],
    "frameworks": ["FastAPI 0.110.0"],
    "worker_model": "thread (sync)",
    "worker_count": 8,
}


class TestDetectWorkerModel:
    """``detect_worker_model`` returns a strategy + execution descriptor."""

    def test_returns_nonempty_string(self):
        model = _output.detect_worker_model()
        assert isinstance(model, str)
        assert model  # non-empty

    def test_describes_mode_and_execution(self):
        # Shape is "<mode> (<execution>)" e.g. "process (async)" / "thread (sync)".
        model = _output.detect_worker_model()
        assert "(" in model
        assert ")" in model
        mode = model.split(" (", 1)[0]
        assert mode in {"thread", "process", "subinterpreter"}


class TestInfoPanelNonTTYWritesStdout:
    """The non-TTY branch must write to stdout, not a dropped logger."""

    def test_piped_text_output_is_not_empty(self, mocker, capsys):
        # Force the non-pretty branch: not a TTY, no FORCE_COLOR, _is_pretty False.
        mocker.patch.object(_output, "_is_pretty", return_value=False)
        mocker.patch.object(sys.stderr, "isatty", return_value=False)
        mocker.patch.dict("os.environ", {}, clear=False)

        _output.info_panel(output_format="text", **_BASE_KWARGS)

        captured = capsys.readouterr()
        # The bug: logger.info dropped everything. The fix writes to stdout.
        assert captured.out, "info_panel produced no stdout in non-TTY text mode"
        assert "pounce v1.2.3" in captured.out

    def test_piped_text_includes_install_path_frameworks_worker_model(self, mocker, capsys):
        mocker.patch.object(_output, "_is_pretty", return_value=False)
        mocker.patch.object(sys.stderr, "isatty", return_value=False)

        _output.info_panel(output_format="text", **_BASE_KWARGS)

        out = capsys.readouterr().out
        # Field parity in the plain/text branch — these were missing before.
        assert "/opt/pounce/src/pounce" in out, "install_path missing from text output"
        assert "FastAPI 0.110.0" in out, "frameworks missing from text output"
        assert "thread (sync)" in out, "worker model missing from text output"


class TestInfoPanelJSON:
    """JSON mode emits a stable, machine-readable dict to stdout."""

    def test_json_is_valid_and_on_stdout(self, capsys):
        _output.info_panel(output_format="json", **_BASE_KWARGS)
        captured = capsys.readouterr()
        assert captured.out, "json mode produced no stdout"
        payload = json.loads(captured.out)
        assert payload["version"] == "1.2.3"

    def test_json_has_all_contract_fields(self, capsys):
        _output.info_panel(output_format="json", **_BASE_KWARGS)
        payload = json.loads(capsys.readouterr().out)
        for field in (
            "version",
            "python_version",
            "platform",
            "cpu_count",
            "gil_status",
            "install_path",
            "deps",
            "frameworks",
            "worker_model",
            "worker_count",
        ):
            assert field in payload, f"json output missing field: {field}"
        assert payload["install_path"] == "/opt/pounce/src/pounce"
        assert payload["frameworks"] == ["FastAPI 0.110.0"]
        assert payload["worker_model"] == "thread (sync)"


class TestInfoFieldParity:
    """The three render modes must cover the same load-bearing fields."""

    @pytest.mark.parametrize("output_format", ["text", "json"])
    def test_parity_install_path_frameworks_worker_model(self, mocker, capsys, output_format):
        # Force the non-TTY text branch for the "text" case so we exercise the
        # plain renderer, not the kida template.
        mocker.patch.object(_output, "_is_pretty", return_value=False)
        mocker.patch.object(sys.stderr, "isatty", return_value=False)

        _output.info_panel(output_format=output_format, **_BASE_KWARGS)
        out = capsys.readouterr().out
        # Each mode must surface install path, framework, and worker model.
        assert "/opt/pounce/src/pounce" in out
        assert "FastAPI 0.110.0" in out
        assert "thread (sync)" in out

    def test_pretty_template_covers_the_same_fields(self):
        # The pretty (kida) branch renders via a template; confirm it includes
        # the parity fields so all three modes agree.
        rendered = _output._render(
            "info.kida",
            version="1.2.3",
            python_version="3.14.0",
            platform="macOS-15.3-arm64",
            cpu_count=8,
            gil_status="nogil",
            install_path="/opt/pounce/src/pounce",
            deps=[],
            frameworks=["FastAPI 0.110.0"],
            worker_model="thread (sync)",
            worker_count=8,
        )
        assert "/opt/pounce/src/pounce" in rendered
        assert "FastAPI 0.110.0" in rendered
        assert "thread (sync)" in rendered
