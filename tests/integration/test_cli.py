"""Integration tests for pounce._cli — command-line interface."""

import pytest

from pounce._cli import _build_parser, main


class TestCLIParser:
    """Argument parser produces correct defaults and overrides."""

    def test_default_args(self):
        parser = _build_parser()
        parsed = parser.parse_args(["myapp:app"])
        assert parsed.app == "myapp:app"
        assert parsed.host == "127.0.0.1"
        assert parsed.port == 8000
        assert parsed.workers == 1
        assert parsed.log_level == "info"
        assert parsed.root_path == ""
        assert parsed.no_compression is False
        assert parsed.server_timing is False
        assert parsed.no_access_log is False
        assert parsed.ssl_certfile is None
        assert parsed.ssl_keyfile is None
        assert parsed.reload is False
        assert parsed.keep_alive_timeout == 5.0
        assert parsed.max_requests_per_connection == 0

    def test_custom_args(self):
        parser = _build_parser()
        parsed = parser.parse_args(
            [
                "myapp.web:create_app()",
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--workers",
                "4",
                "--log-level",
                "debug",
                "--root-path",
                "/api",
                "--no-compression",
                "--server-timing",
                "--no-access-log",
            ]
        )
        assert parsed.app == "myapp.web:create_app()"
        assert parsed.host == "0.0.0.0"
        assert parsed.port == 9000
        assert parsed.workers == 4
        assert parsed.log_level == "debug"
        assert parsed.root_path == "/api"
        assert parsed.no_compression is True
        assert parsed.server_timing is True
        assert parsed.no_access_log is True

    def test_missing_app_exits(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])

    def test_phase3_tls_args(self):
        parser = _build_parser()
        parsed = parser.parse_args(
            [
                "myapp:app",
                "--ssl-certfile",
                "/path/to/cert.pem",
                "--ssl-keyfile",
                "/path/to/key.pem",
            ]
        )
        assert parsed.ssl_certfile == "/path/to/cert.pem"
        assert parsed.ssl_keyfile == "/path/to/key.pem"

    def test_phase3_reload_flag(self):
        parser = _build_parser()
        parsed = parser.parse_args(["myapp:app", "--reload"])
        assert parsed.reload is True

    def test_phase3_keepalive_args(self):
        parser = _build_parser()
        parsed = parser.parse_args(
            [
                "myapp:app",
                "--keep-alive-timeout",
                "30.0",
                "--max-requests-per-connection",
                "1000",
            ]
        )
        assert parsed.keep_alive_timeout == 30.0
        assert parsed.max_requests_per_connection == 1000

    def test_factory_pattern_preserved(self):
        """Factory pattern 'module:create_app()' is preserved through argparse."""
        parser = _build_parser()
        parsed = parser.parse_args(["myapp:create_app()"])
        assert parsed.app == "myapp:create_app()"

    def test_dotted_factory_pattern(self):
        """Dotted factory pattern 'myapp.web:create_app()' is preserved."""
        parser = _build_parser()
        parsed = parser.parse_args(["myapp.web:create_app()"])
        assert parsed.app == "myapp.web:create_app()"

    def test_invalid_log_level_exits(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["myapp:app", "--log-level", "verbose"])


class TestCLIMain:
    """main() validates the app string before starting."""

    def test_invalid_app_exits(self):
        """main() exits with code 1 for an invalid app string."""
        with pytest.raises(SystemExit) as exc_info:
            main(["nonexistent_module_xyz:app"])
        assert exc_info.value.code == 1

    def test_no_colon_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["justmodule"])
        assert exc_info.value.code == 1


class TestPublicAPI:
    """pounce.run() and pounce.ServerConfig are importable."""

    def test_import_run(self):
        from pounce import run

        assert callable(run)

    def test_import_server_config(self):
        from pounce import ServerConfig

        config = ServerConfig()
        assert config.host == "127.0.0.1"
        assert config.port == 8000

    def test_import_version(self):
        from pounce import __version__

        assert isinstance(__version__, str)
        assert "0.4.0" in __version__
