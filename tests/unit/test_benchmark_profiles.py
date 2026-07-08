"""Unit tests for the streaming and worker-mode benchmark profiles (#141).

These cover the artifact-wiring logic only; the SSE/worker-spawn drivers
themselves are exercised under the ``benchmark`` marker, not here.
"""

from benchmarks.drain_profile import (
    _HUNG,
    _REFUSED,
    DRAIN_WORKLOAD,
    _classify_new_connection,
    drain_sample,
    summarize_drain,
)
from benchmarks.h3_profile import H3_WORKLOAD, summarize_http3
from benchmarks.run_benchmark import build_profile_artifact, compare_artifact
from benchmarks.streaming_profile import summarize_streams
from benchmarks.worker_modes import (
    WORKER_MODE_WORKLOAD,
    _mode_result_to_sample,
    build_worker_mode_artifact,
)

# ── Worker-mode comparison ───────────────────────────────────────────


def _mode_row(mode: str, rps: float) -> dict:
    return {
        "mode": mode,
        "workers": 2,
        "requests": 200,
        "errors": 0,
        "elapsed_s": 1.0,
        "rps": rps,
        "latency_avg_ms": 2.0,
        "latency_p50_ms": 1.5,
        "latency_p99_ms": 4.0,
        "rss_before_mb": 50.0,
        "rss_after_mb": 51.0,
        "rss_delta_mb": 1.0,
    }


def test_mode_result_to_sample_maps_mode_to_server() -> None:
    sample = _mode_result_to_sample(_mode_row("thread", 2000.0), sample_index=3)
    assert sample is not None
    assert sample["server"] == "thread"
    assert sample["workload"] == WORKER_MODE_WORKLOAD
    assert sample["req_per_sec"] == 2000.0
    assert sample["p99_latency_ms"] == 4.0
    assert sample["sample_index"] == 3
    # 1.0 MB RSS delta surfaced as bytes for grouped variance.
    assert sample["server_rss_bytes"] == 1024 * 1024


def test_mode_result_to_sample_skips_errored_mode() -> None:
    assert (
        _mode_result_to_sample({"mode": "subinterpreter", "error": "not available"}, sample_index=1)
        is None
    )


def test_build_worker_mode_artifact_groups_per_mode() -> None:
    samples = [
        _mode_result_to_sample(_mode_row("thread", 2000.0), sample_index=1),
        _mode_result_to_sample(_mode_row("subinterpreter", 3500.0), sample_index=1),
    ]
    samples = [s for s in samples if s is not None]
    artifact = build_worker_mode_artifact(samples, workers=2, requests=200, concurrency=10)

    assert artifact["workload"] == WORKER_MODE_WORKLOAD
    assert artifact["worker_mode"] == "comparison"
    assert set(artifact["server_command"]) == {"thread", "subinterpreter"}
    servers = {g["server"] for g in artifact["variance"]["groups"]}
    assert servers == {"thread", "subinterpreter"}


def test_worker_mode_artifact_detects_regression() -> None:
    """A worker-mode artifact is gate-comparable across runs."""

    def _artifact(thread_rps: float) -> dict:
        samples = [
            s
            for s in (
                _mode_result_to_sample(_mode_row("thread", thread_rps), sample_index=i)
                for i in range(1, 4)
            )
            if s is not None
        ]
        return build_worker_mode_artifact(samples, workers=2, requests=200, concurrency=10)

    baseline = _artifact(3000.0)
    candidate = _artifact(1500.0)  # thread mode RPS halved
    assert compare_artifact(baseline, candidate).regressed is True


# ── Sustained streaming ──────────────────────────────────────────────


def test_summarize_streams_aggregates_per_stream_metrics() -> None:
    stream_results = [
        {"events": 10, "ttfb_ms": 20.0, "inter_event_ms": 1000.0, "connected": 1},
        {"events": 12, "ttfb_ms": 30.0, "inter_event_ms": 1010.0, "connected": 1},
        {"events": 0, "ttfb_ms": 0.0, "inter_event_ms": 0.0, "connected": 0},
    ]
    summary = summarize_streams(stream_results, duration=10.0)
    assert summary["events_total"] == 22
    assert summary["streams_connected"] == 2
    assert summary["event_rate_per_sec"] == 2.2  # 22 events / 10s
    # p99 TTFB is the worst of the two connected streams.
    assert summary["ttfb_p99_ms"] == 30.0
    assert summary["inter_event_median_ms"] == 1005.0


