"""Unit tests for the standalone benchmark runner."""

from copy import deepcopy
from pathlib import Path

import pytest

import benchmarks.run_benchmark as runner
from benchmarks.run_benchmark import (
    BenchmarkSuite,
    _benchmark_url,
    _command_string,
    _nearest_rank,
    _sample_plan,
    _sample_process_stats,
    _server_command,
    _telemetry_block,
    _TelemetrySampler,
    build_artifact,
    build_profile_artifact,
    compare_artifact,
    save_artifact,
    validate_artifact,
)


def test_benchmark_url_uses_workload_path() -> None:
    assert _benchmark_url(8100, "chirp") == "http://127.0.0.1:8100/threads/1"


def test_benchmark_url_keeps_root_workload_path() -> None:
    assert _benchmark_url(8100, "bengal") == "http://127.0.0.1:8100/"


def test_benchmark_url_exposes_named_profile_paths() -> None:
    assert _benchmark_url(8100, "bengal_asset") == "http://127.0.0.1:8100/assets/site.css"
    assert _benchmark_url(8100, "bengal_feed") == "http://127.0.0.1:8100/feed.xml"
    assert _benchmark_url(8100, "chirp_events") == "http://127.0.0.1:8100/events"
    assert _benchmark_url(8100, "chirp_home") == "http://127.0.0.1:8100/"


def test_pounce_server_command_uses_current_cli_shape() -> None:
    command = _server_command("pounce", "chirp", 8100, 2)
    assert command[1:5] == ["-m", "pounce", "serve", "--app"]
    assert "benchmarks.apps.chirp_forum:app" in command


def test_comparison_server_commands_use_documented_cli_shapes() -> None:
    hypercorn = _server_command("hypercorn", "hello", 8101, 2)
    granian = _server_command("granian", "hello", 8102, 2)

    assert hypercorn[1:5] == ["-m", "hypercorn", "--bind", "127.0.0.1:8101"]
    assert hypercorn[-1] == "benchmarks.apps.hello:app"
    assert granian[1:5] == ["-m", "granian", "--interface", "asgi"]
    assert "127.0.0.1" in granian
    assert "8102" in granian
    assert granian[-1] == "benchmarks.apps.hello:app"


def test_command_string_redacts_sys_executable_path() -> None:
    command = _server_command("pounce", "chirp", 8100, 2)
    rendered = _command_string(command)
    assert rendered.startswith("python")
    assert "/.venv/bin/python" not in rendered


def test_build_artifact_command_can_include_interpreter() -> None:
    suite = BenchmarkSuite(
        timestamp="2026-05-22T120000-0400",
        python_version="3.14.2 free-threaded",
        platform="test-os",
    )

    artifact = build_artifact(
        suite,
        command=["/tmp/work/.venv/bin/python", "benchmarks/run_benchmark.py"],
        workload="chirp",
        workers=1,
        duration=5,
        connections=50,
        threads=4,
        load_tool="wrk",
        load_tool_version="wrk 4.2.0",
        compare=False,
    )

    assert artifact["command"] == "python benchmarks/run_benchmark.py"


def test_nearest_rank_uses_ceiling_rank() -> None:
    values = [float(i) for i in range(1, 12)]
    assert _nearest_rank(values, 95) == 11.0


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


def test_build_artifact_has_required_schema_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_server_version", lambda module: f"{module} 1.2.3")
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
        "artifact_schema_version",
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
        "telemetry",
        "variance",
        "raw_output",
        "summary",
    }
    assert required <= artifact.keys()
    assert artifact["workload"] == "chirp"
    assert artifact["comparison_target_version"] == "uvicorn 1.2.3"
    assert "pounce:chirp" in artifact["server_command"]
    assert "uvicorn:chirp" in artifact["server_command"]


