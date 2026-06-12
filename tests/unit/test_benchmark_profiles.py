"""Unit tests for the streaming and worker-mode benchmark profiles (#141).

These cover the artifact-wiring logic only; the SSE/worker-spawn drivers
themselves are exercised under the ``benchmark`` marker, not here.
"""

from benchmarks.run_benchmark import compare_artifact
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
