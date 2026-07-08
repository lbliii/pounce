"""Integration tests for pounce._cli — command-line interface."""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import threading
from pathlib import Path

import pytest

from pounce._cli import cli, main, parse_dirs, parse_extensions, parse_hosts
from pounce.config import ServerConfig


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

    @pytest.mark.issue(242)
    def test_timeout_args(self, mocker):
        mock_server = mocker.patch("pounce._cli.Server")
        mocker.patch("pounce._cli.import_app", return_value=lambda: None)
        cli.call(
            "serve",
            app="myapp:app",
            keep_alive_timeout=30.0,
            request_timeout=45.0,
            write_timeout=60.0,
            max_requests_per_connection=1000,
        )
        config = mock_server.call_args[0][0]
        assert config.keep_alive_timeout == 30.0
        assert config.request_timeout == 45.0
        assert config.write_timeout == 60.0
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


class TestParseHosts:
    """parse_hosts() normalizes comma-separated host strings."""

    def test_none_returns_empty(self):
        assert parse_hosts(None) == ()

    def test_empty_string_returns_empty(self):
        assert parse_hosts("") == ()

    def test_multiple_hosts(self):
        assert parse_hosts("localhost,127.0.0.1,example.com") == (
            "localhost",
            "127.0.0.1",
            "example.com",
        )

    def test_whitespace_stripped(self):
        assert parse_hosts(" localhost , 127.0.0.1 ") == ("localhost", "127.0.0.1")

    def test_empty_entries_filtered(self):
        assert parse_hosts("localhost,,127.0.0.1") == ("localhost", "127.0.0.1")


class TestServeCliOverrides:
    """High-value serve flags merge into ServerConfig."""

    def test_debug_trusted_hosts_and_metrics(self):
        from pounce._config_file import load_config_with_overrides

        merged = load_config_with_overrides(
            {
                "debug": True,
                "trusted_hosts": ["localhost", "127.0.0.1"],
                "metrics_enabled": True,
            }
        )
        cfg = ServerConfig.from_mapping(merged)
        assert cfg.debug is True
        assert cfg.trusted_hosts == frozenset({"localhost", "127.0.0.1"})
        assert cfg.metrics_enabled is True


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


class TestCLIConfigGroup:
    """`pounce config schema` and `pounce config show` subcommands."""

    def test_schema_json_is_valid_jsonschema(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        cli.call("config.schema", output_format="json")
        out = capsys.readouterr().out
        doc = json.loads(out)
        assert doc["type"] == "object"
        assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        # Spot-check a few known properties
        assert doc["properties"]["port"]["default"] == 8000
        assert doc["properties"]["log_level"]["enum"] == [
            "critical",
            "debug",
            "error",
            "info",
            "warning",
        ]

    def test_schema_toml_template_has_commented_fields(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.call("config.schema", output_format="toml-template")
        out = capsys.readouterr().out
        # pounce.toml uses top-level keys — no [pounce] section header.
        assert "[pounce]" not in out
        assert "# port = 8000" in out
        assert "# host = " in out
        # enum hint
        assert "# log_level = " in out
        assert "one of:" in out

    def test_show_toml_redacts_secrets(self, capsys: pytest.CaptureFixture[str]) -> None:
        cli.call("config.show", output_format="toml")
        out = capsys.readouterr().out
        assert out.startswith("[pounce]\n")
        # REDACT_TO_BOOL: raw field name never appears
        assert "\nssl_certfile =" not in out
        assert "\nssl_certfile_set =" in out
        # EXPOSE passes through
        assert "port = 8000" in out

    def test_show_json_redacts_secrets(self, capsys: pytest.CaptureFixture[str]) -> None:
        import json

        cli.call("config.show", output_format="json")
        doc = json.loads(capsys.readouterr().out)
        assert "ssl_certfile" not in doc
        assert doc["ssl_certfile_set"] is False
        assert doc["port"] == 8000


class TestCLIInitCommand:
    """The 'init' command scaffolds a fresh pounce project."""

    def test_scaffolds_three_files(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cli.call("init", directory=str(tmp_path))
        out = capsys.readouterr().out
        assert "Scaffolded 3 files" in out
        assert (tmp_path / "app.py").exists()
        assert (tmp_path / "pounce.toml").exists()
        assert (tmp_path / ".gitignore").exists()

    def test_collision_without_force_exits(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        (tmp_path / "app.py").write_text("existing", encoding="utf-8")
        with pytest.raises(SystemExit) as ei:
            cli.call("init", directory=str(tmp_path))
        assert ei.value.code == 1
        # Original file must survive.
        assert (tmp_path / "app.py").read_text(encoding="utf-8") == "existing"
        err = capsys.readouterr().err
        assert "app.py" in err

    def test_force_overwrites(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("old", encoding="utf-8")
        cli.call("init", directory=str(tmp_path), force=True)
        # Overwritten with the template.
        content = (tmp_path / "app.py").read_text(encoding="utf-8")
        assert "async def app" in content
        assert "hello from pounce" in content


def _load_module_from_path(module_name: str, file_path: Path):
    """Import a module from an explicit file path (no sys.path mutation)."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCLIInitEndToEnd:
    """``pounce init`` output actually serves the advertised response."""

    def test_scaffolded_app_serves_hello(self, tmp_path: Path) -> None:
        """The generated app.py, run through the real worker pipeline, returns
        ``hello from pounce\\n`` — the acceptance criterion in the epic plan.
        """
        from pounce._init import run_init
        from pounce.config import ServerConfig
        from pounce.net.listener import create_listeners
        from pounce.supervisor import Supervisor
        from tests.conftest import _wait_for_ready, send_raw_request

        run_init(tmp_path)
        module = _load_module_from_path("pounce_init_scaffold_app", tmp_path / "app.py")
        asgi_app = module.app

        config = ServerConfig(host="127.0.0.1", port=0, workers=1, access_log=False)
        sockets = create_listeners(config, 1)
        addr = sockets[0].getsockname()
        sup = Supervisor(config, asgi_app, mode="thread")

        t = threading.Thread(target=sup.run, args=(sockets,), daemon=True)
        t.start()
        try:
            _wait_for_ready(addr)
            response = send_raw_request(
                addr,
                b"GET / HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
            )
            assert b"200" in response
            assert b"hello from pounce\n" in response
        finally:
            sup.shutdown()
            t.join(timeout=5.0)
            for s in set(sockets):
                with contextlib.suppress(Exception):
                    s.close()
            # Clean up the one-shot module so future imports are unaffected.
            sys.modules.pop("pounce_init_scaffold_app", None)

    def test_scaffolded_config_passes_check(self, tmp_path: Path) -> None:
        """``pounce check`` on the generated pounce.toml must succeed —
        i.e. every field (all commented) parses cleanly into ServerConfig.
        """
        from pounce._config_file import load_config_with_overrides
        from pounce._init import run_init
        from pounce.config import ServerConfig

        run_init(tmp_path)
        merged = load_config_with_overrides({}, config_path=tmp_path / "pounce.toml")
        # No unknown keys, no bad types — the generated template is loadable.
        ServerConfig(**merged)
