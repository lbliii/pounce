"""Tests for metadata-driven branded help across every command level."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import pytest


def _run_help(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pounce", *args, "--help"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "NO_COLOR": "1", "PATH": ""},
    )
    return result.stdout


def _mcp_tools() -> list[dict[str, Any]]:
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    result = subprocess.run(
        [sys.executable, "-m", "pounce", "--mcp"],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "NO_COLOR": "1", "PATH": ""},
    )
    response = json.loads(result.stdout)
    return response["result"]["tools"]


@pytest.mark.issue(304)
class TestConfigGroupHelp:
    """``pounce config --help`` must list subcommands, not ``_command_config``."""

    def test_config_help_lists_schema_subcommand(self) -> None:
        out = _run_help("config")
        assert "schema" in out, (
            "pounce config --help should list the 'schema' subcommand; got:\n" + out
        )

    def test_config_help_lists_show_subcommand(self) -> None:
        out = _run_help("config")
        assert "show" in out, "pounce config --help should list the 'show' subcommand; got:\n" + out

    def test_config_help_does_not_leak_subparser_dest(self) -> None:
        # The internal argparse dest name must never appear in user-facing help.
        out = _run_help("config")
        assert "_command_config" not in out, (
            "pounce config --help leaked the internal subparser dest; got:\n" + out
        )
        assert "_command" not in out, (
            "pounce config --help leaked an internal dest name; got:\n" + out
        )


@pytest.mark.issue(304)
class TestTopLevelHelpRegression:
    """Top-level ``pounce --help`` must continue to list subcommands as before."""

    def test_top_level_help_lists_commands(self) -> None:
        out = _run_help()
        # Sanity: the top-level subcommand set is still rendered under "Commands".
        for cmd in ("serve", "check", "info", "init", "config"):
            assert cmd in out, f"top-level --help missing '{cmd}'; got:\n{out}"

    def test_top_level_help_does_not_leak_dest(self) -> None:
        out = _run_help()
        assert "_command" not in out, (
            "top-level pounce --help leaked the internal dest; got:\n" + out
        )

    def test_top_level_help_is_branded_without_ansi_when_piped(self) -> None:
        out = _run_help()
        assert "=^..^=  pounce" in out
        assert "\x1b[" not in out


@pytest.mark.issue(304)
class TestServeCheckHelp:
    """serve/check help surfaces the TOML configuration escape hatch."""

    def test_serve_help_mentions_toml_template(self) -> None:
        out = _run_help("serve")
        assert "pounce config schema --output-format toml-template" in out
        assert "[tool.pounce]" in out

    def test_check_help_mentions_toml_template(self) -> None:
        out = _run_help("check")
        assert "pounce config schema --output-format toml-template" in out

    def test_serve_help_lists_new_flags(self) -> None:
        out = _run_help("serve")
        assert "--debug" in out
        assert "--trusted-hosts" in out
        assert "--metrics" in out

    @pytest.mark.parametrize(
        "command",
        [
            ("serve",),
            ("check",),
            ("bench",),
            ("info",),
            ("config",),
            ("config", "schema"),
            ("config", "show"),
        ],
    )
    def test_command_help_is_branded(self, command: tuple[str, ...]) -> None:
        out = _run_help(*command)
        assert f"=^..^=  pounce {' '.join(command)}" in out

    def test_help_uses_schema_description(self) -> None:
        from pounce._cli import cli

        description = cli.commands["serve"].schema["properties"]["worker_mode"]["description"]
        assert description in _run_help("serve")

    def test_description_parity_across_agent_and_human_surfaces(self) -> None:
        from milo.schema import function_to_schema

        from pounce._cli import cli, serve

        expected = "Worker model: auto, sync, async, or subinterpreter."
        function_schema = function_to_schema(serve)
        command_schema = cli.commands["serve"].schema
        serve_tool = next(tool for tool in _mcp_tools() if tool["name"] == "serve")

        assert function_schema["properties"]["worker_mode"]["description"] == expected
        assert command_schema["properties"]["worker_mode"]["description"] == expected
        assert serve_tool["inputSchema"]["properties"]["worker_mode"]["description"] == expected
        assert expected in _run_help("serve")

    def test_root_help_does_not_import_lazy_commands(self, capsys: pytest.CaptureFixture[str]) -> None:
        from pounce._cli import _PounceCLI

        lazy_cli = _PounceCLI(name="pounce", description="lazy fixture")
        lazy_cli.lazy_command(
            "deferred",
            "module_that_must_not_be_imported_for_help:handler",
            description="Deferred command",
        )

        lazy_cli._format_root_help()

        assert "deferred" in capsys.readouterr().out

    def test_template_failure_falls_back_to_plain_help(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from milo.help import HelpState

        from pounce import _output
        from pounce._cli import _render_help_state

        class BrokenEnvironment:
            def get_template(self, name: str) -> None:
                raise RuntimeError(f"cannot load {name}")

        monkeypatch.setattr(_output, "_get_env", BrokenEnvironment)
        with pytest.warns(UserWarning, match="Falling back to plain text"):
            rendered = _render_help_state(
                HelpState(
                    prog="pounce",
                    description="fallback fixture",
                    commands=({"name": "serve", "help": "Start server"},),
                )
            )

        assert "pounce - fallback fixture" in rendered
        assert "serve" in rendered
