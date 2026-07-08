"""Contract checks for scheduled and release benchmark evidence."""

from __future__ import annotations

from pathlib import Path

WORKFLOW = Path(".github/workflows/benchmarks.yml")


def test_benchmark_workflow_covers_release_schedule_and_both_python_modes() -> None:
    workflow = WORKFLOW.read_text()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "release:" in workflow
    assert 'python-version: "3.14"' in workflow
    assert 'python-version: "3.14t"' in workflow
    assert "workload: hello" in workflow
    assert "workload: chirp" in workflow


def test_benchmark_workflow_emits_validated_sustained_release_assets() -> None:
    workflow = WORKFLOW.read_text()

    assert '--duration "${DURATION}"' in workflow
    assert "--connections 4" in workflow
    assert '--repeat "${REPEAT}"' in workflow
    assert '--rate "${TARGET_RPS}"' in workflow
    assert "pounce,uvicorn,hypercorn,granian" in workflow
    assert "validate_artifact" in workflow
    assert "actions/upload-artifact@v7" in workflow
    assert "gh release upload" in workflow