def test_summarize_streams_handles_no_connections() -> None:
    summary = summarize_streams(
        [{"events": 0, "ttfb_ms": 0.0, "inter_event_ms": 0.0, "connected": 0}],
        duration=5.0,
    )
    assert summary["events_total"] == 0
    assert summary["streams_connected"] == 0
    assert summary["event_rate_per_sec"] == 0.0
    assert summary["ttfb_p99_ms"] == 0.0


# ── HTTP/3 profile (#240) ───────────────────────────────────────────


def test_summarize_http3_aggregates_connections_and_latency() -> None:
    summary = summarize_http3(
        [
            {
                "requests": 2,
                "errors": 0,
                "response_bytes": 26,
                "latencies_ms": [1.0, 4.0],
            },
            {
                "requests": 1,
                "errors": 1,
                "response_bytes": 13,
                "latencies_ms": [2.0],
            },
        ],
        duration=2.0,
    )
    assert H3_WORKLOAD == "http3_hello"
    assert summary["connections"] == 2
    assert summary["successful_connections"] == 2
    assert summary["requests"] == 3
    assert summary["errors"] == 1
    assert summary["response_bytes"] == 39
    assert summary["req_per_sec"] == 1.5
    assert summary["latency_avg_ms"] == 2.333
    assert summary["latency_p50_ms"] == 2.0
    assert summary["latency_p99_ms"] == 4.0


def test_summarize_http3_handles_no_completed_requests() -> None:
    summary = summarize_http3(
        [{"requests": 0, "errors": 1, "response_bytes": 0, "latencies_ms": []}],
        duration=5.0,
    )
    assert summary["successful_connections"] == 0
    assert summary["req_per_sec"] == 0.0
    assert summary["latency_p99_ms"] == 0.0


# ── Reload/drain under load (#141) ───────────────────────────────────


def _clean_inflight() -> dict:
    """In-flight results where every /slow and /stream request completed."""
    return {
        "slow0": b"HTTP/1.1 200 OK\r\n\r\nslow-done",
        "slow1": b"HTTP/1.1 200 OK\r\n\r\nslow-done",
        "stream": b"HTTP/1.1 200 OK\r\n\r\nchunk-0\nchunk-1\nchunk-2\n",
    }


def test_classify_new_connection_distinguishes_refusal_from_drop() -> None:
    assert _classify_new_connection(_HUNG) == "hung"
    assert _classify_new_connection(b"") == "clean_close"
    assert _classify_new_connection(_REFUSED) == "clean_close"
    assert _classify_new_connection(b"HTTP/1.1 503 Service Unavailable\r\n\r\n") == "refused_503"
    assert _classify_new_connection(b"HTTP/1.1 200 OK\r\n\r\nfast-ok") == "served_200"
    assert _classify_new_connection(b"\x01\x02garbled") == "garbage"


def test_summarize_drain_clean_drain_when_all_contracts_hold() -> None:
    drain = summarize_drain(
        inflight_results=_clean_inflight(),
        inflight_expected=3,
        new_conn_results=[_REFUSED, b"", b"HTTP/1.1 503 Service Unavailable\r\n\r\n"],
        drain_duration_s=1.5,
        shutdown_timeout=3,
        returncode=0,
        orphan_pids=[],
    )
    assert drain["inflight_completed"] == 3
    assert drain["inflight_completion_rate"] == 1.0
    # All three new connections cleanly refused, none silently dropped.
    assert drain["refusals"] == 3
    assert drain["disconnect_rate"] == 1.0
    assert drain["silent_drops"] == 0
    assert drain["drop_rate"] == 0.0
    assert drain["orphan_workers"] == 0
    assert drain["exited_within_timeout"] is True
    assert drain["clean_drain"] is True


def test_summarize_drain_flags_silent_drop_as_unclean() -> None:
    """A hung (accepted-then-dropped) new connection breaks the clean-drain contract."""
    drain = summarize_drain(
        inflight_results=_clean_inflight(),
        inflight_expected=3,
        new_conn_results=[_REFUSED, _HUNG],
        drain_duration_s=1.0,
        shutdown_timeout=3,
        returncode=0,
        orphan_pids=[],
    )
    assert drain["silent_drops"] == 1
    assert drain["new_connection_buckets"]["hung"] == 1
    assert drain["drop_rate"] == 0.5
    assert drain["clean_drain"] is False


