"""Unit tests for subinterpreter worker components."""

import queue

import pytest

from pounce._runtime import WorkerMode, has_subinterpreters
from pounce._subinterpreter_bootstrap import _import_app, _try_get
from pounce.config import _IIC_SKIP_FIELDS, ServerConfig

# ---------------------------------------------------------------------------
# WorkerMode enum
# ---------------------------------------------------------------------------


class TestWorkerModeSubinterpreter:
    """WorkerMode.SUBINTERPRETER is a valid member."""

    def test_subinterpreter_in_enum(self):
        assert WorkerMode.SUBINTERPRETER == "subinterpreter"

    def test_subinterpreter_string_value(self):
        assert str(WorkerMode.SUBINTERPRETER) == "subinterpreter"

    def test_all_modes_present(self):
        modes = {m.value for m in WorkerMode}
        assert modes == {"thread", "process", "subinterpreter"}


# ---------------------------------------------------------------------------
# has_subinterpreters()
# ---------------------------------------------------------------------------


class TestHasSubinterpreters:
    """Runtime detection of concurrent.interpreters."""

    def test_returns_bool(self):
        result = has_subinterpreters()
        assert isinstance(result, bool)

    def test_consistent(self):
        assert has_subinterpreters() == has_subinterpreters()


# ---------------------------------------------------------------------------
# Config validation for subinterpreter mode
# ---------------------------------------------------------------------------


class TestConfigSubinterpreterValidation:
    """ServerConfig validates subinterpreter mode."""

    @pytest.mark.skipif(
        not has_subinterpreters(),
        reason="concurrent.interpreters not available",
    )
    def test_subinterpreter_mode_accepted(self):
        config = ServerConfig(worker_mode="subinterpreter")
        assert config.worker_mode == "subinterpreter"

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError, match="worker_mode must be one of"):
            ServerConfig(worker_mode="foobar")

    def test_subinterpreter_in_valid_modes(self):
        config = ServerConfig()
        assert "subinterpreter" in config._VALID_WORKER_MODES


# ---------------------------------------------------------------------------
# Config IIC serialization
# ---------------------------------------------------------------------------


class TestConfigIICSkipFields:
    """_IIC_SKIP_FIELDS excludes non-serializable and internal fields."""

    def test_access_log_filter_excluded(self):
        assert "access_log_filter" in _IIC_SKIP_FIELDS

    def test_middleware_excluded(self):
        assert "middleware" in _IIC_SKIP_FIELDS

    def test_display_excluded(self):
        assert "display" in _IIC_SKIP_FIELDS

    def test_valid_log_levels_excluded(self):
        assert "_VALID_LOG_LEVELS" in _IIC_SKIP_FIELDS

    def test_valid_worker_modes_excluded(self):
        assert "_VALID_WORKER_MODES" in _IIC_SKIP_FIELDS


class TestConfigIICDictEdgeCases:
    """Edge cases for to_iic_dict / from_iic_dict."""

    def test_empty_trusted_hosts_round_trips(self):
        config = ServerConfig()
        restored = ServerConfig.from_iic_dict(config.to_iic_dict())
        assert restored.trusted_hosts == frozenset()

    def test_static_files_dict_preserved(self):
        config = ServerConfig(static_files={"/static": "/var/www"})
        d = config.to_iic_dict()
        assert d["static_files"] == {"/static": "/var/www"}
        restored = ServerConfig.from_iic_dict(d)
        assert restored.static_files == {"/static": "/var/www"}

    def test_bool_fields_preserved(self):
        config = ServerConfig(debug=True, compression=False, access_log=False)
        restored = ServerConfig.from_json(config.to_json())
        assert restored.debug is True
        assert restored.compression is False
        assert restored.access_log is False

    def test_float_fields_preserved(self):
        config = ServerConfig(keep_alive_timeout=1.5, request_timeout=60.0)
        restored = ServerConfig.from_json(config.to_json())
        assert restored.keep_alive_timeout == 1.5
        assert restored.request_timeout == 60.0

    def test_none_fields_preserved(self):
        config = ServerConfig()
        restored = ServerConfig.from_json(config.to_json())
        assert restored.ssl_certfile is None
        assert restored.uds is None
        assert restored.health_check_path is None


