"""Tests for startup observability of backpressure and worker mode.

Covers:
- Issue #109: a startup INFO line stating the EFFECTIVE aggregate when rate
  limiting / request queueing are enabled (aggregate = limit x workers in
  process/subinterpreter mode for the rate limiter, and in ALL modes for the
  per-worker request queue).
- Issue #157: a one-line INFO notice when the beta ``subinterpreter`` worker
  mode is actually resolved/used, plus the ``(beta)`` marker on the
  ``--worker-mode`` CLI help text.
"""

import logging

import pytest

from pounce._runtime import WorkerMode
from pounce.config import ServerConfig
from pounce.server import Server


async def _ok_app(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


class TestRateLimitAggregateLog:
    """Issue #109: rate-limit startup INFO line states the aggregate."""

    def test_process_mode_logs_rate_times_workers(self, caplog, monkeypatch) -> None:
        """Process/subinterpreter mode: aggregate per-IP ceiling = rate x workers."""
        config = ServerConfig(
            rate_limit_enabled=True,
            rate_limit_requests_per_second=100.0,
            rate_limit_burst=200,
            workers=4,
        )
        server = Server(config, _ok_app)
        # Force the per-worker (copy-per-worker) mode regardless of local GIL state.
        # Server has __slots__, so patch the module-level detector it delegates to.
        monkeypatch.setattr("pounce.server.detect_worker_mode", lambda: WorkerMode.PROCESS)

        with caplog.at_level(logging.INFO, logger="pounce"):
            server._apply_rate_limiter()

        # The aggregate line names the multiplier and the computed aggregate.
        assert "per worker x 4 workers" in caplog.text
        assert "400.0 req/s aggregate" in caplog.text

    def test_thread_mode_logs_shared_limiter(self, caplog, monkeypatch) -> None:
        """Thread mode: a single limiter is shared, so aggregate = configured."""
        config = ServerConfig(
            rate_limit_enabled=True,
            rate_limit_requests_per_second=100.0,
            workers=4,
        )
        server = Server(config, _ok_app)
        monkeypatch.setattr("pounce.server.detect_worker_mode", lambda: WorkerMode.THREAD)

        with caplog.at_level(logging.INFO, logger="pounce"):
            server._apply_rate_limiter()

        assert "shared across workers" in caplog.text
        assert "aggregate = configured" in caplog.text
        # Must NOT claim a per-worker multiplier in thread mode.
        assert "per worker x" not in caplog.text

    def test_no_aggregate_line_when_disabled(self, caplog) -> None:
        config = ServerConfig(rate_limit_enabled=False)
        server = Server(config, _ok_app)
        with caplog.at_level(logging.INFO, logger="pounce"):
            server._apply_rate_limiter()
        assert "Rate limiting" not in caplog.text


class TestRequestQueueAggregateLog:
    """Issue #109: request-queue startup INFO line states the aggregate."""

    def test_logs_depth_times_workers(self, caplog) -> None:
        """The queue is per-worker in ALL modes, so aggregate depth = depth x workers."""
        config = ServerConfig(
            request_queue_enabled=True,
            request_queue_max_depth=10,
            workers=3,
        )
        server = Server(config, _ok_app)

        with caplog.at_level(logging.INFO, logger="pounce"):
            server._apply_request_queue()

        assert "max depth 10 per worker x 3 workers" in caplog.text
        assert "30 aggregate queued" in caplog.text

    def test_no_aggregate_line_when_unbounded(self, caplog) -> None:
        """Unbounded depth (0) has no meaningful aggregate multiplier."""
        config = ServerConfig(
            request_queue_enabled=True,
            request_queue_max_depth=0,
            workers=3,
        )
        server = Server(config, _ok_app)
        with caplog.at_level(logging.INFO, logger="pounce"):
            server._apply_request_queue()
        assert "aggregate queued" not in caplog.text


class TestSubinterpreterNotice:
    """Issue #157: beta notice when subinterpreter worker mode is resolved."""

    def test_notice_emitted_for_subinterpreter(self, caplog) -> None:
        config = ServerConfig()
        server = Server(config, _ok_app)
        with caplog.at_level(logging.INFO, logger="pounce"):
            server._log_worker_mode_notice(WorkerMode.SUBINTERPRETER)
        assert "subinterpreter (beta)" in caplog.text

    @pytest.mark.parametrize("mode", [WorkerMode.THREAD, WorkerMode.PROCESS])
    def test_no_notice_for_stable_modes(self, caplog, mode) -> None:
        config = ServerConfig()
        server = Server(config, _ok_app)
        with caplog.at_level(logging.INFO, logger="pounce"):
            server._log_worker_mode_notice(mode)
        assert "subinterpreter" not in caplog.text

    def test_effective_worker_mode_honors_config(self) -> None:
        sub = Server(ServerConfig(worker_mode="subinterpreter"), _ok_app)
        assert sub._effective_worker_mode() is WorkerMode.SUBINTERPRETER
        # Non-subinterpreter config falls back to GIL-based detection
        # (PROCESS on a GIL build, THREAD on free-threaded).
        auto = Server(ServerConfig(worker_mode="auto"), _ok_app)
        assert auto._effective_worker_mode() in (WorkerMode.THREAD, WorkerMode.PROCESS)


class TestWorkerModeHelpText:
    """Issue #157: the --worker-mode CLI help marks subinterpreter as beta."""

    def test_serve_help_marks_subinterpreter_beta(self) -> None:
        from pounce._cli import _SERVE_HELP

        assert "subinterpreter (beta)" in _SERVE_HELP["worker_mode"]

    def test_check_inherits_beta_marker(self) -> None:
        # 'check' must expose the same flags/help as 'serve' (parity).
        from pounce._cli import _CHECK_HELP, _SERVE_HELP

        assert _CHECK_HELP["worker_mode"] == _SERVE_HELP["worker_mode"]
        assert "subinterpreter (beta)" in _CHECK_HELP["worker_mode"]
