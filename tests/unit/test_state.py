"""Tests for the server lifecycle state machine (_state.py).

Exercises the pure reducer (no mocks needed) and the store integration
(dispatch → state transitions).
"""

import logging

import pytest
from milo._types import Action

from pounce._state import (
    BANNER,
    READY,
    RELOAD_COMPLETE,
    RELOAD_DETECTED,
    RELOAD_FAILED,
    RELOAD_START,
    SHUTDOWN_COMPLETE,
    SHUTDOWN_DRAINED,
    SHUTDOWN_START,
    SHUTDOWN_TIMEOUT,
    SUPERVISOR_ALL_STOPPED,
    SUPERVISOR_SHUTDOWN,
    SUPERVISOR_STARTING,
    WORKER_CRASHED,
    WORKER_MAX_RESTARTS,
    WORKER_STARTED,
    ServerModel,
    _log_startup_banner_text,
    _reset_store,
    dispatch,
    get_state,
    server_reducer,
)
from pounce.config import ServerConfig
from pounce.display import DisplayConfig

# ── Reducer unit tests ───────────────────────────────────


class TestServerReducer:
    """Pure reducer tests — no store, no side effects."""

    def test_none_initial_state_returns_default_model(self):
        state = server_reducer(None, Action("@@INIT"))
        assert state == ServerModel()
        assert state.phase == "init"

    def test_init_action_preserves_state(self):
        state = ServerModel(phase="ready")
        result = server_reducer(state, Action("@@INIT"))
        assert result is state

    def test_unknown_action_returns_state_unchanged(self):
        state = ServerModel(phase="ready")
        result = server_reducer(state, Action("UNKNOWN_ACTION"))
        assert result is state

    # ── Startup ──────────────────────────────────────

    def test_banner_sets_startup_phase(self):
        state = server_reducer(
            ServerModel(),
            Action(
                BANNER,
                payload={
                    "config": None,
                    "effective_workers": 4,
                    "mode_label": "threads",
                    "worker_model": "thread (sync)",
                    "gil_status": "nogil",
                },
            ),
        )
        assert state.phase == "startup"
        assert state.effective_workers == 4
        assert state.mode_label == "threads"
        assert state.worker_model == "thread (sync)"
        assert state.gil_status == "nogil"

    @pytest.mark.issue(246)
    def test_text_startup_log_includes_resolved_worker_model(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="pounce"):
            _log_startup_banner_text(
                config=ServerConfig(),
                display=DisplayConfig(),
                effective_workers=4,
                mode_label="threads",
                worker_model="thread (sync)",
                gil_status="nogil",
            )

        assert "resolved thread (sync)" in caplog.text

    def test_ready_sets_ready_phase(self):
        state = server_reducer(
            ServerModel(phase="startup"),
            Action(READY, payload={"host": "127.0.0.1", "port": 8000}),
        )
        assert state.phase == "ready"

    # ── Supervisor / Workers ─────────────────────────

    def test_supervisor_starting_sets_serving_phase(self):
        state = server_reducer(
            ServerModel(phase="ready"),
            Action(SUPERVISOR_STARTING, payload={"count": 4, "mode": "thread"}),
        )
        assert state.phase == "serving"
        assert state.effective_workers == 4
        assert state.supervisor_mode == "thread"

    def test_worker_started_returns_state_unchanged(self):
        state = ServerModel(phase="serving")
        result = server_reducer(
            state,
            Action(WORKER_STARTED, payload={"worker_id": 0, "mode": "thread"}),
        )
        assert result is state

    def test_worker_crashed_returns_state_unchanged(self):
        state = ServerModel(phase="serving")
        result = server_reducer(
            state,
            Action(WORKER_CRASHED, payload={"worker_id": 0, "restart_count": 1}),
        )
        assert result is state

    def test_worker_max_restarts_returns_state_unchanged(self):
        state = ServerModel(phase="serving")
        result = server_reducer(
            state,
            Action(WORKER_MAX_RESTARTS, payload={"worker_id": 0, "max_restarts": 5}),
        )
        assert result is state

    # ── Reload ───────────────────────────────────────

    def test_reload_detected_sets_reloading_phase(self):
        state = server_reducer(
            ServerModel(phase="serving"),
            Action(RELOAD_DETECTED, payload={"files": ["app.py"]}),
        )
        assert state.phase == "reloading"

    def test_reload_start_sets_reloading_phase(self):
        state = server_reducer(
            ServerModel(phase="serving"),
            Action(RELOAD_START),
        )
        assert state.phase == "reloading"

    def test_reload_complete_restores_serving_phase(self):
        state = server_reducer(
            ServerModel(phase="reloading", effective_workers=2, generation=0),
            Action(RELOAD_COMPLETE, payload={"workers": 4, "generation": 1}),
        )
        assert state.phase == "serving"
        assert state.effective_workers == 4
        assert state.generation == 1

    def test_reload_complete_preserves_defaults_without_payload(self):
        state = server_reducer(
            ServerModel(phase="reloading", effective_workers=2, generation=3),
            Action(RELOAD_COMPLETE, payload={}),
        )
        assert state.effective_workers == 2
        assert state.generation == 3

    def test_reload_failed_restores_serving_phase(self):
        state = server_reducer(
            ServerModel(phase="reloading"),
            Action(RELOAD_FAILED, payload={"error": "import error"}),
        )
        assert state.phase == "serving"

    # ── Shutdown ─────────────────────────────────────

    def test_shutdown_start_sets_shutting_down_phase(self):
        state = server_reducer(
            ServerModel(phase="serving"),
            Action(SHUTDOWN_START, payload={"connections": 5}),
        )
        assert state.phase == "shutting_down"
        assert state.connections == 5

    def test_shutdown_start_defaults_connections_to_zero(self):
        state = server_reducer(
            ServerModel(phase="serving"),
            Action(SHUTDOWN_START),
        )
        assert state.phase == "shutting_down"
        assert state.connections == 0

    def test_shutdown_drained_clears_connections(self):
        state = server_reducer(
            ServerModel(phase="shutting_down", connections=5),
            Action(SHUTDOWN_DRAINED),
        )
        assert state.connections == 0

    def test_shutdown_timeout_returns_state_unchanged(self):
        state = ServerModel(phase="shutting_down")
        result = server_reducer(
            state,
            Action(SHUTDOWN_TIMEOUT, payload={"timeout": 10.0}),
        )
        assert result is state

    def test_shutdown_complete_sets_stopped_phase(self):
        state = server_reducer(
            ServerModel(phase="shutting_down"),
            Action(SHUTDOWN_COMPLETE),
        )
        assert state.phase == "stopped"

    def test_supervisor_shutdown_sets_shutting_down_phase(self):
        state = server_reducer(
            ServerModel(phase="serving"),
            Action(SUPERVISOR_SHUTDOWN, payload={"count": 4}),
        )
        assert state.phase == "shutting_down"

    def test_supervisor_all_stopped_returns_state_unchanged(self):
        state = ServerModel(phase="shutting_down")
        result = server_reducer(state, Action(SUPERVISOR_ALL_STOPPED))
        assert result is state


