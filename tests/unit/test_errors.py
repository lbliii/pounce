"""Tests for pounce._errors — error hierarchy and status code mapping."""

import pytest

from pounce._errors import (
    AppError,
    LifespanError,
    LimitError,
    ParseError,
    PounceError,
    ReloadError,
    RequestTimeoutError,
    SupervisorError,
    TLSError,
    WorkerError,
)


class TestErrorHierarchy:
    """All error types inherit from PounceError."""

    def test_pounce_error_is_exception(self):
        assert issubclass(PounceError, Exception)

    def test_parse_error_inherits(self):
        assert issubclass(ParseError, PounceError)

    def test_timeout_error_inherits(self):
        assert issubclass(RequestTimeoutError, PounceError)

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

    def test_tls_error_inherits(self):
        assert issubclass(TLSError, PounceError)

    def test_reload_error_inherits(self):
        assert issubclass(ReloadError, PounceError)


class TestStatusCodes:
    """Each error has a default HTTP status code."""

    def test_pounce_error_default_500(self):
        err = PounceError("boom")
        assert err.status_code == 500

    def test_parse_error_400(self):
        err = ParseError("bad request")
        assert err.status_code == 400

    def test_timeout_error_408(self):
        err = RequestTimeoutError("timed out")
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

    def test_tls_error_500(self):
        err = TLSError("bad certificate")
        assert err.status_code == 500

    def test_reload_error_500(self):
        err = ReloadError("watcher failed")
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
        with pytest.raises(PounceError) as exc_info:
            raise ParseError("test")
        assert exc_info.value.status_code == 400


class TestErrorCodes:
    """Errors carry a semantic code (POUNCE_<CATEGORY>_<SPECIFIC>)."""

    def test_default_code_on_base(self):
        assert PounceError("x").code == "POUNCE_E_UNKNOWN"

    def test_default_code_per_subclass(self):
        assert ParseError("x").code == "POUNCE_PARSE_E"
        assert LimitError("x").code == "POUNCE_LIMIT_E"
        assert TLSError("x").code == "POUNCE_TLS_E"
        assert RequestTimeoutError("x").code == "POUNCE_TIMEOUT_E"
        assert AppError("x").code == "POUNCE_APP_E"
        assert LifespanError("x").code == "POUNCE_LIFESPAN_E"
        assert SupervisorError("x").code == "POUNCE_SUPERVISOR_E"
        assert WorkerError("x").code == "POUNCE_WORKER_E"
        assert ReloadError("x").code == "POUNCE_RELOAD_E"

    def test_explicit_code_override(self):
        err = TLSError("cert gone", code="POUNCE_TLS_CERT_MISSING")
        assert err.code == "POUNCE_TLS_CERT_MISSING"

    def test_hint_defaults_to_none(self):
        assert ParseError("x").hint is None

    def test_hint_set(self):
        err = TLSError(
            "cert gone",
            code="POUNCE_TLS_CERT_MISSING",
            hint="Pass --ssl-certfile=PATH or set [tool.pounce] ssl_certfile.",
        )
        assert err.hint == "Pass --ssl-certfile=PATH or set [tool.pounce] ssl_certfile."

    def test_doc_anchor(self):
        err = TLSError("x", doc="docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING")
        assert err.doc == "docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING"

    def test_backward_compat_positional_message(self):
        # Existing code: `raise ParseError("msg")` must continue to work.
        err = ParseError("bad header")
        assert str(err) == "bad header"
        assert err.status_code == 400
        assert err.code == "POUNCE_PARSE_E"
        assert err.hint is None
        assert err.doc is None


class TestErrorPickle:
    """Process-worker mode pickles exceptions across boundaries — all fields must survive."""

    def test_pickle_roundtrip_preserves_all_fields(self):
        import pickle

        original = TLSError(
            "cert gone",
            status_code=500,
            code="POUNCE_TLS_CERT_MISSING",
            hint="Pass --ssl-certfile",
            doc="docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING",
        )
        restored = pickle.loads(pickle.dumps(original))  # noqa: S301
        assert type(restored) is TLSError
        assert str(restored) == "cert gone"
        assert restored.status_code == 500
        assert restored.code == "POUNCE_TLS_CERT_MISSING"
        assert restored.hint == "Pass --ssl-certfile"
        assert restored.doc == "docs/troubleshooting.md#POUNCE_TLS_CERT_MISSING"

    def test_pickle_roundtrip_defaults(self):
        import pickle

        original = ParseError("bad")
        restored = pickle.loads(pickle.dumps(original))  # noqa: S301
        assert type(restored) is ParseError
        assert str(restored) == "bad"
        assert restored.code == "POUNCE_PARSE_E"
        assert restored.hint is None