# ---------------------------------------------------------------------------
# _import_app
# ---------------------------------------------------------------------------


class TestImportApp:
    """_import_app resolves module:attribute paths."""

    def test_direct_import(self):
        app = _import_app("examples.hello:app")
        assert callable(app)

    def test_factory_import(self):
        app = _import_app("examples.factory_app:create_app()")
        assert callable(app)

    def test_invalid_path_no_colon(self):
        with pytest.raises(ValueError, match="Invalid app path"):
            _import_app("examples.hello")

    def test_invalid_path_empty_attr(self):
        with pytest.raises(ValueError, match="Invalid app path"):
            _import_app("examples.hello:")

    def test_invalid_path_empty_module(self):
        with pytest.raises(ValueError, match="Invalid app path"):
            _import_app(":app")

    def test_nonexistent_module(self):
        with pytest.raises(ModuleNotFoundError):
            _import_app("nonexistent.module:app")

    def test_nonexistent_attribute(self):
        with pytest.raises(AttributeError):
            _import_app("examples.hello:nonexistent")


# ---------------------------------------------------------------------------
# _try_get
# ---------------------------------------------------------------------------


class TestTryGet:
    """_try_get does non-blocking reads from a queue-like object."""

    def test_returns_item_from_queue(self):
        q = queue.Queue()
        q.put(("shutdown",))
        result = _try_get(q)
        assert result == ("shutdown",)

    def test_returns_none_on_empty(self):
        q = queue.Queue()
        result = _try_get(q)
        assert result is None

    def test_returns_none_on_error(self):
        class BrokenQueue:
            def get_nowait(self):
                raise RuntimeError("broken")

        result = _try_get(BrokenQueue())
        assert result is None


# ---------------------------------------------------------------------------
# _serialize_lifespan_state (via supervisor module)
# ---------------------------------------------------------------------------


class TestSerializeLifespanState:
    """_serialize_lifespan_state filters non-JSON-safe keys."""

    def setup_method(self):
        from pounce.supervisor import _serialize_lifespan_state

        self._serialize = _serialize_lifespan_state

    def test_empty_state(self):
        import json

        result = self._serialize({})
        assert json.loads(result) == {}

    def test_json_safe_keys_preserved(self):
        import json

        state = {"app_name": "test", "version": 42, "debug": True}
        result = json.loads(self._serialize(state))
        assert result == state

    def test_non_serializable_keys_dropped(self):
        import json

        state = {
            "name": "test",
            "pool": object(),  # not JSON-serializable
        }
        result = json.loads(self._serialize(state))
        assert result == {"name": "test"}
        assert "pool" not in result

    def test_nested_dict_preserved(self):
        import json

        state = {"config": {"key": "value", "count": 3}}
        result = json.loads(self._serialize(state))
        assert result == state

    def test_list_values_preserved(self):
        import json

        state = {"tags": ["a", "b", "c"]}
        result = json.loads(self._serialize(state))
        assert result == state


# ---------------------------------------------------------------------------
# IIC protocol constants
# ---------------------------------------------------------------------------


class TestIICProtocolConstants:
    """IIC protocol constants are defined correctly."""

    def test_commands_are_strings(self):
        from pounce._subinterpreter_bootstrap import CMD_DRAIN, CMD_SHUTDOWN

        assert CMD_SHUTDOWN == "shutdown"
        assert CMD_DRAIN == "drain"

    def test_status_constants(self):
        from pounce._subinterpreter_bootstrap import (
            STATUS_DRAINING,
            STATUS_ERROR,
            STATUS_IDLE,
            STATUS_SERVING,
            STATUS_STARTED,
            STATUS_STOPPED,
        )

        assert STATUS_STARTED == "started"
        assert STATUS_SERVING == "serving"
        assert STATUS_DRAINING == "draining"
        assert STATUS_IDLE == "idle"
        assert STATUS_STOPPED == "stopped"
        assert STATUS_ERROR == "error"


# ---------------------------------------------------------------------------
# _try_iic_get (supervisor-side) — UnboundQueueItem guard
# ---------------------------------------------------------------------------


