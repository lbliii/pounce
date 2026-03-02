"""
Tests for Sentry error tracking integration.

"""

from unittest.mock import MagicMock, Mock, patch

import pytest

from pounce._sentry import (
    add_breadcrumb,
    capture_exception,
    capture_message,
    create_sentry_wrapper,
    flush,
    init_sentry,
    is_sentry_available,
    set_context,
    set_tag,
    set_user,
    start_transaction,
)
from pounce.config import ServerConfig


class TestSentryAvailability:
    """Tests for Sentry availability check."""

    def test_is_sentry_available_when_installed(self):
        """Test that is_sentry_available returns True when installed."""
        with patch.dict("sys.modules", {"sentry_sdk": Mock()}):
            assert is_sentry_available() is True

    def test_is_sentry_available_when_not_installed(self):
        """Test that is_sentry_available returns False when not installed."""
        with (
            patch.dict("sys.modules", {"sentry_sdk": None}),
            patch("builtins.__import__", side_effect=ImportError),
        ):
            assert is_sentry_available() is False


class TestSentryConfiguration:
    """Tests for Sentry configuration."""

    def test_sentry_disabled_by_default(self):
        """Test that Sentry is disabled by default."""
        config = ServerConfig()
        assert config.sentry_dsn is None

    def test_sentry_can_be_enabled(self):
        """Test that Sentry can be enabled with DSN."""
        config = ServerConfig(
            sentry_dsn="https://example@o0.ingest.sentry.io/0",
        )
        assert config.sentry_dsn == "https://example@o0.ingest.sentry.io/0"

    def test_sentry_environment_configuration(self):
        """Test Sentry environment configuration."""
        config = ServerConfig(
            sentry_dsn="https://example@o0.ingest.sentry.io/0",
            sentry_environment="production",
        )
        assert config.sentry_environment == "production"

    def test_sentry_release_configuration(self):
        """Test Sentry release configuration."""
        config = ServerConfig(
            sentry_dsn="https://example@o0.ingest.sentry.io/0",
            sentry_release="myapp@1.0.0",
        )
        assert config.sentry_release == "myapp@1.0.0"

    def test_default_traces_sample_rate(self):
        """Test default traces sample rate."""
        config = ServerConfig()
        assert config.sentry_traces_sample_rate == 0.1

    def test_custom_traces_sample_rate(self):
        """Test custom traces sample rate."""
        config = ServerConfig(sentry_traces_sample_rate=0.5)
        assert config.sentry_traces_sample_rate == 0.5

    def test_traces_sample_rate_validation(self):
        """Test that traces_sample_rate must be 0.0-1.0."""
        with pytest.raises(ValueError, match=r"sentry_traces_sample_rate must be 0\.0-1\.0"):
            ServerConfig(sentry_traces_sample_rate=1.5)

        with pytest.raises(ValueError, match=r"sentry_traces_sample_rate must be 0\.0-1\.0"):
            ServerConfig(sentry_traces_sample_rate=-0.1)

    def test_profiles_sample_rate_validation(self):
        """Test that profiles_sample_rate must be 0.0-1.0."""
        with pytest.raises(ValueError, match=r"sentry_profiles_sample_rate must be 0\.0-1\.0"):
            ServerConfig(sentry_profiles_sample_rate=2.0)

        with pytest.raises(ValueError, match=r"sentry_profiles_sample_rate must be 0\.0-1\.0"):
            ServerConfig(sentry_profiles_sample_rate=-0.5)


