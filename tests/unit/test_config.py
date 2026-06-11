"""Tests for pounce.config — ServerConfig immutable dataclass."""

from dataclasses import FrozenInstanceError

import hypothesis
import hypothesis.strategies as st
import pytest

from pounce.config import ServerConfig


class TestServerConfigDefaults:
    """ServerConfig provides sensible defaults for all fields."""

    def test_default_host(self):
        config = ServerConfig()
        assert config.host == "127.0.0.1"

    def test_default_port(self):
        config = ServerConfig()
        assert config.port == 8000

    def test_default_workers(self):
        config = ServerConfig()
        assert config.workers == 1

    def test_default_backlog(self):
        config = ServerConfig()
        assert config.backlog == 2048

    def test_default_timeouts(self):
        config = ServerConfig()
        assert config.keep_alive_timeout == 5.0
        assert config.request_timeout == 30.0
        assert config.shutdown_timeout == 10.0

    def test_default_limits(self):
        config = ServerConfig()
        assert config.max_request_size == 1_048_576
        assert config.max_header_size == 65_536
        assert config.max_headers == 100
        assert config.max_connections == 10_000
        assert config.max_requests_per_connection == 0  # 0 = unlimited

    def test_default_logging(self):
        config = ServerConfig()
        assert config.access_log is True
        assert config.log_level == "info"

    def test_default_display(self):
        config = ServerConfig()
        assert config.display is None

    def test_default_http(self):
        config = ServerConfig()
        assert config.server_header == "pounce"
        assert config.date_header is True
        assert config.root_path == ""

    def test_default_compression(self):
        config = ServerConfig()
        assert config.compression is True
        assert config.compression_min_size == 500

    def test_default_server_timing(self):
        config = ServerConfig()
        assert config.server_timing is False

    def test_default_reload(self):
        config = ServerConfig()
        assert config.reload is False

    def test_default_reload_include(self):
        config = ServerConfig()
        assert config.reload_include == ()

    def test_default_reload_dirs(self):
        config = ServerConfig()
        assert config.reload_dirs == ()

    def test_default_h11(self):
        config = ServerConfig()
        assert config.h11_max_incomplete_event_size is None

    def test_default_trusted_hosts(self):
        config = ServerConfig()
        assert config.trusted_hosts == frozenset()

    def test_default_forwarded_for_trusted_hops(self):
        config = ServerConfig()
        assert config.forwarded_for_trusted_hops == 1

    def test_custom_forwarded_for_trusted_hops(self):
        config = ServerConfig(forwarded_for_trusted_hops=2)
        assert config.forwarded_for_trusted_hops == 2

    def test_forwarded_for_trusted_hops_validation(self):
        with pytest.raises(ValueError, match="forwarded_for_trusted_hops must be >= 1"):
            ServerConfig(forwarded_for_trusted_hops=0)

    def test_default_ssl(self):
        config = ServerConfig()
        assert config.ssl_certfile is None
        assert config.ssl_keyfile is None

    def test_default_worker_mode(self):
        config = ServerConfig()
        assert config.worker_mode == "auto"

    def test_default_worker_startup_failure(self):
        config = ServerConfig()
        assert config.worker_startup_failure == "ignore"


class TestServerConfigOverrides:
    """ServerConfig fields can be overridden at construction."""

    def test_custom_host_port(self):
        config = ServerConfig(host="0.0.0.0", port=9000)
        assert config.host == "0.0.0.0"
        assert config.port == 9000

    def test_custom_workers(self):
        config = ServerConfig(workers=4)
        assert config.workers == 4

    def test_custom_timeouts(self):
        config = ServerConfig(
            keep_alive_timeout=10.0,
            request_timeout=60.0,
            shutdown_timeout=30.0,
        )
        assert config.keep_alive_timeout == 10.0
        assert config.request_timeout == 60.0
        assert config.shutdown_timeout == 30.0

    def test_custom_root_path(self):
        config = ServerConfig(root_path="/api/v1")
        assert config.root_path == "/api/v1"

    def test_compression_disabled(self):
        config = ServerConfig(compression=False)
        assert config.compression is False

    def test_server_timing_enabled(self):
        config = ServerConfig(server_timing=True)
        assert config.server_timing is True

    def test_custom_trusted_hosts(self):
        config = ServerConfig(trusted_hosts=("10.0.0.1", "10.0.0.2"))
        assert config.trusted_hosts == frozenset({"10.0.0.1", "10.0.0.2"})

    def test_ssl_paths(self):
        config = ServerConfig(
            ssl_certfile="/path/to/cert.pem",
            ssl_keyfile="/path/to/key.pem",
        )
        assert config.ssl_certfile == "/path/to/cert.pem"
        assert config.ssl_keyfile == "/path/to/key.pem"

    def test_custom_reload_include(self):
        config = ServerConfig(reload_include=(".html", ".css", ".md"))
        assert config.reload_include == (".html", ".css", ".md")

    def test_custom_reload_dirs(self):
        config = ServerConfig(reload_dirs=("./templates", "./static"))
        assert config.reload_dirs == ("./templates", "./static")

    def test_custom_max_requests_per_connection(self):
        config = ServerConfig(max_requests_per_connection=1000)
        assert config.max_requests_per_connection == 1000

    def test_custom_keep_alive_timeout(self):
        config = ServerConfig(keep_alive_timeout=30.0)
        assert config.keep_alive_timeout == 30.0

    def test_custom_worker_mode(self):
        config = ServerConfig(worker_mode="sync")
        assert config.worker_mode == "sync"


