"""Unit tests for the standalone benchmark runner."""

import pytest

from benchmarks.run_benchmark import (
    BenchmarkSuite,
    _benchmark_url,
    _sample_plan,
    _server_command,
    build_artifact,
)


def test_benchmark_url_uses_workload_path() -> None:
    assert _benchmark_url(8100, "chirp") == "http://127.0.0.1:8100/threads/1"


def test_benchmark_url_keeps_root_workload_path() -> None:
    assert _benchmark_url(8100, "bengal") == "http://127.0.0.1:8100/"


def test_benchmark_url_exposes_named_profile_paths() -> None:
    assert (
        _benchmark_url(8100, "bengal_asset")
        == "http://127.0.0.1:8100/assets/site.css"
    )
    assert _benchmark_url(8100, "bengal_feed") == "http://127.0.0.1:8100/feed.xml"
    assert _benchmark_url(8100, "chirp_events") == "http://127.0.0.1:8100/events"
    assert _benchmark_url(8100, "chirp_home") == "http://127.0.0.1:8100/"


def test_pounce_server_command_uses_current_cli_shape() -> None:
    command = _server_command("pounce", "chirp", 8100, 2)
    assert command[1:5] == ["-m", "pounce", "serve", "--app"]
    assert "benchmarks.apps.chirp_forum:app" in command


def test_sample_plan_repeats_each_workload_in_order() -> None:
    assert _sample_plan(["hello", "chirp"], 2) == [
        (1, "hello"),
        (1, "chirp"),
        (2, "hello"),
        (2, "chirp"),
    ]


def test_sample_plan_rejects_zero_repeat() -> None:
    with pytest.raises(ValueError, match="repeat must be >= 1"):
        _sample_plan(["hello"], 0)


def test_build_artifact_has_required_schema_fields() -> None:
    suite = BenchmarkSuite(
        timestamp="2026-05-22T120000-0400",
        python_version="3.14.2 free-threaded",
        platform="test-os",
        results=[
            {
                "server": "pounce",
                "workload": "chirp",
                "workers": 4,
                "req_per_sec": 1000.0,
                "p99_latency_ms": 2.0,
            }
        ],
    )

    artifact = build_artifact(
        suite,
        command=["python", "benchmarks/run_benchmark.py", "--workload", "chirp"],
        workload="chirp",
        workers=4,
        duration=30,
        connections=100,
        threads=4,
        load_tool="wrk",
        load_tool_version="wrk 4.2.0",
        compare=True,
    )

    required = {
        "artifact_id",
        "created_at",
        "git_sha",
        "command",
        "server_command",
        "workload",
        "python_version",
        "python_gil_mode",
        "os",
        "hardware",
        "worker_mode",
        "workers",
        "duration_seconds",
        "connections",
        "threads",
        "load_tool",
        "load_tool_version",
        "comparison_target",
        "comparison_target_version",
        "samples",
        "variance",
        "raw_output",
        "summary",
    }
    assert required <= artifact.keys()
    assert artifact["workload"] == "chirp"
    assert "pounce:chirp" in artifact["server_command"]
    assert "uvicorn:chirp" in artifact["server_command"]


def test_build_artifact_summarizes_repeated_samples() -> None:
    suite = BenchmarkSuite(
        timestamp="2026-05-22T120000-0400",
        python_version="3.14.2 free-threaded",
        platform="test-os",
        results=[
            {
                "server": "pounce",
                "workload": "chirp",
                "workers": 4,
                "req_per_sec": 1000.0,
                "p99_latency_ms": 2.0,
                "errors": 0,
                "sample_index": 1,
                "server_rss_bytes": 10_240,
            },
            {
                "server": "pounce",
                "workload": "chirp",
                "workers": 4,
                "req_per_sec": 1100.0,
                "p99_latency_ms": 3.0,
                "errors": 1,
                "sample_index": 2,
                "server_rss_bytes": 20_480,
            },
        ],
    )

    artifact = build_artifact(
        suite,
        command=[
            "python",
            "benchmarks/run_benchmark.py",
            "--workload",
            "chirp",
            "--repeat",
            "2",
        ],
        workload="chirp",
        workers=4,
        duration=30,
        connections=100,
        threads=4,
        load_tool="wrk",
        load_tool_version="wrk 4.2.0",
        compare=False,
    )

    [group] = artifact["summary"]["groups"]
    assert group["sample_count"] == 2
    assert group["req_per_sec"]["median"] == 1050.0
    assert group["req_per_sec"]["variance"] == 2500.0
    assert group["p99_latency_ms"]["p95"] == 3.0
    assert group["server_rss_bytes"]["max"] == 20_480
    assert group["errors_total"] == 1
    assert artifact["variance"]["groups"] == artifact["summary"]["groups"]
