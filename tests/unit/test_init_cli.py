"""Subprocess-level tests for CLI commands that print their own output.

Sprint 2.3 of the vibe-readiness epic fixes the trailing ``None`` that milo's
dispatcher prints when a command returns ``None``. Every pounce CLI command
prints its own output via ``print()`` or ``_output._write``; none of them
return data for milo to format. Setting ``display_result=False`` on each
``@cli.command`` registration suppresses the bogus ``None`` without losing
the ``--format json`` / ``--output-file`` pathways (those already use custom
printing, not milo's serializer).

These tests invoke the real CLI in a subprocess so they catch both bugs:
the ``None`` milo prints *and* any future dispatcher-level regression that
pounce unit tests would not notice.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(
    *args: str, cwd: Path | str | None = None, env_extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = {"NO_COLOR": "1", "PATH": ""}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "pounce", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd else None,
        env=env,
        check=False,
    )


class TestInitNoNonePrint:
    """``pounce init`` scaffolds files and exits without a trailing ``None``."""

    def test_init_fresh_dir_no_none_in_stdout(self, tmp_path: Path) -> None:
        result = _run("init", cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # Success path prints a scaffold summary + "Next: ..." line.
        assert "Scaffolded" in result.stdout
        assert "Next: pounce serve" in result.stdout
        # The bug: milo's dispatcher prints str(None) == "None" for every
        # command returning None. Never should appear in user-facing output.
        assert "None" not in result.stdout, (
            "pounce init leaked a trailing 'None' in stdout:\n" + result.stdout
        )

    def test_init_no_none_in_stderr(self, tmp_path: Path) -> None:
        result = _run("init", cwd=tmp_path)
        assert "None" not in result.stderr, (
            "pounce init leaked 'None' in stderr:\n" + result.stderr
        )


class TestCheckNoNonePrint:
    """``pounce check`` runs validators and exits without a trailing ``None``.

    Uses a minimal importable app in the tempdir so the check passes; that
    gates the success-path output, which is where ADR 0.3 + Sprint 2.2 added
    the "All checks passed." summary line that the 2.3 regression sits next
    to.
    """

    def test_check_success_no_none_in_stdout(self, tmp_path: Path) -> None:
        import socket

        # Bind an ephemeral port so the check passes deterministically on
        # machines where 8000 may already be in use (CI, dev boxes).
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            free_port = sock.getsockname()[1]

        (tmp_path / "app.py").write_text("async def app(s,r,sd): pass\n", encoding="utf-8")
        # check_results renders on the pretty path when stderr is a TTY, and on
        # the plain path when piped. Subprocess-captured stderr is non-TTY, so
        # we exercise the branch agents actually hit.
        result = _run(
            "check", "--app", "app:app", "--port", str(free_port), cwd=tmp_path
        )
        assert result.returncode == 0, result.stderr
        # Output of check_results goes to stderr (via _write) — assert the
        # summary is there *and* no bogus None anywhere.
        assert "All checks passed" in result.stderr
        assert "None" not in result.stdout, (
            "pounce check leaked 'None' in stdout:\n" + result.stdout
        )
        assert "None" not in result.stderr, (
            "pounce check leaked 'None' in stderr:\n" + result.stderr
        )


class TestInfoNoNonePrint:
    """``pounce info`` prints the diagnostic panel without a trailing ``None``."""

    def test_info_no_none_in_output(self) -> None:
        result = _run("info")
        assert result.returncode == 0, result.stderr
        assert "None" not in result.stdout, (
            "pounce info leaked 'None' in stdout:\n" + result.stdout
        )


class TestCheckConfigParity:
    """Sprint 3: ``pounce check --config FILE`` accepts a TOML path, matching ``serve``.

    Before this sprint, ``check`` had no ``config`` parameter at all — running
    ``pounce init && pounce check --config pounce.toml`` failed with
    ``unrecognized arguments: --config``. The just-scaffolded config was not
    validate-able by the just-scaffolded check command.
    """

    def _free_port(self) -> int:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def test_check_accepts_config_flag(self, tmp_path: Path) -> None:
        # Scaffold exactly what `pounce init` would write, then validate it.
        (tmp_path / "app.py").write_text("async def app(s,r,sd): pass\n", encoding="utf-8")
        toml = tmp_path / "pounce.toml"
        toml.write_text(f'host = "127.0.0.1"\nport = {self._free_port()}\n', encoding="utf-8")

        result = _run(
            "check", "--app", "app:app", "--config", str(toml), cwd=tmp_path
        )
        assert result.returncode == 0, (
            "pounce check --config rejected the flag or the scaffolded TOML:\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        assert "unrecognized" not in result.stderr.lower()
        assert "All checks passed" in result.stderr

    def test_check_toml_port_override_flows_through(self, tmp_path: Path) -> None:
        # The real test of plumbing: a port in the TOML has to reach the
        # pre-flight port-availability check. If we put a busy port in the
        # TOML, ``check`` must report it as FAIL — proving the TOML actually
        # propagated through the loader into the pre-flight validator.
        import socket

        # Occupy a port so check_port_available reports it as in use.
        occupant = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        occupant.bind(("127.0.0.1", 0))
        occupant.listen(1)
        busy_port = occupant.getsockname()[1]
        try:
            (tmp_path / "app.py").write_text(
                "async def app(s,r,sd): pass\n", encoding="utf-8"
            )
            toml = tmp_path / "pounce.toml"
            toml.write_text(
                f'host = "127.0.0.1"\nport = {busy_port}\n', encoding="utf-8"
            )

            result = _run(
                "check", "--app", "app:app", "--config", str(toml), cwd=tmp_path
            )
            # Port in use → check fails with exit 1.
            assert result.returncode == 1, (
                "check should fail on busy TOML port — proving --config is "
                f"reaching the port validator:\nstdout={result.stdout!r}\n"
                f"stderr={result.stderr!r}"
            )
            assert "FAIL" in result.stderr
        finally:
            occupant.close()

    def test_check_llms_txt_lists_config(self) -> None:
        # ``--llms-txt`` auto-derives from each command's milo annotations, so
        # the moment ``check`` gains a ``config`` parameter it should show up
        # here without any extra wiring. This pins the agent-discovery path.
        result = _run("--llms-txt")
        assert result.returncode == 0, result.stderr
        stdout = result.stdout
        # Each command is emitted as a bullet item with its parameters on the
        # following line. Find ``- **check**:`` and slice through to the next
        # ``- **`` bullet (or end of block).
        check_idx = stdout.find("- **check**:")
        assert check_idx != -1, "expected '- **check**:' bullet in llms.txt output"
        next_idx = stdout.find("- **", check_idx + len("- **check**:"))
        check_section = stdout[check_idx:next_idx] if next_idx != -1 else stdout[check_idx:]
        assert "--config" in check_section, (
            "check command should list --config in llms.txt; got section:\n"
            + check_section
        )

    def test_init_then_check_roundtrip(self, tmp_path: Path) -> None:
        # End-to-end acceptance from the plan: init scaffolds, check validates
        # the scaffolded TOML. Must exit 0 with no manual editing.
        init = _run("init", cwd=tmp_path)
        assert init.returncode == 0, init.stderr
        assert (tmp_path / "pounce.toml").exists()

        result = _run(
            "check",
            "--app",
            "app:app",
            "--config",
            "pounce.toml",
            "--port",
            str(self._free_port()),
            cwd=tmp_path,
        )
        assert result.returncode == 0, (
            "init → check --config pounce.toml round-trip failed:\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