class TestSentryInit:
    """Tests for Sentry initialization."""

    def test_init_sentry_raises_when_not_installed(self):
        """Test that init_sentry raises ImportError when SDK not installed."""
        with (
            patch("pounce._sentry.is_sentry_available", return_value=False),
            pytest.raises(ImportError, match=r"sentry-sdk not installed"),
        ):
            init_sentry(dsn="https://example@o0.ingest.sentry.io/0")

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_init_sentry_calls_sdk_init(self, mock_available):
        """Test that init_sentry calls sentry_sdk.init with correct params."""
        mock_sentry = MagicMock()
        mock_logging_integration = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "sentry_sdk": mock_sentry,
                "sentry_sdk.integrations.logging": MagicMock(
                    LoggingIntegration=mock_logging_integration
                ),
            },
        ):
            init_sentry(
                dsn="https://example@o0.ingest.sentry.io/0",
                environment="production",
                release="myapp@1.0.0",
                traces_sample_rate=0.5,
                profiles_sample_rate=0.3,
                debug=True,
            )

            # Verify sentry_sdk.init was called
            mock_sentry.init.assert_called_once()
            call_kwargs = mock_sentry.init.call_args[1]

            assert call_kwargs["dsn"] == "https://example@o0.ingest.sentry.io/0"
            assert call_kwargs["environment"] == "production"
            assert call_kwargs["release"] == "myapp@1.0.0"
            assert call_kwargs["traces_sample_rate"] == 0.5
            assert call_kwargs["profiles_sample_rate"] == 0.3
            assert call_kwargs["debug"] is True


class TestSentryWrapper:
    """Tests for Sentry ASGI wrapper."""

    def test_wrapper_returns_app_when_sentry_unavailable(self):
        """Test that wrapper returns original app when Sentry unavailable."""
        mock_app = MagicMock()

        with patch("pounce._sentry.is_sentry_available", return_value=False):
            wrapped = create_sentry_wrapper(mock_app)
            assert wrapped is mock_app

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_wrapper_uses_sentry_middleware(self, mock_available):
        """Test that wrapper uses SentryAsgiMiddleware."""
        mock_app = MagicMock()
        mock_middleware = MagicMock()
        mock_sentry = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "sentry_sdk": mock_sentry,
                "sentry_sdk.integrations.asgi": MagicMock(SentryAsgiMiddleware=mock_middleware),
            },
        ):
            create_sentry_wrapper(mock_app)

            # Verify SentryAsgiMiddleware was instantiated with app
            mock_middleware.assert_called_once_with(mock_app)


class TestSentryCapture:
    """Tests for Sentry capture functions."""

    def test_capture_exception_returns_none_when_unavailable(self):
        """Test that capture_exception returns None when Sentry unavailable."""
        with patch("pounce._sentry.is_sentry_available", return_value=False):
            error = ValueError("test error")
            result = capture_exception(error)
            assert result is None

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_capture_exception_calls_sdk(self, mock_available):
        """Test that capture_exception calls sentry_sdk.capture_exception."""
        mock_sentry = MagicMock()
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = Mock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = Mock(return_value=False)
        mock_sentry.capture_exception.return_value = "event-id-123"

        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            error = ValueError("test error")
            result = capture_exception(
                error,
                level="warning",
                tags={"component": "test"},
                extra={"detail": "info"},
            )

            # Verify capture was called
            mock_sentry.capture_exception.assert_called_once_with(error)

            # Verify scope was configured
            assert mock_scope.level == "warning"
            mock_scope.set_tag.assert_called_once_with("component", "test")
            mock_scope.set_extra.assert_called_once_with("detail", "info")

            assert result == "event-id-123"

    def test_capture_message_returns_none_when_unavailable(self):
        """Test that capture_message returns None when Sentry unavailable."""
        with patch("pounce._sentry.is_sentry_available", return_value=False):
            result = capture_message("test message")
            assert result is None

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_capture_message_calls_sdk(self, mock_available):
        """Test that capture_message calls sentry_sdk.capture_message."""
        mock_sentry = MagicMock()
        mock_scope = MagicMock()
        mock_sentry.push_scope.return_value.__enter__ = Mock(return_value=mock_scope)
        mock_sentry.push_scope.return_value.__exit__ = Mock(return_value=False)
        mock_sentry.capture_message.return_value = "event-id-456"

        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            result = capture_message("test message", level="info")

            # Verify capture was called
            mock_sentry.capture_message.assert_called_once_with("test message", level="info")

            assert result == "event-id-456"


