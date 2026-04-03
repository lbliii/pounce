"""Unit tests for the bench CLI command.

Tests command construction, uvicorn detection, and cleanup logic
by mocking subprocess calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pounce._bench import (
    BenchSuite,
    WorkloadResult,
    _find_free_port,
    _format_results,
    _run_bench,
)

# ── WorkloadResult / BenchSuite ─────────────────────────────────────


class TestWorkloadResult:
    def test_rps_calculation(self) -> None:
        w = WorkloadResult("hello", 1000, 0, 10.0, [], 50.0)
        assert w.rps == pytest.approx(100.0)

    def test_rps_zero_duration(self) -> None:
        w = WorkloadResult("hello", 1000, 0, 0.0, [], 0.0)
        assert w.rps == 0.0

    def test_percentiles(self) -> None:
        lats = [float(i) for i in range(100)]
        w = WorkloadResult("hello", 100, 0, 1.0, lats, 0.0)
        assert w.p50 == pytest.approx(50.0)
        assert w.p95 == pytest.approx(95.0)
        assert w.p99 == pytest.approx(99.0)

    def test_percentiles_empty(self) -> None:
        w = WorkloadResult("hello", 0, 0, 1.0, [], 0.0)
        assert w.p50 == 0.0
        assert w.p95 == 0.0
        assert w.p99 == 0.0


# ── _format_results ─────────────────────────────────────────────────


class TestFormatResults:
    def test_single_suite(self) -> None:
        suite = BenchSuite("pounce", workers=1, connections=10, duration=5)
        suite.workloads.append(
            WorkloadResult("hello", 5000, 0, 5.0, [1.0, 2.0, 3.0], 40.0)
        )
        output = _format_results([suite])
        assert "pounce" in output
        assert "hello" in output

    def test_comparison_table(self) -> None:
        s1 = BenchSuite("pounce", workers=1, connections=10, duration=5)
        s1.workloads.append(WorkloadResult("hello", 5000, 0, 5.0, [1.0], 40.0))
        s2 = BenchSuite("uvicorn", workers=1, connections=10, duration=5)
        s2.workloads.append(WorkloadResult("hello", 4000, 0, 5.0, [1.5], 45.0))
        output = _format_results([s1, s2])
        assert "Comparison" in output
        assert "pounce vs uvicorn" in output


# ── _run_bench command construction ─────────────────────────────────


def _make_mock_proc() -> MagicMock:
    """Create a mock subprocess.Popen instance with common defaults."""
    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.stderr = MagicMock()
    mock_proc.stderr.read.return_value = b""
    mock_proc.wait.return_value = 0
    return mock_proc


class TestRunBenchCommandConstruction:
    """Verify that _run_bench constructs correct command lines for pounce and uvicorn."""

    @patch("shutil.rmtree")
    @patch("pounce._bench._wait_for_server", return_value=False)
    @patch("subprocess.Popen")
    @patch("tempfile.mkdtemp", return_value="/tmp/pounce_bench_test")
    @patch("pounce._bench._write_bench_app")
    def test_pounce_cmd_uses_app_flag(
        self, mock_write, mock_mkdtemp, mock_popen, mock_wait, mock_rmtree
    ) -> None:
        """Pounce commands use --app flag."""
        mock_popen.return_value = _make_mock_proc()

        pounce_cmd = ["python", "-m", "pounce", "serve", "--host", "127.0.0.1", "--port", "9999"]
        _run_bench(pounce_cmd, "pounce", duration=1, connections=1, host="127.0.0.1", port=9999)

        popen_args = mock_popen.call_args[0][0]
        assert "--app" in popen_args
        app_idx = popen_args.index("--app")
        assert popen_args[app_idx + 1] == "_bench_app:app"

    @patch("shutil.rmtree")
    @patch("pounce._bench._wait_for_server", return_value=False)
    @patch("subprocess.Popen")
    @patch("tempfile.mkdtemp", return_value="/tmp/pounce_bench_test")
    @patch("pounce._bench._write_bench_app")
    def test_uvicorn_cmd_uses_positional_app(
        self, mock_write, mock_mkdtemp, mock_popen, mock_wait, mock_rmtree
    ) -> None:
        """Uvicorn commands use positional app argument (no --app flag)."""
        mock_popen.return_value = _make_mock_proc()

        uvi_cmd = ["python", "-m", "uvicorn", "--host", "127.0.0.1", "--port", "9999"]
        _run_bench(uvi_cmd, "uvicorn", duration=1, connections=1, host="127.0.0.1", port=9999)

        popen_args = mock_popen.call_args[0][0]
        assert "--app" not in popen_args
        assert popen_args[-1] == "_bench_app:app"

    @patch("shutil.rmtree")
    @patch("pounce._bench._wait_for_server", return_value=False)
    @patch("subprocess.Popen")
    @patch("tempfile.mkdtemp", return_value="/tmp/pounce_bench_test")
    @patch("pounce._bench._write_bench_app")
    def test_uvicorn_direct_binary_detected(
        self, mock_write, mock_mkdtemp, mock_popen, mock_wait, mock_rmtree
    ) -> None:
        """Direct uvicorn binary is detected via basename."""
        mock_popen.return_value = _make_mock_proc()

        uvi_cmd = ["uvicorn", "--host", "127.0.0.1"]
        _run_bench(uvi_cmd, "uvicorn", duration=1, connections=1, host="127.0.0.1", port=9999)

        popen_args = mock_popen.call_args[0][0]
        assert "--app" not in popen_args
        assert popen_args[-1] == "_bench_app:app"

    @patch("shutil.rmtree")
    @patch("pounce._bench._wait_for_server", return_value=False)
    @patch("subprocess.Popen")
    @patch("tempfile.mkdtemp", return_value="/tmp/pounce_bench_test")
    @patch("pounce._bench._write_bench_app")
    def test_pythonpath_includes_tmpdir(
        self, mock_write, mock_mkdtemp, mock_popen, mock_wait, mock_rmtree
    ) -> None:
        """PYTHONPATH is set to include the temp directory."""
        mock_popen.return_value = _make_mock_proc()

        _run_bench(
            ["pounce", "serve"], "pounce", duration=1, connections=1,
            host="127.0.0.1", port=9999,
        )

        env = mock_popen.call_args[1]["env"]
        assert "/tmp/pounce_bench_test" in env["PYTHONPATH"]

    @patch("shutil.rmtree")
    @patch("pounce._bench._wait_for_server", return_value=False)
    @patch("subprocess.Popen")
    @patch("tempfile.mkdtemp", return_value="/tmp/pounce_bench_test")
    @patch("pounce._bench._write_bench_app")
    def test_cleanup_on_server_failure(
        self, mock_write, mock_mkdtemp, mock_popen, mock_wait, mock_rmtree
    ) -> None:
        """Temp directory is cleaned up even when server fails to start."""
        proc = _make_mock_proc()
        proc.stderr.read.return_value = b"Error: bind failed"
        mock_popen.return_value = proc

        suite = _run_bench(
            ["pounce", "serve"], "pounce", duration=1, connections=1,
            host="127.0.0.1", port=9999,
        )

        assert len(suite.workloads) == 0
        mock_rmtree.assert_called_once_with("/tmp/pounce_bench_test", ignore_errors=True)


# ── _find_free_port ──────────────────────────────────────────────────


class TestFindFreePort:
    def test_returns_valid_port(self) -> None:
        port = _find_free_port()
        assert 1024 <= port <= 65535
