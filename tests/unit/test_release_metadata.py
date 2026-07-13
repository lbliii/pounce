"""Release metadata regression proof."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]


@pytest.mark.issue(300)
def test_release_title_uses_only_pep621_project_metadata() -> None:
    """Towncrier category names cannot leak into the GitHub release title."""
    result = subprocess.run(
        [sys.executable, "scripts/release_metadata.py", "title"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "bengal-pounce 0.9.1"
