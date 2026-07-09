"""Tests for the built-in sustained fixed-rate benchmark driver."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from benchmarks.fixed_rate import FixedRateRequest, _percentile_ms, run_fixed_rate

pytestmark = pytest.mark.issue(228)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        body = b"ok"
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def test_percentile_uses_nearest_rank() -> None:
    latencies = [1_000_000, 2_000_000, 3_000_000, 4_000_000]
    assert _percentile_ms(latencies, 0.50) == 2.0
    assert _percentile_ms(latencies, 0.999) == 4.0
    assert _percentile_ms([], 0.999) == 0.0


def test_fixed_rate_driver_reports_tail_latency_and_raw_schedule() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_fixed_rate(
            f"http://127.0.0.1:{server.server_port}/",
            duration=0.25,
            connections=2,
            rate=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert result["total_requests"] == 5
    assert result["errors"] == 0
    assert result["p50_latency_ms"] > 0
    assert result["p99_latency_ms"] >= result["p50_latency_ms"]
    assert result["p999_latency_ms"] >= result["p99_latency_ms"]
    assert '"target_rps": 20' in result["load_tool_stdout"]


@pytest.mark.issue(229)
def test_fixed_rate_driver_rotates_request_variants_with_headers() -> None:
    observed: list[tuple[bytes, str | None]] = []
    observed_lock = threading.Lock()

    class VariantHandler(_Handler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            body = self.rfile.read(length)
            with observed_lock:
                observed.append((body, self.headers.get("Mcp-Name")))
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), VariantHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_fixed_rate(
            f"http://127.0.0.1:{server.server_port}/mcp",
            duration=0.2,
            connections=2,
            rate=20,
            requests=(
                FixedRateRequest("POST", b'"cpu"', {"Mcp-Name": "cpu"}),
                FixedRateRequest("POST", b'"blocking"', {"Mcp-Name": "blocking"}),
            ),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert result["total_requests"] == 4
    assert result["errors"] == 0
    assert sorted(observed) == [
        (b'"blocking"', "blocking"),
        (b'"blocking"', "blocking"),
        (b'"cpu"', "cpu"),
        (b'"cpu"', "cpu"),
    ]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"duration": 0}, "duration must be > 0"),
        ({"connections": 0}, "connections must be >= 1"),
        ({"rate": 0}, "rate must be >= 1"),
        ({"requests": ()}, "requests must contain at least one"),
    ],
)
def test_fixed_rate_driver_validates_public_inputs(kwargs: dict, message: str) -> None:
    options = {"duration": 1, "connections": 1, "rate": 1}
    options.update(kwargs)
    with pytest.raises(ValueError, match=message):
        run_fixed_rate("http://127.0.0.1/", **options)
