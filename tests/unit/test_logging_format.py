"""Tests for pounce.logging — configuration and access log formatting."""

import logging

from pounce.config import ServerConfig
from pounce.logging import access_log, configure_logging


class TestConfigureLogging:
    """configure_logging() sets up pounce loggers."""

    def test_sets_log_level(self):
        config = ServerConfig(log_level="debug")
        configure_logging(config)
        root = logging.getLogger("pounce")
        assert root.level == logging.DEBUG

    def test_info_level(self):
        config = ServerConfig(log_level="info")
        configure_logging(config)
        root = logging.getLogger("pounce")
        assert root.level == logging.INFO

    def test_warning_level(self):
        config = ServerConfig(log_level="warning")
        configure_logging(config)
        root = logging.getLogger("pounce")
        assert root.level == logging.WARNING


class TestAccessLog:
    """access_log() writes formatted entries."""

    def test_format(self, caplog):
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/api/users", 200, 1234, 5.3, "127.0.0.1:5000")

        assert len(caplog.records) == 1
        msg = caplog.records[0].getMessage()
        assert "GET" in msg
        assert "/api/users" in msg
        assert "200" in msg
        assert "1234" in msg
        assert "5.3ms" in msg
        assert "127.0.0.1:5000" in msg

    def test_404_status(self, caplog):
        with caplog.at_level(logging.INFO, logger="pounce.access"):
            access_log("GET", "/missing", 404, 0, 1.2, "10.0.0.1:9999")

        msg = caplog.records[0].getMessage()
        assert "404" in msg
        assert "/missing" in msg