class TestServerConfigFrozen:
    """ServerConfig is immutable (frozen=True)."""

    def test_cannot_change_host(self):
        config = ServerConfig()
        with pytest.raises(FrozenInstanceError):
            config.host = "0.0.0.0"  # type: ignore[misc]

    def test_cannot_change_port(self):
        config = ServerConfig()
        with pytest.raises(FrozenInstanceError):
            config.port = 9000  # type: ignore[misc]

    def test_cannot_change_workers(self):
        config = ServerConfig()
        with pytest.raises(FrozenInstanceError):
            config.workers = 4  # type: ignore[misc]

    def test_cannot_change_compression(self):
        config = ServerConfig()
        with pytest.raises(FrozenInstanceError):
            config.compression = False  # type: ignore[misc]


class TestServerConfigEquality:
    """ServerConfig supports equality comparisons."""

    def test_equal_configs(self):
        a = ServerConfig()
        b = ServerConfig()
        assert a == b

    def test_different_configs(self):
        a = ServerConfig(port=8000)
        b = ServerConfig(port=9000)
        assert a != b


class TestServerConfigValidation:
    """ServerConfig validates input values."""

    def test_negative_workers_raises(self):
        with pytest.raises(ValueError, match="workers must be >= 0"):
            ServerConfig(workers=-1)

    def test_zero_workers_allowed(self):
        config = ServerConfig(workers=0)
        assert config.workers == 0

    def test_positive_workers_allowed(self):
        config = ServerConfig(workers=4)
        assert config.workers == 4

    def test_negative_port_raises(self):
        with pytest.raises(ValueError, match="port must be 0-65535"):
            ServerConfig(port=-1)

    def test_port_too_high_raises(self):
        with pytest.raises(ValueError, match="port must be 0-65535"):
            ServerConfig(port=70000)

    def test_keep_alive_timeout_zero_raises(self):
        with pytest.raises(ValueError, match="keep_alive_timeout must be > 0"):
            ServerConfig(keep_alive_timeout=0.0)

    def test_keep_alive_timeout_negative_raises(self):
        with pytest.raises(ValueError, match="keep_alive_timeout must be > 0"):
            ServerConfig(keep_alive_timeout=-1.0)

    def test_max_requests_per_connection_negative_raises(self):
        with pytest.raises(ValueError, match="max_requests_per_connection must be >= 0"):
            ServerConfig(max_requests_per_connection=-1)

    def test_invalid_worker_mode_raises(self):
        with pytest.raises(ValueError, match="worker_mode must be one of"):
            ServerConfig(worker_mode="invalid")

    def test_invalid_worker_startup_failure_raises(self):
        with pytest.raises(ValueError, match="worker_startup_failure must be one of"):
            ServerConfig(worker_startup_failure="explode")

    def test_worker_startup_failure_normalized(self):
        config = ServerConfig(worker_startup_failure="SHUTDOWN")
        assert config.worker_startup_failure == "shutdown"

    def test_ssl_certfile_without_keyfile_raises(self):
        with pytest.raises(ValueError, match="ssl_certfile and ssl_keyfile must both"):
            ServerConfig(ssl_certfile="/path/to/cert.pem")

    def test_ssl_keyfile_without_certfile_raises(self):
        with pytest.raises(ValueError, match="ssl_certfile and ssl_keyfile must both"):
            ServerConfig(ssl_keyfile="/path/to/key.pem")

    def test_http3_max_connections_zero_raises(self):
        with pytest.raises(ValueError, match="http3_max_connections must be > 0"):
            ServerConfig(http3_max_connections=0)

    def test_http3_max_connections_negative_raises(self):
        with pytest.raises(ValueError, match="http3_max_connections must be > 0"):
            ServerConfig(http3_max_connections=-1)

    def test_http3_idle_timeout_zero_raises(self):
        with pytest.raises(ValueError, match="http3_idle_timeout must be > 0"):
            ServerConfig(http3_idle_timeout=0.0)

    def test_http3_idle_timeout_negative_raises(self):
        with pytest.raises(ValueError, match="http3_idle_timeout must be > 0"):
            ServerConfig(http3_idle_timeout=-5.0)

    def test_http3_enabled_without_tls_raises(self):
        with pytest.raises(ValueError, match="http3_enabled requires"):
            ServerConfig(http3_enabled=True)

    def test_http3_enabled_with_uds_raises(self):
        with pytest.raises(ValueError, match="not supported with Unix domain sockets"):
            ServerConfig(
                http3_enabled=True,
                ssl_certfile="/tmp/cert.pem",
                ssl_keyfile="/tmp/key.pem",
                uds="/tmp/test.sock",
            )

    def test_http3_qpack_max_table_capacity_default(self):
        config = ServerConfig()
        assert config.http3_qpack_max_table_capacity == 0

    def test_http3_qpack_max_table_capacity_valid(self):
        config = ServerConfig(http3_qpack_max_table_capacity=4096)
        assert config.http3_qpack_max_table_capacity == 4096

    def test_http3_qpack_max_table_capacity_zero_valid(self):
        config = ServerConfig(http3_qpack_max_table_capacity=0)
        assert config.http3_qpack_max_table_capacity == 0

    def test_http3_qpack_max_table_capacity_negative_raises(self):
        with pytest.raises(ValueError, match="http3_qpack_max_table_capacity must be >= 0"):
            ServerConfig(http3_qpack_max_table_capacity=-1)

    def test_http3_zero_rtt_enabled_default(self):
        config = ServerConfig()
        assert config.http3_zero_rtt_enabled is False

    def test_http3_zero_rtt_enabled_true(self):
        config = ServerConfig(http3_zero_rtt_enabled=True)
        assert config.http3_zero_rtt_enabled is True