def test_build_artifact_records_multiple_comparison_servers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_server_version", lambda module: f"{module} 1.2.3")
    suite = BenchmarkSuite(timestamp="2026-07-08T120000-0400")

    artifact = build_artifact(
        suite,
        command=["python", "benchmarks/run_benchmark.py"],
        workload="hello",
        workers=4,
        duration=120,
        connections=100,
        threads=4,
        load_tool="pounce-fixed-rate",
        load_tool_version="builtin-v1",
        compare=False,
        servers=("pounce", "uvicorn", "hypercorn", "granian"),
        target_rps=1000,
    )

    assert artifact["target_rps"] == 1000
    assert artifact["comparison_target"] == ["uvicorn", "hypercorn", "granian"]
    assert artifact["comparison_target_version"] == {
        "uvicorn": "uvicorn 1.2.3",
        "hypercorn": "hypercorn 1.2.3",
        "granian": "granian 1.2.3",
    }
    assert set(artifact["server_command"]) == {
        "pounce:hello",
        "uvicorn:hello",
        "hypercorn:hello",
        "granian:hello",
    }


def test_build_artifact_separates_samples_from_raw_output() -> None:
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
                "load_tool_stdout": "Requests/sec: 1000.00\n",
                "load_tool_stderr": "",
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
        compare=False,
    )

    assert "load_tool_stdout" not in artifact["samples"][0]
    assert artifact["raw_output"] == [
        {
            "server": "pounce",
            "workload": "chirp",
            "workers": 4,
            "sample_index": 1,
            "load_tool": "wrk",
            "stdout": "Requests/sec: 1000.00\n",
            "stderr": "",
        }
    ]


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


# ── Process telemetry (#139) ─────────────────────────────────────────


def test_sample_process_stats_parses_ps_output(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        stdout = "  100  20480  12.5\n  101  10240   7.5\n"

    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: _Result())
    stats = _sample_process_stats([100, 101])
    assert stats[100] == {"rss_bytes": 20480 * 1024.0, "cpu_percent": 12.5}
    assert stats[101] == {"rss_bytes": 10240 * 1024.0, "cpu_percent": 7.5}


def test_sample_process_stats_empty_for_no_pids() -> None:
    assert _sample_process_stats([]) == {}


def test_process_tree_pids_includes_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_child_pids_proc", lambda pid: [])
    monkeypatch.setattr(runner, "_child_pids_ps", lambda pid: [200, 201])
    assert runner._process_tree_pids(100) == [100, 200, 201]


def test_telemetry_sampler_aggregates_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_process_tree_pids", lambda pid: [10, 11])
    monkeypatch.setattr(
        runner,
        "_sample_process_stats",
        lambda pids: {
            10: {"rss_bytes": 30_000_000.0, "cpu_percent": 60.0},
            11: {"rss_bytes": 20_000_000.0, "cpu_percent": 40.0},
        },
    )
    sampler = _TelemetrySampler(10, interval=0.01)
    sampler._poll_once()
    result = sampler.result()
    assert result.supported is True
    # Peak RSS is summed across the whole tree (child workers included).
    assert result.peak_rss_bytes == 50_000_000
    # CPU% is aggregated across the tree per sample.
    assert result.cpu_percent_peak == 100.0
    assert result.cpu_percent_mean == 100.0
    assert result.worker_pids == [10, 11]
    assert result.interval_seconds == 0.01
    [point] = result.process_cpu_series
    assert point["rss_bytes_total"] == 50_000_000
    assert point["cpu_percent_total"] == 100.0
    assert point["processes"] == [
        {"pid": 10, "role": "root", "rss_bytes": 30_000_000, "cpu_percent": 60.0},
        {"pid": 11, "role": "child", "rss_bytes": 20_000_000, "cpu_percent": 40.0},
    ]


def test_telemetry_sampler_degrades_when_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_process_tree_pids", lambda pid: [10])
    monkeypatch.setattr(runner, "_sample_process_stats", lambda pids: {})
    sampler = _TelemetrySampler(10, interval=0.01)
    sampler._poll_once()
    result = sampler.result()
    assert result.supported is False
    assert result.peak_rss_bytes is None
    assert result.cpu_percent_peak is None
    assert result.worker_pids == []
    assert result.process_cpu_series == []


