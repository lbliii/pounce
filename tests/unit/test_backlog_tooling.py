"""Tests for issue-to-acceptance linkage tooling."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


issue_coverage = _load_script("issue_coverage")
closure_gate = _load_script("check_closure_acceptance")


def _write_test(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / name).write_text(body, encoding="utf-8")


@pytest.mark.issue(264)
def test_issue_collector_supports_function_class_and_module_markers(tmp_path: Path) -> None:
    _write_test(
        tmp_path,
        "test_markers.py",
        "import pytest\n"
        "pytestmark = pytest.mark.issue(100)\n"
        "@pytest.mark.issue(200, 201)\n"
        "class TestProof:\n"
        "    def test_class_marker(self):\n"
        "        pass\n"
        "@pytest.mark.issue(300)\n"
        "def test_function_marker():\n"
        "    pass\n",
    )

    coverage = issue_coverage.collect_issue_tests([tmp_path])

    assert set(coverage) == {100, 200, 201, 300}
    assert coverage[100] == ["test_markers.py::<module>"]
    assert coverage[200] == ["test_markers.py::TestProof::test_class_marker"]
    assert coverage[300] == ["test_markers.py::test_function_marker"]


@pytest.mark.issue(264)
def test_issue_collector_ignores_invalid_python(tmp_path: Path) -> None:
    _write_test(tmp_path, "test_invalid.py", "def broken(:\n    pass\n")
    assert issue_coverage.collect_issue_tests([tmp_path]) == {}


@pytest.mark.issue(264)
def test_closure_gate_parses_keywords_and_explicit_exemption() -> None:
    body = "Closes #143\nfixes #200\nResolved: #7\nrefs #999"
    assert closure_gate.extract_closing_issues(body) == {7, 143, 200}
    assert closure_gate.is_exempt("Acceptance: n/a (docs-only)")
    assert not closure_gate.is_exempt(body)


@pytest.mark.issue(264)
def test_closure_gate_rejects_unproved_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(closure_gate, "collect_issue_tests", lambda: {143: ["test_x.py"]})
    assert closure_gate.main(["--body", "Closes #143 and fixes #200"]) == 1


@pytest.mark.issue(264)
def test_closure_gate_accepts_proof_or_exemption(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(closure_gate, "collect_issue_tests", lambda: {143: ["test_x.py"]})
    assert closure_gate.main(["--body", "Closes #143"]) == 0
    assert closure_gate.main(["--body", "Closes #200\nAcceptance: n/a (docs-only)"]) == 0