# ── Store integration tests ──────────────────────────────


class TestStoreLifecycle:
    """Integration tests dispatching through the real store."""

    @pytest.fixture(autouse=True)
    def _reset(self):
        """Reset the store singleton between tests."""
        _reset_store()
        yield
        _reset_store()

    def test_initial_state(self):
        assert get_state().phase == "init"

    def test_full_lifecycle_single_worker(self):
        """Walk through a complete single-worker lifecycle."""
        dispatch(
            BANNER,
            config=_fake_config(),
            effective_workers=1,
            mode_label="single",
            gil_status="nogil",
        )
        assert get_state().phase == "startup"

        dispatch(READY, host="127.0.0.1", port=8000)
        assert get_state().phase == "ready"

        dispatch(SHUTDOWN_START)
        assert get_state().phase == "shutting_down"

        dispatch(SHUTDOWN_DRAINED)
        assert get_state().connections == 0

        dispatch(SHUTDOWN_COMPLETE)
        assert get_state().phase == "stopped"

    def test_full_lifecycle_multi_worker(self):
        """Walk through a multi-worker lifecycle with supervisor."""
        dispatch(
            BANNER,
            config=_fake_config(),
            effective_workers=4,
            mode_label="threads",
            gil_status="nogil",
        )
        dispatch(READY, host="0.0.0.0", port=8000)

        dispatch(SUPERVISOR_STARTING, count=4, mode="thread")
        assert get_state().phase == "serving"
        assert get_state().effective_workers == 4

        dispatch(WORKER_STARTED, worker_id=0, mode="thread", generation=0)
        dispatch(WORKER_STARTED, worker_id=1, mode="thread", generation=0)

        dispatch(SUPERVISOR_SHUTDOWN, count=4)
        assert get_state().phase == "shutting_down"

        dispatch(SUPERVISOR_ALL_STOPPED)
        dispatch(SHUTDOWN_COMPLETE)
        assert get_state().phase == "stopped"

    def test_reload_cycle(self):
        """Dispatch a reload sequence and verify state transitions."""
        dispatch(
            BANNER,
            config=_fake_config(),
            effective_workers=2,
            mode_label="threads",
            gil_status="nogil",
        )
        dispatch(READY, host="127.0.0.1", port=8000)
        dispatch(SUPERVISOR_STARTING, count=2, mode="thread")
        assert get_state().phase == "serving"

        dispatch(RELOAD_DETECTED, files=["app.py", "config.py"])
        assert get_state().phase == "reloading"

        dispatch(RELOAD_START)
        assert get_state().phase == "reloading"

        dispatch(RELOAD_COMPLETE, workers=2, generation=1)
        assert get_state().phase == "serving"
        assert get_state().generation == 1

    def test_reload_failure_restores_serving(self):
        dispatch(
            BANNER,
            config=_fake_config(),
            effective_workers=1,
            mode_label="single",
            gil_status="nogil",
        )
        dispatch(READY, host="127.0.0.1", port=8000)
        dispatch(SUPERVISOR_STARTING, count=1, mode="thread")

        dispatch(RELOAD_START)
        dispatch(RELOAD_FAILED, error="import error")
        assert get_state().phase == "serving"

    def test_worker_crash_does_not_change_phase(self):
        dispatch(
            BANNER,
            config=_fake_config(),
            effective_workers=2,
            mode_label="threads",
            gil_status="nogil",
        )
        dispatch(READY, host="127.0.0.1", port=8000)
        dispatch(SUPERVISOR_STARTING, count=2, mode="thread")

        dispatch(WORKER_CRASHED, worker_id=0, restart_count=1)
        assert get_state().phase == "serving"

        dispatch(WORKER_MAX_RESTARTS, worker_id=0, max_restarts=5)
        assert get_state().phase == "serving"

    def test_shutdown_timeout_path(self):
        dispatch(
            BANNER,
            config=_fake_config(),
            effective_workers=1,
            mode_label="single",
            gil_status="nogil",
        )
        dispatch(READY, host="127.0.0.1", port=8000)

        dispatch(SHUTDOWN_START, connections=10)
        assert get_state().connections == 10

        dispatch(SHUTDOWN_TIMEOUT, timeout=5.0)
        assert get_state().phase == "shutting_down"

        dispatch(SHUTDOWN_COMPLETE)
        assert get_state().phase == "stopped"


# ── Helpers ──────────────────────────────────────────────


class _FakeConfig:
    """Minimal config stub for banner rendering."""

    display = None
    ssl_certfile = None
    ssl_keyfile = None
    http3_enabled = False
    compression = True
    access_log = True
    server_timing = False
    reload = False
    workers = 1
    host = "127.0.0.1"
    port = 8000
    uds = None
    log_level = "info"
    health_check_path = None
    root_path = ""


def _fake_config():
    return _FakeConfig()
