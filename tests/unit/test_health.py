"""Tests for pounce._health — built-in health check endpoint."""

import json

from pounce._health import build_health_response


class TestBuildHealthResponse:
    """build_health_response() returns a valid JSON health payload."""

    def test_returns_200_status(self):
        status, headers, body = build_health_response(
            worker_id=1, active_connections=5,
        )
        assert status == 200

    def test_content_type_json(self):
        _, headers, _ = build_health_response(
            worker_id=0, active_connections=0,
        )
        header_dict = dict(headers)
        assert header_dict[b"content-type"] == b"application/json"

    def test_content_length_matches_body(self):
        _, headers, body = build_health_response(
            worker_id=0, active_connections=0,
        )
        header_dict = dict(headers)
        assert int(header_dict[b"content-length"]) == len(body)

    def test_no_cache_header(self):
        _, headers, _ = build_health_response(
            worker_id=0, active_connections=0,
        )
        header_dict = dict(headers)
        assert b"no-cache" in header_dict[b"cache-control"]

    def test_body_is_valid_json(self):
        _, _, body = build_health_response(
            worker_id=1, active_connections=10,
        )
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["worker_id"] == 1
        assert payload["active_connections"] == 10
        assert "uptime_seconds" in payload

    def test_uptime_is_positive(self):
        _, _, body = build_health_response(
            worker_id=0, active_connections=0,
        )
        payload = json.loads(body)
        assert payload["uptime_seconds"] >= 0