def test_telemetry_block_reports_peak_and_cpu() -> None:
    samples = [
        {
            "peak_rss_bytes": 50_000_000,
            "cpu_percent_mean": 80.0,
            "cpu_percent_peak": 120.0,
            "worker_pids": [10, 11],
            "telemetry_interval_seconds": 0.2,
            "process_cpu_series": [
                {
                    "elapsed_seconds": 0.2,
                    "rss_bytes_total": 50_000_000,
                    "cpu_percent_total": 120.0,
                    "processes": [
                        {
                            "pid": 10,
                            "role": "root",
                            "rss_bytes": 30_000_000,
                            "cpu_percent": 20.0,
                        },
                        {
                            "pid": 11,
                            "role": "child",
                            "rss_bytes": 20_000_000,
                            "cpu_percent": 100.0,
                        },
                    ],
                }
            ],
        },
        {
            "peak_rss_bytes": 70_000_000,
            "cpu_percent_mean": 90.0,
            "cpu_percent_peak": 150.0,
            "worker_pids": [10, 12],
        },
    ]
    block = _telemetry_block(samples)
    assert block["supported"] is True
    assert block["peak_rss_bytes"] == 70_000_000
    assert block["cpu_percent"]["peak"] == 150.0
    assert block["cpu_percent"]["mean"] == 85.0
    assert block["worker_pids"] == [10, 11, 12]
    [series] = block["process_cpu_series"]
    assert series["server"] == "unknown"
    assert series["interval_seconds"] == 0.2
    assert series["points"][0]["processes"][1]["pid"] == 11


def test_telemetry_block_unsupported_when_no_telemetry() -> None:
    block = _telemetry_block([{"req_per_sec": 1000.0}])
    assert block["supported"] is False
    assert block["peak_rss_bytes"] is None
    assert block["cpu_percent"]["peak"] is None
    assert block["worker_pids"] == []
    assert block["process_cpu_series"] == []


def test_build_artifact_includes_telemetry_and_summary_fields() -> None:
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
                "peak_rss_bytes": 60_000_000,
                "cpu_percent_mean": 80.0,
                "cpu_percent_peak": 120.0,
                "worker_pids": [10, 11, 12],
                "telemetry_interval_seconds": 0.2,
                "process_cpu_series": [
                    {
                        "elapsed_seconds": 0.2,
                        "rss_bytes_total": 60_000_000,
                        "cpu_percent_total": 120.0,
                        "processes": [
                            {
                                "pid": 10,
                                "role": "root",
                                "rss_bytes": 20_000_000,
                                "cpu_percent": 20.0,
                            },
                            {
                                "pid": 11,
                                "role": "child",
                                "rss_bytes": 20_000_000,
                                "cpu_percent": 50.0,
                            },
                            {
                                "pid": 12,
                                "role": "child",
                                "rss_bytes": 20_000_000,
                                "cpu_percent": 50.0,
                            },
                        ],
                    }
                ],
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
        compare=False,
    )

    assert artifact["telemetry"]["supported"] is True
    assert artifact["telemetry"]["peak_rss_bytes"] == 60_000_000
    assert artifact["telemetry"]["cpu_percent"]["peak"] == 120.0
    assert artifact["telemetry"]["worker_pids"] == [10, 11, 12]
    assert artifact["artifact_schema_version"] == 2
    assert "process_cpu_series" not in artifact["samples"][0]
    [series] = artifact["telemetry"]["process_cpu_series"]
    assert series["sample_index"] == 1
    assert series["points"][0]["processes"][2]["pid"] == 12
    validate_artifact(artifact)

    [group] = artifact["summary"]["groups"]
    assert group["peak_rss_bytes"]["max"] == 60_000_000
    assert group["cpu_percent"]["max"] == 120.0
    assert group["worker_pids"] == [10, 11, 12]