class TestServerConfigResolveWorkers:
    """resolve_workers() handles auto-detect and explicit values."""

    def test_explicit_workers(self):
        config = ServerConfig(workers=4)
        assert config.resolve_workers() == 4

    def test_single_worker(self):
        config = ServerConfig(workers=1)
        assert config.resolve_workers() == 1

    def test_auto_detect_returns_positive(self):
        config = ServerConfig(workers=0)
        result = config.resolve_workers()
        assert result >= 1

    def test_auto_detect_uses_cpu_count(self):
        from unittest.mock import patch

        config = ServerConfig(workers=0)
        with patch("pounce._runtime.os.cpu_count", return_value=8):
            assert config.resolve_workers() == 8


class TestExecutorThreadsPerWorker:
    """Tests for executor_threads_per_worker config field."""

    def test_default_is_zero(self):
        config = ServerConfig()
        assert config.executor_threads_per_worker == 0

    def test_custom_value(self):
        config = ServerConfig(executor_threads_per_worker=8)
        assert config.executor_threads_per_worker == 8

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="executor_threads_per_worker must be >= 0"):
            ServerConfig(executor_threads_per_worker=-1)

    def test_zero_allowed(self):
        config = ServerConfig(executor_threads_per_worker=0)
        assert config.executor_threads_per_worker == 0


class TestServerConfigSlots:
    """ServerConfig uses __slots__ for memory efficiency."""

    def test_no_dict(self):
        config = ServerConfig()
        assert not hasattr(config, "__dict__")

    def test_cannot_add_arbitrary_attrs(self):
        config = ServerConfig()
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            config.nonexistent = "value"  # type: ignore[attr-defined]


class TestNewConfigFields:
    """Tests for fields added in the production epic."""

    def test_default_header_timeout(self):
        config = ServerConfig()
        assert config.header_timeout == 10.0

    def test_custom_header_timeout(self):
        config = ServerConfig(header_timeout=30.0)
        assert config.header_timeout == 30.0

    def test_header_timeout_must_be_positive(self):
        with pytest.raises(ValueError, match="header_timeout"):
            ServerConfig(header_timeout=0)

    def test_header_timeout_negative_rejected(self):
        with pytest.raises(ValueError, match="header_timeout"):
            ServerConfig(header_timeout=-1.0)

    def test_default_uds_is_none(self):
        config = ServerConfig()
        assert config.uds is None

    def test_custom_uds(self):
        config = ServerConfig(uds="/run/pounce.sock")
        assert config.uds == "/run/pounce.sock"

    def test_empty_uds_rejected(self):
        with pytest.raises(ValueError, match="uds"):
            ServerConfig(uds="")

    def test_default_health_check_path_is_none(self):
        config = ServerConfig()
        assert config.health_check_path is None

    def test_custom_health_check_path(self):
        config = ServerConfig(health_check_path="/health")
        assert config.health_check_path == "/health"

    def test_default_trusted_hosts_empty(self):
        config = ServerConfig()
        assert config.trusted_hosts == frozenset()

    def test_custom_trusted_hosts(self):
        config = ServerConfig(trusted_hosts=("10.0.0.1", "10.0.0.2"))
        assert config.trusted_hosts == frozenset({"10.0.0.1", "10.0.0.2"})


