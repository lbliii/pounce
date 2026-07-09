"""Verify that the public Railway main canary serves an expected git commit."""

from __future__ import annotations

import argparse
import http.client
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


def _get(url: str, *, timeout: float = 15.0) -> Response:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Only absolute HTTP(S) origins are supported: {url!r}")
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=timeout)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    try:
        connection.request("GET", path, headers={"user-agent": "pounce-main-canary/1"})
        response = connection.getresponse()
        return Response(
            status=response.status,
            headers={key.lower(): value for key, value in response.getheaders()},
            body=response.read(),
        )
    finally:
        connection.close()


def _json_response(response: Response, url: str) -> dict[str, Any]:
    if response.status != 200:
        raise RuntimeError(f"{url} returned HTTP {response.status}")
    payload = json.loads(response.body)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} returned a non-object JSON payload")
    return payload


def _matches_expected_commit(payload: dict[str, Any], expected_sha: str) -> bool:
    deployed = payload.get("git_commit")
    return isinstance(deployed, str) and deployed == expected_sha


def _wait_for_commit(origin: str, expected_sha: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    root_url = f"{origin}/"
    while time.monotonic() < deadline:
        try:
            payload = _json_response(_get(root_url), root_url)
            if _matches_expected_commit(payload, expected_sha):
                return payload
            last_error = RuntimeError(
                f"Railway still serves commit {payload.get('git_commit')!r}; "
                f"waiting for {expected_sha}"
            )
        except (OSError, RuntimeError, json.JSONDecodeError, http.client.HTTPException) as exc:
            last_error = exc
        time.sleep(5)
    raise TimeoutError(
        f"Railway did not serve commit {expected_sha} within {timeout:.0f}s"
    ) from last_error


def _verify_runtime(payload: dict[str, Any], expected_sha: str) -> None:
    expected = {
        "status": "ok",
        "channel": "main-canary",
        "gil_enabled": False,
        "git_commit": expected_sha,
    }
    mismatches = {
        key: {"expected": value, "actual": payload.get(key)}
        for key, value in expected.items()
        if payload.get(key) != value
    }
    if mismatches:
        raise RuntimeError(f"unexpected Railway canary identity: {mismatches}")
    if not payload.get("pounce_version") or not payload.get("python_version"):
        raise RuntimeError(f"incomplete Railway runtime identity: {payload}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, help="Public Railway HTTPS origin")
    parser.add_argument("--expected-sha", required=True, help="Full git SHA expected from Railway")
    parser.add_argument("--timeout", type=float, default=900, help="Seconds to wait for deployment")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    origin = args.origin.rstrip("/")
    payload = _wait_for_commit(origin, args.expected_sha, args.timeout)
    _verify_runtime(payload, args.expected_sha)

    ready_url = f"{origin}/readyz"
    ready = _json_response(_get(ready_url), ready_url)
    if ready.get("status") != "ok":
        raise RuntimeError(f"unexpected readiness payload: {ready}")

    slow_url = f"{origin}/slow"
    slow = _json_response(_get(slow_url), slow_url)
    _verify_runtime(slow, args.expected_sha)

    stream_url = f"{origin}/stream"
    stream = _get(stream_url)
    content_type = stream.headers.get("content-type", "")
    if stream.status != 200 or not content_type.startswith("text/event-stream"):
        raise RuntimeError(
            f"unexpected stream response: status={stream.status}, content-type={content_type!r}"
        )
    if stream.body.count(b"event: canary\n") != 2 or args.expected_sha.encode() not in stream.body:
        raise RuntimeError(f"incomplete canary stream: {stream.body[:240]!r}")

    print(
        "Railway main canary passed: "
        f"commit={args.expected_sha}, version={payload['pounce_version']}, "
        f"python={payload['python_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
