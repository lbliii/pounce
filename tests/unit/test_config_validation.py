"""Validation-branch coverage for :class:`pounce.config.ServerConfig`.

``__post_init__`` rejects out-of-range and malformed values with a specific
``ValueError`` message. The existing suite covers the common ones; these tests
fill the remaining branches so a future edit that drops or weakens a guard
fails loudly. Each test pins the exact message fragment, so removing the guard
(or changing its message) is caught.
"""

from __future__ import annotations

import pytest

from pounce.config import ServerConfig


class TestLimitValidation:
    def test_backlog_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="backlog must be > 0"):
            ServerConfig(backlog=0)

    def test_max_connections_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError, match="max_connections must be >= 0"):
            ServerConfig(max_connections=-1)

    def test_max_header_size_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_header_size must be > 0"):
            ServerConfig(max_header_size=0)

    def test_max_headers_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_headers must be > 0"):
            ServerConfig(max_headers=0)

    def test_max_request_size_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="max_request_size must be > 0"):
            ServerConfig(max_request_size=0)

    def test_compression_min_size_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError, match="compression_min_size must be >= 0"):
            ServerConfig(compression_min_size=-1)


class TestTimeoutValidation:
    def test_startup_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="startup_timeout must be > 0"):
            ServerConfig(startup_timeout=0)

    def test_request_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="request_timeout must be > 0"):
            ServerConfig(request_timeout=0)

    def test_log_slow_requests_threshold_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="log_slow_requests_threshold must be > 0"):
            ServerConfig(log_slow_requests_threshold=0)


class TestPathValidation:
    def test_metrics_path_must_start_with_slash(self) -> None:
        with pytest.raises(ValueError, match="metrics_path must start with /"):
            ServerConfig(metrics_path="noslash")

    def test_health_check_path_must_start_with_slash(self) -> None:
        with pytest.raises(ValueError, match="health_check_path must start with /"):
            ServerConfig(health_check_path="noslash")

    def test_introspection_path_must_start_with_slash(self) -> None:
        with pytest.raises(ValueError, match="introspection_path must start with /"):
            ServerConfig(introspection_path="noslash")

    def test_introspection_bind_must_be_non_empty(self) -> None:
        with pytest.raises(ValueError, match="introspection_bind must be a non-empty string"):
            ServerConfig(introspection_bind="")

    def test_uds_must_be_non_empty_when_set(self) -> None:
        with pytest.raises(ValueError, match="uds must be a non-empty path or None"):
            ServerConfig(uds="")


class TestRateLimitValidation:
    def test_requests_per_second_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="rate_limit_requests_per_second must be > 0"):
            ServerConfig(rate_limit_requests_per_second=0)

    def test_burst_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="rate_limit_burst must be > 0"):
            ServerConfig(rate_limit_burst=0)

    def test_max_tracked_ips_must_be_at_least_one(self) -> None:
        with pytest.raises(ValueError, match="rate_limit_max_tracked_ips must be >= 1"):
            ServerConfig(rate_limit_max_tracked_ips=0)


class TestRequestQueueValidation:
    def test_max_depth_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError, match="request_queue_max_depth must be >= 0"):
            ServerConfig(request_queue_max_depth=-1)

    def test_zero_depth_allowed(self) -> None:
        # 0 means "unbounded" and must be accepted.
        assert ServerConfig(request_queue_max_depth=0).request_queue_max_depth == 0


class TestSentrySampleRateValidation:
    @pytest.mark.parametrize("rate", [-0.1, 1.5])
    def test_traces_sample_rate_out_of_range(self, rate: float) -> None:
        with pytest.raises(ValueError, match="sentry_traces_sample_rate must be"):
            ServerConfig(sentry_traces_sample_rate=rate)

    @pytest.mark.parametrize("rate", [-0.1, 1.5])
    def test_profiles_sample_rate_out_of_range(self, rate: float) -> None:
        with pytest.raises(ValueError, match="sentry_profiles_sample_rate must be"):
            ServerConfig(sentry_profiles_sample_rate=rate)

    @pytest.mark.parametrize("rate", [0.0, 0.5, 1.0])
    def test_boundary_rates_accepted(self, rate: float) -> None:
        cfg = ServerConfig(sentry_traces_sample_rate=rate, sentry_profiles_sample_rate=rate)
        assert cfg.sentry_traces_sample_rate == rate
        assert cfg.sentry_profiles_sample_rate == rate


class TestHttp3Validation:
    def test_idle_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="http3_idle_timeout must be > 0"):
            ServerConfig(http3_idle_timeout=0)

    def test_qpack_max_table_capacity_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError, match="http3_qpack_max_table_capacity must be >= 0"):
            ServerConfig(http3_qpack_max_table_capacity=-1)


class TestSignageValidation:
    def test_invalid_signage_rejected(self) -> None:
        with pytest.raises(ValueError, match="signage must be one of"):
            ServerConfig(signage="not-a-real-signage")

    @pytest.mark.parametrize("value", ["full", "minimal", "off"])
    def test_valid_signage_accepted(self, value: str) -> None:
        assert ServerConfig(signage=value).signage == value

    def test_signage_is_case_and_whitespace_insensitive(self) -> None:
        # Validation strips/lowercases before checking the allowlist, so a
        # mixed-case padded value must be accepted (no ValueError raised).
        cfg = ServerConfig(signage="  Full  ")
        assert cfg.signage == "  Full  "
