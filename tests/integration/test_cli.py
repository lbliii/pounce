"""Integration tests for pounce._cli — command-line interface."""

import pytest

from pounce._cli import cli, main, parse_dirs, parse_extensions


class TestCLIServeCommand:
    """The 'serve' command parses correct defaults and overrides via cli.call()."""

    def test_default_args(self, mocker):
        """Defaults match ServerConfig defaults."""
        mock_server = mocker.patch("pounce._cli.Server")
        mock_import = mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call("serve", app="myapp:app")
        mock_import.assert_called_once_with("myapp:app")
        config = mock_server.call_args[0][0]
        assert config.host == "127.0.0.1"
        assert config.port == 8000
        assert config.workers == 1
        assert config.log_level == "info"
        assert config.root_path == ""
        assert config.compression is True
        assert config.server_timing is False
        assert config.access_log is True
        assert config.ssl_certfile is None
        assert config.ssl_keyfile is None
        assert config.reload is False
        assert config.keep_alive_timeout == 5.0
        assert config.max_requests_per_connection == 0

    def test_custom_args(self, mocker):
        mock_server = mocker.patch("pounce._cli.Server")
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call(
            "serve",
            app="myapp.web:create_app()",
            host="0.0.0.0",
            port=9000,
            workers=4,
            log_level="debug",
            root_path="/api",
            no_compression=True,
            server_timing=True,
            no_access_log=True,
        )
        config = mock_server.call_args[0][0]
        assert config.host == "0.0.0.0"
        assert config.port == 9000
        assert config.workers == 4
        assert config.log_level == "debug"
        assert config.root_path == "/api"
        assert config.compression is False
        assert config.server_timing is True
        assert config.access_log is False

    def test_tls_args(self, mocker):
        mock_server = mocker.patch("pounce._cli.Server")
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call(
            "serve",
            app="myapp:app",
            ssl_certfile="/path/to/cert.pem",
            ssl_keyfile="/path/to/key.pem",
        )
        config = mock_server.call_args[0][0]
        assert config.ssl_certfile == "/path/to/cert.pem"
        assert config.ssl_keyfile == "/path/to/key.pem"

    def test_reload_flag(self, mocker):
        mock_server = mocker.patch("pounce._cli.Server")
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call("serve", app="myapp:app", reload=True)
        config = mock_server.call_args[0][0]
        assert config.reload is True

    def test_reload_include(self, mocker):
        mock_server = mocker.patch("pounce._cli.Server")
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call("serve", app="myapp:app", reload=True, reload_include=".html,.css,.md")
        config = mock_server.call_args[0][0]
        assert config.reload_include == (".html", ".css", ".md")

    def test_reload_dir_multiple(self, mocker):
        mock_server = mocker.patch("pounce._cli.Server")
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call("serve", app="myapp:app", reload=True, reload_dir=["./templates", "./static"])
        config = mock_server.call_args[0][0]
        assert config.reload_dirs == ("./templates", "./static")

    def test_keepalive_args(self, mocker):
        mock_server = mocker.patch("pounce._cli.Server")
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call(
            "serve",
            app="myapp:app",
            keep_alive_timeout=30.0,
            max_requests_per_connection=1000,
        )
        config = mock_server.call_args[0][0]
        assert config.keep_alive_timeout == 30.0
        assert config.max_requests_per_connection == 1000

    def test_factory_pattern_preserved(self, mocker):
        mocker.patch("pounce._cli.Server")
        mock_import = mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call("serve", app="myapp:create_app()")
        mock_import.assert_called_once_with("myapp:create_app()")

    def test_dotted_factory_pattern(self, mocker):
        mocker.patch("pounce._cli.Server")
        mock_import = mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call("serve", app="myapp.web:create_app()")
        mock_import.assert_called_once_with("myapp.web:create_app()")


class TestCLIMainViaArgv:
    """main() with argv strings routes through the serve subcommand."""

    def test_serve_via_main(self, mocker):
        mock_server = mocker.patch("pounce._cli.Server")
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        main(["serve", "--app", "myapp:app", "--port", "9000"])
        config = mock_server.call_args[0][0]
        assert config.port == 9000

    def test_invalid_app_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["serve", "--app", "nonexistent_module_xyz:app"])
        assert exc_info.value.code == 1

    def test_no_colon_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["serve", "--app", "justmodule"])
        assert exc_info.value.code == 1


class TestCLIBuiltinFlags:
    """Milo built-in flags work correctly."""

    def test_version_flag(self, capsys):
        from pounce import __version__

        with pytest.raises(SystemExit):
            main(["--version"])
        captured = capsys.readouterr()
        assert __version__ in captured.out

    def test_llms_txt_flag(self, capsys):
        main(["--llms-txt"])
        captured = capsys.readouterr()
        assert "serve" in captured.out.lower()
        assert "pounce" in captured.out.lower()


class TestParseExtensions:
    """parse_extensions() normalizes comma-separated extension strings."""

    def test_none_returns_empty(self):
        assert parse_extensions(None) == ()

    def test_empty_string_returns_empty(self):
        assert parse_extensions("") == ()

    def test_dotted_extensions(self):
        assert parse_extensions(".html,.css,.md") == (".html", ".css", ".md")

    def test_missing_dots_prefixed(self):
        assert parse_extensions("html,css,md") == (".html", ".css", ".md")

    def test_mixed_dots(self):
        assert parse_extensions(".html,css,.md") == (".html", ".css", ".md")

    def test_whitespace_stripped(self):
        assert parse_extensions(" .html , .css , .md ") == (".html", ".css", ".md")

    def test_empty_entries_filtered(self):
        assert parse_extensions(".html,,.css") == (".html", ".css")

    def test_whitespace_only_entries_filtered(self):
        assert parse_extensions(".html,  ,.css") == (".html", ".css")

    def test_single_extension(self):
        assert parse_extensions("html") == (".html",)


class TestParseDirs:
    """parse_dirs() cleans directory path lists."""

    def test_none_returns_empty(self):
        assert parse_dirs(None) == ()

    def test_empty_list_returns_empty(self):
        assert parse_dirs([]) == ()

    def test_single_dir(self):
        assert parse_dirs(["./templates"]) == ("./templates",)

    def test_multiple_dirs(self):
        assert parse_dirs(["./templates", "./static"]) == ("./templates", "./static")

    def test_whitespace_stripped(self):
        assert parse_dirs([" ./templates ", " ./static "]) == ("./templates", "./static")

    def test_empty_strings_filtered(self):
        assert parse_dirs(["./templates", "", "./static"]) == ("./templates", "./static")

    def test_whitespace_only_filtered(self):
        assert parse_dirs(["./templates", "   ", "./static"]) == ("./templates", "./static")


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
        assert __version__.count(".") >= 2  # Semver (e.g. 0.2.0)
