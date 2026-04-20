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
        assert "show" in out, (
            "pounce config --help should list the 'show' subcommand; got:\n" + out
        )

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