class TestSentryContext:
    """Tests for Sentry context functions."""

    def test_set_user_does_nothing_when_unavailable(self):
        """Test that set_user does nothing when Sentry unavailable."""
        with patch("pounce._sentry.is_sentry_available", return_value=False):
            # Should not raise
            set_user(user_id="123", email="user@example.com")

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_set_user_calls_sdk(self, mock_available):
        """Test that set_user calls sentry_sdk.set_user."""
        mock_sentry = MagicMock()

        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            set_user(
                user_id="123",
                email="user@example.com",
                username="testuser",
                ip_address="192.168.1.1",
            )

            mock_sentry.set_user.assert_called_once_with(
                {
                    "id": "123",
                    "email": "user@example.com",
                    "username": "testuser",
                    "ip_address": "192.168.1.1",
                }
            )

    def test_set_tag_does_nothing_when_unavailable(self):
        """Test that set_tag does nothing when Sentry unavailable."""
        with patch("pounce._sentry.is_sentry_available", return_value=False):
            # Should not raise
            set_tag("environment", "production")

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_set_tag_calls_sdk(self, mock_available):
        """Test that set_tag calls sentry_sdk.set_tag."""
        mock_sentry = MagicMock()

        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            set_tag("environment", "production")

            mock_sentry.set_tag.assert_called_once_with("environment", "production")

    def test_set_context_does_nothing_when_unavailable(self):
        """Test that set_context does nothing when Sentry unavailable."""
        with patch("pounce._sentry.is_sentry_available", return_value=False):
            # Should not raise
            set_context("database", {"query": "SELECT *"})

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_set_context_calls_sdk(self, mock_available):
        """Test that set_context calls sentry_sdk.set_context."""
        mock_sentry = MagicMock()

        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            set_context("database", {"query": "SELECT *", "duration_ms": 150})

            mock_sentry.set_context.assert_called_once_with(
                "database", {"query": "SELECT *", "duration_ms": 150}
            )

    def test_add_breadcrumb_does_nothing_when_unavailable(self):
        """Test that add_breadcrumb does nothing when Sentry unavailable."""
        with patch("pounce._sentry.is_sentry_available", return_value=False):
            # Should not raise
            add_breadcrumb("User logged in", category="auth")

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_add_breadcrumb_calls_sdk(self, mock_available):
        """Test that add_breadcrumb calls sentry_sdk.add_breadcrumb."""
        mock_sentry = MagicMock()

        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            add_breadcrumb(
                "User logged in",
                category="auth",
                level="info",
                data={"user_id": "123"},
            )

            mock_sentry.add_breadcrumb.assert_called_once_with(
                message="User logged in",
                category="auth",
                level="info",
                data={"user_id": "123"},
            )


class TestSentryTransaction:
    """Tests for Sentry transaction tracking."""

    def test_start_transaction_returns_noop_when_unavailable(self):
        """Test that start_transaction returns no-op when Sentry unavailable."""
        with patch("pounce._sentry.is_sentry_available", return_value=False):
            transaction = start_transaction("test", op="http.server")

            # Should be a no-op context manager
            with transaction:
                pass  # Should not raise

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_start_transaction_calls_sdk(self, mock_available):
        """Test that start_transaction calls sentry_sdk.start_transaction."""
        mock_sentry = MagicMock()

        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            start_transaction("GET /api/users", op="http.server")

            mock_sentry.start_transaction.assert_called_once_with(
                name="GET /api/users", op="http.server"
            )


class TestSentryFlush:
    """Tests for Sentry flush."""

    def test_flush_returns_true_when_unavailable(self):
        """Test that flush returns True when Sentry unavailable."""
        with patch("pounce._sentry.is_sentry_available", return_value=False):
            result = flush(timeout=2.0)
            assert result is True

    @patch("pounce._sentry.is_sentry_available", return_value=True)
    def test_flush_calls_sdk(self, mock_available):
        """Test that flush calls client.flush."""
        mock_client = MagicMock()
        mock_client.flush.return_value = True
        mock_hub = MagicMock()
        mock_hub.current.client = mock_client
        mock_sentry = MagicMock(Hub=mock_hub)

        with patch.dict("sys.modules", {"sentry_sdk": mock_sentry}):
            result = flush(timeout=5.0)

            mock_client.flush.assert_called_once_with(timeout=5.0)
            assert result is True
