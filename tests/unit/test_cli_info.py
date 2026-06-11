"""Tests for the 'pounce info' command and dependency probing."""

from __future__ import annotations

import json
import subprocess
import sys

from pounce._cli import cli
from pounce._output import (
    detect_frameworks,
    probe_all_optional_deps,
    probe_optional_dep,
)


def _run_info(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke ``pounce info`` in a subprocess with captured (non-TTY) pipes."""
    return subprocess.run(
        [sys.executable, "-m", "pounce", "info", *args],
        capture_output=True,
        text=True,
        env={"NO_COLOR": "1", "PATH": ""},
        check=False,
    )


class TestProbeOptionalDep:
    """probe_optional_dep detects installed/missing modules."""

    def test_installed_module(self):
        installed, _version = probe_optional_dep("sys")
        assert installed is True

    def test_missing_module(self):
        installed, version = probe_optional_dep("nonexistent_module_xyz")
        assert installed is False
        assert version == ""

    def test_module_with_version(self, mocker):
        fake = mocker.MagicMock()
        fake.__version__ = "1.2.3"
        mocker.patch("builtins.__import__", return_value=fake)
        installed, version = probe_optional_dep("fake_mod")
        assert installed is True
        assert version == "1.2.3"


class TestProbeAllOptionalDeps:
    """probe_all_optional_deps returns structured results for all deps."""

    def test_returns_list(self):
        result = probe_all_optional_deps()
        assert isinstance(result, list)
        assert len(result) == 4
        for dep in result:
            assert "name" in dep
            assert "installed" in dep
            assert "version" in dep
            assert "hint" in dep


class TestDetectFrameworks:
    """detect_frameworks finds installed ASGI frameworks."""

    def test_returns_list(self):
        result = detect_frameworks()
        assert isinstance(result, list)

    def test_detects_installed_framework(self, mocker):
        fake = mocker.MagicMock()
        fake.__version__ = "0.100.0"
        original_import = (
            __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__
        )

        def side_effect(name, *args, **kwargs):
            if name == "starlette":
                return fake
            return original_import(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=side_effect)
        result = detect_frameworks()
        assert any("Starlette" in fw for fw in result)


class TestInfoCommand:
    """The 'info' command renders the system diagnostic panel."""

    def test_info_calls_info_panel(self, mocker):
        mock_panel = mocker.patch("pounce._output.info_panel")
        cli.call("info")
        mock_panel.assert_called_once()
        kwargs = mock_panel.call_args[1]
        assert "version" in kwargs
        assert "python_version" in kwargs
        assert "platform_str" in kwargs
        assert "cpu_count" in kwargs
        assert "gil_status" in kwargs
        assert "install_path" in kwargs
        assert "deps" in kwargs
        assert "frameworks" in kwargs
        assert "worker_model" in kwargs
        assert "worker_count" in kwargs

    def test_info_default_output_format_is_text(self, mocker):
        mock_panel = mocker.patch("pounce._output.info_panel")
        cli.call("info")
        assert mock_panel.call_args[1]["output_format"] == "text"

    def test_info_passes_json_output_format(self, mocker):
        mock_panel = mocker.patch("pounce._output.info_panel")
        cli.call("info", output_format="json")
        assert mock_panel.call_args[1]["output_format"] == "json"

    def test_info_panel_cpu_count_positive(self, mocker):
        mock_panel = mocker.patch("pounce._output.info_panel")
        cli.call("info")
        assert mock_panel.call_args[1]["cpu_count"] >= 1


class TestInfoTemplate:
    """The info.kida template renders without errors."""

    def test_template_renders(self):
        from pounce._output import _render

        result = _render(
            "info.kida",
            version="0.4.0",
            python_version="3.14.0",
            platform="macOS-15.3-arm64",
            cpu_count=8,
            gil_status="nogil",
            install_path="/usr/lib/pounce",
            deps=[
                {"name": "HTTP/2 (h2)", "installed": True, "version": "4.1.0", "hint": ""},
                {
                    "name": "WebSocket (wsproto)",
                    "installed": False,
                    "version": "",
                    "hint": "pip install pounce[ws]",
                },
            ],
            frameworks=["FastAPI 0.100.0"],
            worker_model="thread (sync)",
            worker_count=8,
        )
        assert "pounce info" in result
        assert "0.4.0" in result

    def test_template_renders_no_frameworks(self):
        from pounce._output import _render

        result = _render(
            "info.kida",
            version="0.4.0",
            python_version="3.14.0",
            platform="Linux",
            cpu_count=4,
            gil_status="GIL",
            install_path="/usr/lib/pounce",
            deps=[],
            frameworks=[],
            worker_model="process (async)",
            worker_count=4,
        )
        assert "pounce info" in result


class TestInfoPipedSubprocess:
    """End-to-end: ``pounce info`` must produce real piped output (issue #156).

    These invoke the CLI in a subprocess so stdout is a non-TTY pipe — the exact
    case where the old ``logger.info`` branch produced zero bytes.
    """

    def test_piped_text_stdout_not_empty(self):
        result = _run_info()
        assert result.returncode == 0, result.stderr
        # The regression fence: piped stdout used to be empty.
        assert result.stdout.strip(), (
            "pounce info | cat produced empty stdout:\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        assert "pounce v" in result.stdout

    def test_piped_text_has_install_path_frameworks_worker_model(self):
        result = _run_info()
        assert result.returncode == 0, result.stderr
        out = result.stdout
        assert "Install path:" in out, "install_path missing from piped text output"
        assert "Worker model:" in out, "worker model missing from piped text output"
        assert "Frameworks:" in out, "frameworks line missing from piped text output"

    def test_piped_json_is_valid_with_contract_fields(self):
        result = _run_info("--output-format", "json")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip(), "json mode produced empty stdout"
        payload = json.loads(result.stdout)
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