def test_save_artifact_rejects_incomplete_process_series(tmp_path: Path) -> None:
    artifact = build_profile_artifact(
        profile="process-cpu",
        command=["python", "benchmarks/run_benchmark.py"],
        server_command={"pounce": "python -m pounce serve ..."},
        samples=[
            {
                "server": "pounce",
                "workload": "hello",
                "workers": 2,
                "sample_index": 1,
                "process_cpu_series": [
                    {
                        "elapsed_seconds": 0.2,
                        "rss_bytes_total": 100,
                        "cpu_percent_total": 50.0,
                        "processes": [
                            {
                                "pid": 10,
                                "role": "root",
                                "rss_bytes": 100,
                                "cpu_percent": 50.0,
                            }
                        ],
                    }
                ],
                "telemetry_interval_seconds": 0.2,
            }
        ],
        workers=2,
        duration=1,
        connections=1,
        threads=1,
        load_tool="wrk",
        load_tool_version="wrk 4.2.0",
        worker_mode="process",
    )
    invalid = deepcopy(artifact)
    del invalid["telemetry"]["process_cpu_series"][0]["points"][0]["processes"][0]["cpu_percent"]
    output = tmp_path / "invalid.json"

    with pytest.raises(ValueError, match="missing required fields: cpu_percent"):
        save_artifact(invalid, output)

    assert not output.exists()


# ── Regression gate (#140) ───────────────────────────────────────────


def _grouped_artifact(
    *,
    req_per_sec_median: float,
    p99_median: float,
    sample_count: int = 5,
    server: str = "pounce",
    workload: str = "chirp",
    workers: int = 1,
) -> dict:
    """Build a minimal artifact with one variance group for gate tests."""
    group = {
        "server": server,
        "workload": workload,
        "workers": workers,
        "sample_count": sample_count,
        "req_per_sec": {"median": req_per_sec_median},
        "p99_latency_ms": {"median": p99_median},
    }
    return {"variance": {"sample_count": sample_count, "groups": [group]}}


def test_compare_artifact_passes_when_metrics_hold() -> None:
    baseline = _grouped_artifact(req_per_sec_median=10_000.0, p99_median=10.0)
    candidate = _grouped_artifact(req_per_sec_median=10_050.0, p99_median=9.8)
    report = compare_artifact(baseline, candidate)
    assert report.regressed is False
    assert len(report.comparisons) == 1
    assert report.regressions == []


def test_compare_artifact_fails_on_throughput_regression() -> None:
    baseline = _grouped_artifact(req_per_sec_median=10_000.0, p99_median=10.0)
    # 30% req/s drop, well beyond the default 10% tolerance.
    candidate = _grouped_artifact(req_per_sec_median=7_000.0, p99_median=10.0)
    report = compare_artifact(baseline, candidate)
    assert report.regressed is True
    [regression] = report.regressions
    assert regression.server == "pounce"
    assert regression.req_per_sec_change < -0.1
    assert any("req/s regressed" in reason for reason in regression.reasons)


def test_compare_artifact_fails_on_p99_latency_regression() -> None:
    baseline = _grouped_artifact(req_per_sec_median=10_000.0, p99_median=10.0)
    # p99 doubles — beyond the default 20% tolerance.
    candidate = _grouped_artifact(req_per_sec_median=10_000.0, p99_median=20.0)
    report = compare_artifact(baseline, candidate)
    assert report.regressed is True
    [regression] = report.regressions
    assert regression.p99_latency_change > 0.2
    assert any("p99 latency rose" in reason for reason in regression.reasons)


def test_compare_artifact_respects_custom_tolerance() -> None:
    baseline = _grouped_artifact(req_per_sec_median=10_000.0, p99_median=10.0)
    candidate = _grouped_artifact(req_per_sec_median=9_400.0, p99_median=10.0)
    # 6% drop passes the default 10% gate...
    assert compare_artifact(baseline, candidate).regressed is False
    # ...but fails a tightened 5% gate.
    assert compare_artifact(baseline, candidate, rps_tolerance=0.05).regressed is True


def test_compare_artifact_skips_snapshot_groups() -> None:
    baseline = _grouped_artifact(req_per_sec_median=10_000.0, p99_median=10.0, sample_count=1)
    # Massive regression, but the baseline is a 1-sample snapshot -> skipped.
    candidate = _grouped_artifact(req_per_sec_median=1.0, p99_median=999.0, sample_count=5)
    report = compare_artifact(baseline, candidate)
    assert report.regressed is False
    assert report.comparisons == []
    assert len(report.skipped) == 1
    assert "snapshot" in report.skipped[0]["reason"]


