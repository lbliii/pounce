"""Unit tests for the standalone benchmark runner."""

from benchmarks.run_benchmark import _benchmark_url


def test_benchmark_url_uses_workload_path() -> None:
    assert _benchmark_url(8100, "chirp") == "http://127.0.0.1:8100/threads/1"


def test_benchmark_url_keeps_root_workload_path() -> None:
    assert _benchmark_url(8100, "bengal") == "http://127.0.0.1:8100/"
