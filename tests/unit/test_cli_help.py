"""Tests for branded --help rendering across top-level and nested subcommand groups.

Sprint 2.1 of the vibe-readiness epic: ``pounce config --help`` was rendering
its subparser dest (``_command_config``) as a generic positional instead of
listing ``schema`` and ``show`` under a Commands group. The branded renderer
at ``_cli._render_branded_help`` hard-coded ``action.dest == "_command"``;
argparse names nested-group dests ``_command_<group-name>``.

This test pins the contract: any parser whose subparser dest follows that
convention is rendered with its subcommands listed, not as a positional.
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def _run_help(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "pounce", *args, "--help"],
        capture_output=True,
        text=True,
        check=True,
        env={"NO_COLOR": "1", "PATH": ""},
    )
    return result.stdout


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


class TestServeCheckHelp:
    """serve/check help surfaces the TOML configuration escape hatch."""

    @staticmethod
    def _subparser(name: str) -> argparse.ArgumentParser:
        from pounce._cli import cli

        parser = cli.build_parser()
        for action_group in parser._action_groups:
            for action in action_group._group_actions:
                choices = getattr(action, "choices", None)
                if isinstance(choices, dict) and name in choices:
                    return choices[name]
        msg = f"subparser {name!r} not found"
        raise AssertionError(msg)

    def test_serve_help_mentions_toml_template(self) -> None:
        from pounce._cli import _render_branded_help

        out = _render_branded_help(self._subparser("serve"))
        assert "pounce config schema --output-format toml-template" in out
        assert "[tool.pounce]" in out

    def test_check_help_mentions_toml_template(self) -> None:
        from pounce._cli import _render_branded_help

        out = _render_branded_help(self._subparser("check"))
        assert "pounce config schema --output-format toml-template" in out

    def test_serve_help_lists_new_flags(self) -> None:
        from pounce._cli import _render_branded_help

        out = _render_branded_help(self._subparser("serve"))
        assert "--debug" in out
        assert "--trusted-hosts" in out
        assert "--metrics" in out