def test_compare_artifact_reports_groups_missing_from_baseline() -> None:
    baseline = _grouped_artifact(req_per_sec_median=10_000.0, p99_median=10.0, workload="chirp")
    candidate = _grouped_artifact(req_per_sec_median=5_000.0, p99_median=99.0, workload="bengal")
    report = compare_artifact(baseline, candidate)
    # A candidate group with no baseline counterpart cannot gate.
    assert report.regressed is False
    assert report.comparisons == []
    assert report.missing == [{"server": "pounce", "workload": "bengal", "workers": 1}]


def test_compare_artifact_end_to_end_against_committed_baseline() -> None:
    """The chirp local-snapshot artifact compared to a deliberately regressed copy."""
    import copy
    import json

    baseline = json.loads(
        Path("benchmarks/artifacts/2026-05-22/chirp-pounce-local.json").read_text()
    )
    candidate = copy.deepcopy(baseline)
    group = candidate["variance"]["groups"][0]
    group["req_per_sec"]["median"] = group["req_per_sec"]["median"] * 0.5
    report = compare_artifact(baseline, candidate)
    assert report.regressed is True


# ── Generic profile artifact builder (#141) ──────────────────────────


def test_build_profile_artifact_has_required_schema_fields() -> None:
    samples = [
        {
            "server": "thread",
            "workload": "worker_mode_comparison",
            "workers": 2,
            "req_per_sec": 2000.0,
            "p99_latency_ms": 5.0,
            "errors": 0,
            "sample_index": 1,
        },
        {
            "server": "subinterpreter",
            "workload": "worker_mode_comparison",
            "workers": 2,
            "req_per_sec": 3500.0,
            "p99_latency_ms": 3.0,
            "errors": 0,
            "sample_index": 1,
        },
    ]
    artifact = build_profile_artifact(
        profile="worker_mode_comparison",
        command=["python", "-m", "benchmarks.worker_modes"],
        server_command={"thread": "python -m pounce serve ..."},
        samples=samples,
        workers=2,
        duration=0,
        connections=10,
        threads=0,
        load_tool="worker_modes.py",
        load_tool_version="in-process driver",
        worker_mode="comparison",
    )
    required = {
        "artifact_schema_version",
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
        "telemetry",
        "variance",
        "raw_output",
        "summary",
    }
    assert required <= artifact.keys()
    assert artifact["workload"] == "worker_mode_comparison"
    assert artifact["worker_mode"] == "comparison"
    # One variance group per mode, so the gate can diff modes against a baseline.
    servers = {g["server"] for g in artifact["variance"]["groups"]}
    assert servers == {"thread", "subinterpreter"}


def test_build_profile_artifact_is_gate_comparable() -> None:
    """A profile artifact built by build_profile_artifact feeds the gate."""
    baseline = build_profile_artifact(
        profile="streaming",
        command=["python", "-m", "benchmarks.streaming_profile"],
        server_command={"pounce:streaming": "python -m pounce serve ..."},
        samples=[
            {
                "server": "pounce",
                "workload": "streaming",
                "workers": 1,
                "req_per_sec": 100.0,
                "p99_latency_ms": 5.0,
                "sample_index": i,
            }
            for i in range(1, 4)
        ],
        workers=1,
        duration=10,
        connections=100,
        threads=1,
        load_tool="streaming_profile.py",
        load_tool_version="driver",
    )
    candidate = build_profile_artifact(
        profile="streaming",
        command=["python", "-m", "benchmarks.streaming_profile"],
        server_command={"pounce:streaming": "python -m pounce serve ..."},
        samples=[
            {
                "server": "pounce",
                "workload": "streaming",
                "workers": 1,
                "req_per_sec": 50.0,  # event rate halved -> regression
                "p99_latency_ms": 5.0,
                "sample_index": i,
            }
            for i in range(1, 4)
        ],
        workers=1,
        duration=10,
        connections=100,
        threads=1,
        load_tool="streaming_profile.py",
        load_tool_version="driver",
    )
    assert compare_artifact(baseline, candidate).regressed is True
