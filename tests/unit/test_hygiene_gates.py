"""Executable proof for the architecture and diagnostic hygiene gates."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RAISE_LINTER = REPO_ROOT / "scripts" / "lint_raise_messages.py"


@pytest.mark.issue(264)
def test_import_linter_architecture_contracts() -> None:
    """Protocol, ASGI, and network ownership edges remain mechanically enforced."""
    executable = Path(sys.executable).with_name("lint-imports")
    assert executable.is_file(), "import-linter must be installed in the dev environment"
    result = subprocess.run(
        [str(executable), "--config", ".importlinter"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.issue(264)
def test_raise_message_baseline_is_current() -> None:
    """No public-path raise-message debt is added or silently rewritten."""
    result = subprocess.run(
        [sys.executable, str(RAISE_LINTER)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_raise_message_linter_rejects_short_static_message(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("def public() -> None:\n    raise ValueError('nope')\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(RAISE_LINTER), str(bad)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "nope" in result.stderr


def test_raise_message_linter_accepts_actionable_message(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    good.write_text(
        "def public(value: str) -> None:\n"
        "    raise ValueError(f'Expected a valid HTTP token, but received {value!r}.')\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(RAISE_LINTER), str(good)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_raise_message_linter_exempts_private_helpers(tmp_path: Path) -> None:
    private = tmp_path / "private.py"
    private.write_text("def _private() -> None:\n    raise ValueError('nope')\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(RAISE_LINTER), str(private)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
