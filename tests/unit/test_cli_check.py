"""Tests for the 'pounce check' command and pre-flight validators."""

import pytest

from pounce._cli import (
    _check_app_importable,
    _check_config_valid,
    _check_deps_for_config,
    _check_port_available,
    _check_signage,
    _check_tls_cert,
    cli,
)


class TestCheckAppImportable:
    """_check_app_importable validates app import paths."""

    def test_valid_app(self, mocker):
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        result = _check_app_importable("myapp:app")
        assert result["status"] == "success"
        assert result["detail"] == "myapp:app"

    def test_invalid_app_import_error(self, mocker):
        mocker.patch("pounce._cli.import_app", side_effect=ImportError("No module named 'myapp'"))
        result = _check_app_importable("myapp:app")
        assert result["status"] == "error"
        assert "No module named" in result["detail"]

    def test_invalid_app_value_error(self, mocker):
        mocker.patch(
            "pounce._cli.import_app", side_effect=ValueError("Expected format 'module:attr'")
        )
        result = _check_app_importable("badformat")
        assert result["status"] == "error"


class TestCheckPortAvailable:
    """_check_port_available checks socket binding."""

    def test_available_port(self, mocker):
        mock_sock = mocker.MagicMock()
        mocker.patch("socket.socket", return_value=mock_sock)
        result = _check_port_available("127.0.0.1", 18999)
        assert result["status"] == "success"

    def test_port_in_use(self, mocker):
        mock_sock = mocker.MagicMock()
        mock_sock.bind.side_effect = OSError(48, "Address already in use")
        mocker.patch("socket.socket", return_value=mock_sock)
        result = _check_port_available("127.0.0.1", 8000)
        assert result["status"] == "error"
        assert "already in use" in result["detail"].lower() or "Address" in result["detail"]


class TestCheckTlsCert:
    """_check_tls_cert validates TLS certificate files."""

    def test_missing_cert(self, tmp_path):
        result = _check_tls_cert(str(tmp_path / "nonexistent.pem"))
        assert result["status"] == "error"
        assert "not found" in result["detail"]

    def test_not_a_file(self, tmp_path):
        result = _check_tls_cert(str(tmp_path))
        assert result["status"] == "error"
        assert "not a file" in result["detail"]

    def test_invalid_cert_content(self, tmp_path):
        cert = tmp_path / "bad.pem"
        cert.write_text("not a cert")
        result = _check_tls_cert(str(cert))
        assert result["status"] == "error"


class TestCheckDepsForConfig:
    """_check_deps_for_config validates optional deps for features."""

    def test_no_features(self):
        result = _check_deps_for_config(http3=False, ssl_certfile=None)
        assert result == []

    def test_http3_missing(self, mocker):
        mocker.patch("pounce._output.probe_optional_dep", return_value=(False, ""))
        result = _check_deps_for_config(http3=True, ssl_certfile=None)
        assert len(result) == 1
        assert result[0]["status"] == "error"
        assert "bengal-zoomies" in result[0]["detail"]

    def test_http3_installed(self, mocker):
        mocker.patch("pounce._output.probe_optional_dep", return_value=(True, "0.1.1"))
        result = _check_deps_for_config(http3=True, ssl_certfile=None)
        assert len(result) == 1
        assert result[0]["status"] == "success"


class TestCheckConfigValid:
    """_check_config_valid constructs ServerConfig to validate."""

    def test_valid_config(self):
        result = _check_config_valid(
            host="127.0.0.1",
            port=8000,
            workers=1,
            worker_mode="auto",
            cpu_affinity=False,
            log_level="info",
            log_format="auto",
            root_path="",
            no_compression=False,
            server_timing=False,
            no_access_log=False,
            ssl_certfile=None,
            ssl_keyfile=None,
            http3=False,
            reload=False,
            reload_include=None,
            reload_dir=None,
            keep_alive_timeout=5.0,
            header_timeout=10.0,
            request_timeout=30.0,
            startup_timeout=30.0,
            max_requests_per_connection=0,
            shutdown_timeout=10.0,
            uds=None,
            health_check_path=None,
        )
        assert result["status"] == "success"

    def test_invalid_config(self):
        result = _check_config_valid(
            host="127.0.0.1",
            port=-1,
            workers=1,
            worker_mode="auto",
            cpu_affinity=False,
            log_level="info",
            log_format="auto",
            root_path="",
            no_compression=False,
            server_timing=False,
            no_access_log=False,
            ssl_certfile=None,
            ssl_keyfile=None,
            http3=False,
            reload=False,
            reload_include=None,
            reload_dir=None,
            keep_alive_timeout=5.0,
            header_timeout=10.0,
            request_timeout=30.0,
            startup_timeout=30.0,
            max_requests_per_connection=0,
            shutdown_timeout=10.0,
            uds=None,
            health_check_path=None,
        )
        assert result["status"] == "error"


class TestCheckSignage:
    """_check_signage validates signage values."""

    def test_valid_signage(self):
        assert _check_signage("full")["status"] == "success"
        assert _check_signage("minimal")["status"] == "success"
        assert _check_signage("off")["status"] == "success"

    def test_invalid_signage(self):
        result = _check_signage("INVALID")
        assert result["status"] == "error"
        assert "INVALID" in result["detail"]
        assert "full" in result["hint"]


class TestCheckCommand:
    """The 'check' command runs all validators and renders results."""

    def test_check_all_pass(self, mocker):
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        mock_results = mocker.patch("pounce._output.check_results")
        # Mock the socket check to succeed
        mock_sock = mocker.MagicMock()
        mocker.patch("socket.socket", return_value=mock_sock)

        cli.call("check", app="myapp:app")
        mock_results.assert_called_once()
        kwargs = mock_results.call_args[1]
        assert kwargs["all_passed"] is True

    def test_check_app_fails_exits_1(self, mocker):
        mocker.patch("pounce._cli.import_app", side_effect=ImportError("no module"))
        mocker.patch("pounce._output.check_results")
        mock_sock = mocker.MagicMock()
        mocker.patch("socket.socket", return_value=mock_sock)

        with pytest.raises(SystemExit) as exc_info:
            cli.call("check", app="badapp:app")
        assert exc_info.value.code == 1


class TestCheckTemplate:
    """The check.kida template renders without errors."""

    def test_template_renders_pass(self):
        from pounce._output import _render

        result = _render(
            "check.kida",
            version="0.4.0",
            checks=[
                {"name": "App import", "status": "success", "detail": "myapp:app", "hint": ""},
                {
                    "name": "Port available",
                    "status": "success",
                    "detail": "127.0.0.1:8000",
                    "hint": "",
                },
            ],
            all_passed=True,
        )
        assert "pounce check" in result
        assert "All checks passed" in result

    def test_template_renders_fail(self):
        from pounce._output import _render

        result = _render(
            "check.kida",
            version="0.4.0",
            checks=[
                {
                    "name": "App import",
                    "status": "error",
                    "detail": "module not found",
                    "hint": "Check path",
                },
            ],
            all_passed=False,
        )
        assert "pounce check" in result
        assert "Fix the issues" in result
