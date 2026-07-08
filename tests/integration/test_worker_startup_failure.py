"""CLI proof for fail-loud per-worker startup hooks (issue #245)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _server_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = str(ROOT / "src")
    if env.get("PYTHONPATH"):
        pythonpath = f"{pythonpath}{os.pathsep}{env['PYTHONPATH']}"
    env["PYTHONPATH"] = pythonpath
    return env


@pytest.mark.integration
@pytest.mark.parametrize("workers", [1, 2])
def test_required_worker_startup_failure_exits_nonzero(
    tmp_path: Path,
    workers: int,
) -> None:
    """Single- and multi-worker CLI boot fail with the catalogued code."""
    config = tmp_path / "pounce.toml"
    config.write_text('worker_startup_failure = "shutdown"\n', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pounce",
            "serve",
            "--app",
            "tests.startup_failure_app:app",
            "--config",
            str(config),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--workers",
            str(workers),
            "--no-access-log",
            "--signage",
            "off",
        ],
        cwd=ROOT,
        env=_server_env(),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "POUNCE_WORKER_STARTUP_FAILED" in output
