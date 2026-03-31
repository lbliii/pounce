"""Tests for the 'pounce info' command and dependency probing."""

from pounce._cli import cli
from pounce._output import (
    detect_frameworks,
    probe_all_optional_deps,
    probe_optional_dep,
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
        )
        assert "pounce info" in result
