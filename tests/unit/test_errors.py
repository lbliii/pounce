"""Tests for pounce._errors — error hierarchy and status code mapping."""

from pounce._errors import (
    AppError,
    LifespanError,
    LimitError,
    ParseError,
    PounceError,
    SupervisorError,
    TimeoutError,
    WorkerError,
)


class TestErrorHierarchy:
    """All error types inherit from PounceError."""

    def test_pounce_error_is_exception(self):
        assert issubclass(PounceError, Exception)

    def test_parse_error_inherits(self):
        assert issubclass(ParseError, PounceError)

    def test_timeout_error_inherits(self):
        assert issubclass(TimeoutError, PounceError)

    def test_limit_error_inherits(self):
        assert issubclass(LimitError, PounceError)

    def test_app_error_inherits(self):
        assert issubclass(AppError, PounceError)

    def test_lifespan_error_inherits(self):
        assert issubclass(LifespanError, PounceError)

    def test_supervisor_error_inherits(self):
        assert issubclass(SupervisorError, PounceError)

    def test_worker_error_inherits(self):
        assert issubclass(WorkerError, PounceError)


class TestStatusCodes:
    """Each error has a default HTTP status code."""

    def test_pounce_error_default_500(self):
        err = PounceError("boom")
        assert err.status_code == 500

    def test_parse_error_400(self):
        err = ParseError("bad request")
        assert err.status_code == 400

    def test_timeout_error_408(self):
        err = TimeoutError("timed out")
        assert err.status_code == 408

    def test_limit_error_413(self):
        err = LimitError("too large")
        assert err.status_code == 413

    def test_limit_error_431_override(self):
        err = LimitError("headers too large", status_code=431)
        assert err.status_code == 431

    def test_app_error_500(self):
        err = AppError("app crashed")
        assert err.status_code == 500

    def test_lifespan_error_500(self):
        err = LifespanError("startup failed")
        assert err.status_code == 500

    def test_supervisor_error_500(self):
        err = SupervisorError("spawn failed")
        assert err.status_code == 500

    def test_worker_error_500(self):
        err = WorkerError("worker crashed")
        assert err.status_code == 500

    def test_custom_status_code_override(self):
        err = PounceError("custom", status_code=503)
        assert err.status_code == 503


class TestErrorMessages:
    """Errors carry the message through str() and args."""

    def test_message_in_str(self):
        err = ParseError("bad header")
        assert str(err) == "bad header"

    def test_message_in_args(self):
        err = AppError("unhandled")
        assert err.args == ("unhandled",)

    def test_catch_as_pounce_error(self):
        try:
            raise ParseError("test")
        except PounceError as exc:
            assert exc.status_code == 400