def test_summarize_drain_flags_orphan_and_incomplete_inflight() -> None:
    drain = summarize_drain(
        inflight_results={
            "slow0": b"HTTP/1.1 200 OK\r\n\r\nslow-done",
            "slow1": b"",  # dropped before completing
            "stream": b"HTTP/1.1 200 OK\r\n\r\nchunk-0\nchunk-1\nchunk-2\n",
        },
        inflight_expected=3,
        new_conn_results=[b""],
        drain_duration_s=2.0,
        shutdown_timeout=3,
        returncode=0,
        orphan_pids=[4242],
    )
    assert drain["inflight_completed"] == 2
    assert drain["inflight_completion_rate"] == round(2 / 3, 4)
    assert drain["orphan_workers"] == 1
    assert drain["clean_drain"] is False


def test_summarize_drain_nonzero_exit_is_unclean() -> None:
    drain = summarize_drain(
        inflight_results=_clean_inflight(),
        inflight_expected=3,
        new_conn_results=[b""],
        drain_duration_s=99.0,  # never exited within timeout
        shutdown_timeout=3,
        returncode=None,
        orphan_pids=[],
    )
    assert drain["exited_within_timeout"] is False
    assert drain["clean_drain"] is False


def test_drain_sample_maps_mode_to_server_and_drain_health_to_metrics() -> None:
    drain = summarize_drain(
        inflight_results=_clean_inflight(),
        inflight_expected=3,
        new_conn_results=[_REFUSED, b""],
        drain_duration_s=1.5,
        shutdown_timeout=3,
        returncode=0,
        orphan_pids=[],
    )
    sample = drain_sample(drain, worker_mode="subinterpreter", workers=2, sample_index=4)
    assert sample["server"] == "subinterpreter"
    assert sample["workload"] == DRAIN_WORKLOAD
    assert sample["sample_index"] == 4
    # Completion rate is the headline req_per_sec; drain duration is the p99.
    assert sample["req_per_sec"] == 1.0
    assert sample["p99_latency_ms"] == 1500.0
    # Silent drops surface as errors so the regression gate flags a leaky drain.
    assert sample["errors"] == 0
    assert sample["total_requests"] == 3
    assert sample["drain"]["clean_drain"] is True


def _drain_artifact(*, completion_rate: float, drain_ms: float) -> dict:
    """Build a 3-sample drain artifact with a fixed per-mode drain health."""
    samples = [
        {
            "server": "async",
            "workload": DRAIN_WORKLOAD,
            "workers": 2,
            "duration_s": 1,
            "threads": 0,
            "connections": 10,
            "req_per_sec": completion_rate,
            "avg_latency_ms": drain_ms,
            "p50_latency_ms": drain_ms,
            "p99_latency_ms": drain_ms,
            "transfer_per_sec": "",
            "total_requests": 5,
            "errors": 0,
            "sample_index": i,
        }
        for i in range(1, 4)
    ]
    return build_profile_artifact(
        profile=DRAIN_WORKLOAD,
        command=["python", "benchmarks/drain_profile.py"],
        server_command={"pounce:async": "python -m pounce serve --app drain_probe"},
        samples=samples,
        workers=2,
        duration=3,
        connections=10,
        threads=0,
        load_tool="drain_profile.py",
        load_tool_version="in-process SIGHUP+SIGTERM drain driver",
        worker_mode="async",
    )


def test_drain_artifact_groups_per_mode_and_is_schema_shaped() -> None:
    artifact = _drain_artifact(completion_rate=1.0, drain_ms=1500.0)
    assert artifact["workload"] == DRAIN_WORKLOAD
    assert artifact["worker_mode"] == "async"
    servers = {g["server"] for g in artifact["variance"]["groups"]}
    assert servers == {"async"}
    # Every schema-required field is present so the gate can consume it.
    schema_required = {
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
    assert schema_required <= set(artifact)


def test_drain_artifact_detects_drain_regression() -> None:
    """A drain whose in-flight completion rate collapses is gate-comparable."""
    baseline = _drain_artifact(completion_rate=1.0, drain_ms=1500.0)
    # Completion rate halved AND drain duration ballooned past the p99 tolerance.
    candidate = _drain_artifact(completion_rate=0.4, drain_ms=9000.0)
    assert compare_artifact(baseline, candidate).regressed is True