class TestTryIICGet:
    """Supervisor-side _try_iic_get discards non-tuple messages."""

    def setup_method(self):
        from pounce.supervisor import _try_iic_get

        self._try_iic_get = _try_iic_get

    def test_returns_valid_tuple(self):
        q = queue.Queue()
        q.put(("serving",))
        assert self._try_iic_get(q) == ("serving",)

    def test_returns_none_on_empty(self):
        q = queue.Queue()
        assert self._try_iic_get(q) is None

    def test_discards_non_tuple_unbound_queue_item(self):
        """When a subinterpreter is destroyed, queued items become
        UnboundQueueItem objects (not tuples).  _try_iic_get must discard them.
        """

        class FakeUnboundQueueItem:
            """Simulates concurrent.interpreters UnboundQueueItem."""

        class MockQueue:
            def get_nowait(self):
                return FakeUnboundQueueItem()

        result = self._try_iic_get(MockQueue())
        assert result is None

    def test_discards_string_message(self):
        """Non-tuple types (e.g. bare strings) should also be discarded."""
        q = queue.Queue()
        q.put("not a tuple")
        assert self._try_iic_get(q) is None

    def test_returns_none_on_exception(self):
        class ExplodingQueue:
            def get_nowait(self):
                raise RuntimeError("kaboom")

        assert self._try_iic_get(ExplodingQueue()) is None


# ---------------------------------------------------------------------------
# IIC queue backpressure
# ---------------------------------------------------------------------------


class TestIICQueueBackpressure:
    """Verify IIC queues handle many messages without deadlock or loss."""

    def test_many_status_messages_no_loss(self):
        """Supervisor reads all messages even if worker sends many before reads."""
        from pounce.supervisor import _try_iic_get

        q = queue.Queue()
        messages = [
            ("started",),
            ("serving",),
            ("draining",),
            ("idle",),
            ("stopped",),
        ]
        for msg in messages:
            q.put(msg)

        received = []
        while True:
            msg = _try_iic_get(q)
            if msg is None:
                break
            received.append(msg)

        assert received == messages

    def test_burst_of_status_messages(self):
        """Simulate a worker sending 100 status updates before supervisor reads."""
        from pounce.supervisor import _try_iic_get

        q = queue.Queue()
        for i in range(100):
            q.put(("serving", i))

        received = []
        while True:
            msg = _try_iic_get(q)
            if msg is None:
                break
            received.append(msg)

        assert len(received) == 100
        assert received[0] == ("serving", 0)
        assert received[99] == ("serving", 99)


# ---------------------------------------------------------------------------
# Lifespan state serialization warnings (Sprint 2)
# ---------------------------------------------------------------------------


class TestLifespanStateWarnings:
    """_serialize_lifespan_state should warn on dropped keys."""

    def setup_method(self):
        from pounce.supervisor import _serialize_lifespan_state

        self._serialize = _serialize_lifespan_state

    def test_warns_on_non_serializable_key(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="pounce"):
            self._serialize({"db_pool": object(), "name": "test"})

        assert any("db_pool" in record.message for record in caplog.records)
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    def test_no_warning_for_all_serializable(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="pounce"):
            self._serialize({"name": "test", "count": 42})

        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warning_records) == 0


# ---------------------------------------------------------------------------
# Factory app import error propagation (Sprint 2)
# ---------------------------------------------------------------------------


class TestFactoryImportErrorPropagation:
    """_import_app wraps factory() exceptions with the factory name."""

    def test_factory_error_includes_name(self):
        """When factory() raises, the error should include the factory path."""
        import types

        # Create a fake module with a failing factory
        mod = types.ModuleType("_test_factory_mod")
        mod.create_app = lambda: (_ for _ in ()).throw(ValueError("config missing"))
        import sys

        sys.modules["_test_factory_mod"] = mod
        try:
            with pytest.raises(RuntimeError, match=r"create_app.*raised.*config missing"):
                _import_app("_test_factory_mod:create_app()")
        finally:
            del sys.modules["_test_factory_mod"]

    def test_factory_error_preserves_cause(self):
        """The original exception should be chained as __cause__."""
        import types

        mod = types.ModuleType("_test_factory_cause")
        mod.make = lambda: (_ for _ in ()).throw(TypeError("bad type"))
        import sys

        sys.modules["_test_factory_cause"] = mod
        try:
            with pytest.raises(RuntimeError) as exc_info:
                _import_app("_test_factory_cause:make()")
            assert isinstance(exc_info.value.__cause__, TypeError)
        finally:
            del sys.modules["_test_factory_cause"]