class TestServerConfigIICSerialization:
    """Round-trip serialization for subinterpreter IIC transfer."""

    def test_default_config_round_trip(self):
        config = ServerConfig()
        restored = ServerConfig.from_iic_dict(config.to_iic_dict())
        assert restored.host == config.host
        assert restored.port == config.port
        assert restored.workers == config.workers
        assert restored.keep_alive_timeout == config.keep_alive_timeout
        assert restored.trusted_hosts == config.trusted_hosts

    def test_json_round_trip(self):
        config = ServerConfig(
            host="0.0.0.0",
            port=9000,
            workers=4,
            trusted_hosts=("10.0.0.1", "10.0.0.2"),
            reload_include=(".html", ".css"),
            reload_dirs=("/app", "/static"),
        )
        restored = ServerConfig.from_json(config.to_json())
        assert restored.host == config.host
        assert restored.port == config.port
        assert restored.workers == config.workers
        assert restored.trusted_hosts == config.trusted_hosts
        assert restored.reload_include == config.reload_include
        assert restored.reload_dirs == config.reload_dirs

    def test_non_serializable_fields_excluded(self):
        config = ServerConfig()
        d = config.to_iic_dict()
        assert "access_log_filter" not in d
        assert "middleware" not in d
        assert "display" not in d

    def test_internal_constants_excluded(self):
        config = ServerConfig()
        d = config.to_iic_dict()
        for key in d:
            assert not key.startswith("_"), f"Internal field {key!r} leaked into IIC dict"

    def test_frozenset_serialized_as_list(self):
        config = ServerConfig(trusted_hosts=("a", "b"))
        d = config.to_iic_dict()
        assert isinstance(d["trusted_hosts"], list)
        assert sorted(d["trusted_hosts"]) == ["a", "b"]

    def test_extra_keys_in_dict_ignored(self):
        d = ServerConfig().to_iic_dict()
        d["nonexistent_key"] = "should be ignored"
        restored = ServerConfig.from_iic_dict(d)
        assert restored.host == "127.0.0.1"

    def test_worker_startup_failure_round_trip(self):
        """The fail-loud policy propagates to subinterpreter workers via IIC."""
        config = ServerConfig(worker_startup_failure="shutdown")
        d = config.to_iic_dict()
        assert d["worker_startup_failure"] == "shutdown"
        restored = ServerConfig.from_json(config.to_json())
        assert restored.worker_startup_failure == "shutdown"


class TestServerConfigHypothesisRoundTrip:
    """Property-based round-trip tests using Hypothesis."""

    @hypothesis.given(
        port=st.integers(min_value=0, max_value=65535),
        workers=st.integers(min_value=0, max_value=32),
        backlog=st.integers(min_value=1, max_value=65535),
        keep_alive_timeout=st.floats(min_value=0.1, max_value=300.0),
        request_timeout=st.floats(min_value=0.1, max_value=300.0),
        max_request_size=st.integers(min_value=1, max_value=100_000_000),
        max_connections=st.integers(min_value=0, max_value=100_000),
        access_log=st.booleans(),
        compression=st.booleans(),
        debug=st.booleans(),
    )
    def test_round_trip_preserves_values(
        self,
        port,
        workers,
        backlog,
        keep_alive_timeout,
        request_timeout,
        max_request_size,
        max_connections,
        access_log,
        compression,
        debug,
    ):
        config = ServerConfig(
            port=port,
            workers=workers,
            backlog=backlog,
            keep_alive_timeout=keep_alive_timeout,
            request_timeout=request_timeout,
            max_request_size=max_request_size,
            max_connections=max_connections,
            access_log=access_log,
            compression=compression,
            debug=debug,
        )
        restored = ServerConfig.from_json(config.to_json())
        assert restored.port == config.port
        assert restored.workers == config.workers
        assert restored.backlog == config.backlog
        assert restored.keep_alive_timeout == config.keep_alive_timeout
        assert restored.request_timeout == config.request_timeout
        assert restored.max_request_size == config.max_request_size
        assert restored.max_connections == config.max_connections
        assert restored.access_log == config.access_log
        assert restored.compression == config.compression
        assert restored.debug == config.debug