# ---------------------------------------------------------------------------
# Config round-trip fuzz (Sprint 2)
# ---------------------------------------------------------------------------


class TestConfigRoundTripFuzz:
    """Hypothesis-based fuzzing of config JSON serialization round-trip."""

    @pytest.mark.skipif(
        not has_subinterpreters(),
        reason="concurrent.interpreters not available",
    )
    def test_config_roundtrip_basic_variations(self):
        """Various config values survive JSON round-trip."""
        configs = [
            ServerConfig(),
            ServerConfig(debug=True, workers=4, host="0.0.0.0", port=9000),
            ServerConfig(
                compression=False,
                access_log=False,
                keep_alive_timeout=30.0,
                request_timeout=120.0,
                max_request_size=1024 * 1024 * 10,
            ),
            ServerConfig(
                static_files={"/a": "/tmp/a", "/b": "/tmp/b"},
                trusted_hosts=frozenset(["example.com", "localhost"]),
            ),
            ServerConfig(
                ssl_certfile="/tmp/cert.pem",
                ssl_keyfile="/tmp/key.pem",
                worker_mode="subinterpreter",
            ),
            ServerConfig(
                reload=True,
                reload_include=("*.py", "*.html"),
                reload_dirs=("src/", "templates/"),
            ),
        ]

        for original in configs:
            json_str = original.to_json()
            restored = ServerConfig.from_json(json_str)
            # Compare all IIC-safe fields
            for key, val in original.to_iic_dict().items():
                restored_val = restored.to_iic_dict()[key]
                assert val == restored_val, f"Field {key!r} mismatch: {val!r} != {restored_val!r}"


# ---------------------------------------------------------------------------
# FD leak prevention (issue #106)
# ---------------------------------------------------------------------------


def _fd_is_open(fd: int) -> bool:
    """Return True if ``fd`` refers to an open descriptor in this process."""
    import os

    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


class TestBootstrapClosesListenerFD:
    """bootstrap() releases its dup'd listener FD on every exit path (issue #106).

    Subinterpreters share the parent's FD table, so the dup'd descriptor and the
    reconstructed socket are the same FD number.  Before the fix the FD was only
    closed inside the clean-drain path, so abnormal failures (bad app import,
    start_server error, startup-hook failure) leaked it.
    """

    def _run_bootstrap(self, app_path: str) -> tuple[int, list[tuple]]:
        """Dup a real listener FD, run bootstrap, return (dup_fd, status_msgs)."""
        import socket
        import sys

        from pounce._subinterpreter_bootstrap import bootstrap

        lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            lsock.bind(("127.0.0.1", 0))
            lsock.listen(8)

            import os

            dup_fd = os.dup(lsock.fileno())
            assert _fd_is_open(dup_fd)

            ctrl_q: queue.Queue = queue.Queue()
            status_q: queue.Queue = queue.Queue()
            config_json = ServerConfig(worker_mode="subinterpreter").to_json()

            with pytest.raises(Exception):  # noqa: B017,PT011 — any failure is fine
                bootstrap(
                    ctrl_q,
                    status_q,
                    config_json,
                    "{}",
                    app_path,
                    dup_fd,
                    int(socket.AF_INET),
                    0,
                    tuple(sys.path),
                )

            msgs = []
            while not status_q.empty():
                msgs.append(status_q.get_nowait())
            return dup_fd, msgs
        finally:
            lsock.close()

    def test_fd_closed_on_bad_app_import(self) -> None:
        """A failing app import must not leak the reconstructed listener FD."""
        dup_fd, _msgs = self._run_bootstrap("nonexistent.module:app")
        assert not _fd_is_open(dup_fd), "dup'd listener FD leaked after import failure"

    def test_emits_fd_closed_status(self) -> None:
        """bootstrap signals STATUS_FD_CLOSED so the supervisor can skip closing."""
        from pounce._subinterpreter_bootstrap import STATUS_FD_CLOSED

        _dup_fd, msgs = self._run_bootstrap("nonexistent.module:app")
        assert any(m[0] == STATUS_FD_CLOSED for m in msgs)


class TestReclaimSubinterpFD:
    """Supervisor._reclaim_subinterp_fd closes leaked FDs without double-close."""

    def _make_supervisor(self):
        from pounce.supervisor import Supervisor

        config = ServerConfig(worker_mode="subinterpreter")
        sup = Supervisor.__new__(Supervisor)
        sup._mode = "subinterpreter"
        sup._config = config
        return sup

    def _make_handle(self, *, alive: bool, sock_fd, status_queue):
        import threading

        from pounce.supervisor import _WorkerHandle

        class _FakeTarget:
            def __init__(self, alive: bool) -> None:
                self._alive = alive

            def is_alive(self) -> bool:
                return self._alive

        handle = _WorkerHandle(0, threading.Thread(target=lambda: None), None)
        handle.target = _FakeTarget(alive)  # type: ignore[assignment]
        handle.sock_fd = sock_fd
        handle.status_queue = status_queue
        return handle

    def test_closes_orphaned_fd_when_thread_dead_no_signal(self) -> None:
        """Dead worker with no self-close signal: supervisor closes the leaked FD."""
        import os

        r, w = os.pipe()
        os.close(w)  # only track r as the "listener" fd
        sup = self._make_supervisor()
        handle = self._make_handle(alive=False, sock_fd=r, status_queue=None)

        assert _fd_is_open(r)
        sup._reclaim_subinterp_fd(handle)
        assert not _fd_is_open(r), "supervisor did not reclaim leaked listener FD"
        assert handle.sock_fd is None
        assert handle.fd_self_closed is True

    def test_skips_close_when_self_close_signalled(self) -> None:
        """If the worker signalled STATUS_FD_CLOSED, do NOT close (double-close guard).

        The FD value may have been reassigned by the OS; closing it would corrupt
        an unrelated descriptor.
        """
        import os

        from pounce._subinterpreter_bootstrap import STATUS_FD_CLOSED

        # A live, unrelated FD standing in for a reassigned value.
        sentinel_fd = os.dup(0)
        status_q: queue.Queue = queue.Queue()
        status_q.put((STATUS_FD_CLOSED,))
        sup = self._make_supervisor()
        handle = self._make_handle(alive=False, sock_fd=sentinel_fd, status_queue=status_q)

        try:
            sup._reclaim_subinterp_fd(handle)
            assert _fd_is_open(sentinel_fd), "supervisor double-closed a self-closed FD"
            assert handle.fd_self_closed is True
            assert handle.sock_fd is None
        finally:
            if _fd_is_open(sentinel_fd):
                os.close(sentinel_fd)

    def test_skips_close_when_thread_still_alive(self) -> None:
        """A still-running worker owns its socket; the supervisor must not close it."""
        import os

        sentinel_fd = os.dup(0)
        sup = self._make_supervisor()
        handle = self._make_handle(alive=True, sock_fd=sentinel_fd, status_queue=None)

        try:
            sup._reclaim_subinterp_fd(handle)
            assert _fd_is_open(sentinel_fd), "supervisor closed FD of a live worker"
            assert handle.sock_fd == sentinel_fd, "FD prematurely dropped from handle"
            assert handle.fd_self_closed is False
        finally:
            os.close(sentinel_fd)

    def test_idempotent_no_double_close(self) -> None:
        """Calling reclaim twice never issues a second close()."""
        import os

        r, w = os.pipe()
        os.close(w)
        sup = self._make_supervisor()
        handle = self._make_handle(alive=False, sock_fd=r, status_queue=None)

        sup._reclaim_subinterp_fd(handle)
        # Second call is a no-op (sock_fd cleared, fd_self_closed set).
        sup._reclaim_subinterp_fd(handle)
        assert handle.sock_fd is None
