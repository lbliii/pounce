"""Deploy and prove the Railway recipe across a graceful redeploy."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

RECIPE_DIR = Path(__file__).resolve().parent
SESSION_ID = os.environ.get("RAILWAY_AGENT_SESSION", f"pounce-railway-{uuid.uuid4().hex[:12]}")
TERMINAL_FAILURES = {
    "CRASHED",
    "FAILED",
    "NEEDS_APPROVAL",
    "REMOVED",
    "REMOVING",
    "SKIPPED",
    "SLEEPING",
}


def _railway_env() -> dict[str, str]:
    env = os.environ.copy()
    env["RAILWAY_CALLER"] = "example:pounce-railway-smoke"
    env["RAILWAY_AGENT_SESSION"] = SESSION_ID
    return env


def _run_railway(args: list[str], *, capture: bool = False) -> str:
    executable = shutil.which("railway")
    if executable is None:
        raise RuntimeError("Railway CLI is required: https://docs.railway.com/cli")
    result = subprocess.run(  # noqa: S603 -- fixed executable plus an argument vector, no shell
        [executable, *args],
        cwd=RECIPE_DIR,
        env=_railway_env(),
        check=True,
        text=True,
        capture_output=capture,
    )
    return result.stdout if capture else ""


def _deployment_items(payload: str) -> list[dict[str, Any]]:
    data = json.loads(payload)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("deployments", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    raise ValueError("Railway deployment list returned an unsupported JSON shape")


def _latest(project: str, environment: str, service: str) -> tuple[str, str]:
    payload = _run_railway(
        [
            "deployment",
            "list",
            "--project",
            project,
            "--environment",
            environment,
            "--service",
            service,
            "--limit",
            "1",
            "--json",
        ],
        capture=True,
    )
    items = _deployment_items(payload)
    if not items:
        return "", ""
    item = items[0]
    return str(item.get("id", "")), str(item.get("status", "")).upper()


def _deploy(project: str, environment: str, service: str, message: str) -> None:
    _run_railway(
        [
            "up",
            ".",
            "--path-as-root",
            "--project",
            project,
            "--environment",
            environment,
            "--service",
            service,
            "--detach",
            "--message",
            message,
        ]
    )


def _wait_for_new_success(
    project: str,
    environment: str,
    service: str,
    previous_id: str,
    timeout: float,
) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        deployment_id, status = _latest(project, environment, service)
        if deployment_id and deployment_id != previous_id:
            print(f"deployment {deployment_id}: {status or 'pending'}", flush=True)
            if status == "SUCCESS":
                return deployment_id
            if status in TERMINAL_FAILURES:
                raise RuntimeError(f"Railway deployment {deployment_id} ended in {status}")
        time.sleep(5)
    raise TimeoutError(f"Railway deployment did not reach SUCCESS within {timeout:.0f}s")


def _get(url: str, *, timeout: float = 10.0) -> bytes:
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
        connection.request("GET", path, headers={"user-agent": "pounce-railway-smoke/1"})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return response.read()
    finally:
        connection.close()


def _wait_ready(origin: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    last_error: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            body = _get(f"{origin}/readyz")
            if b'"status":"ok"' in body or b'"status": "ok"' in body:
                return
        except (OSError, RuntimeError, http.client.HTTPException) as exc:
            last_error = exc
        time.sleep(1)
    raise TimeoutError(f"{origin}/readyz did not become ready") from last_error


@dataclass(slots=True)
class TrafficProbe:
    """Continuously sample an endpoint while Railway switches deployments."""

    url: str
    interval: float
    stop: threading.Event = field(default_factory=threading.Event)
    successes: int = 0
    failures: list[str] = field(default_factory=list)

    def run(self) -> None:
        while not self.stop.is_set():
            try:
                body = _get(self.url, timeout=10)
                if b'"status":"ok"' not in body:
                    raise RuntimeError(f"unexpected body: {body[:120]!r}")
                self.successes += 1
            except Exception as exc:
                self.failures.append(f"{type(exc).__name__}: {exc}")
            self.stop.wait(self.interval)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Railway project ID")
    parser.add_argument("--environment", required=True, help="Railway environment ID or name")
    parser.add_argument("--service", required=True, help="Railway service ID or name")
    parser.add_argument("--origin", required=True, help="Public HTTPS origin")
    parser.add_argument("--timeout", type=float, default=900, help="Seconds per deployment")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    origin = args.origin.rstrip("/")

    previous_id, _ = _latest(args.project, args.environment, args.service)
    _deploy(args.project, args.environment, args.service, "Pounce Railway recipe smoke")
    first_id = _wait_for_new_success(
        args.project,
        args.environment,
        args.service,
        previous_id,
        args.timeout,
    )
    _wait_ready(origin)
    sample = _get(f"{origin}/")
    parsed = json.loads(sample)
    if parsed.get("status") != "ok" or parsed.get("gil_enabled") is not False:
        raise RuntimeError(f"unexpected sample response: {parsed!r}")

    fast = TrafficProbe(f"{origin}/", interval=0.1)
    slow = TrafficProbe(f"{origin}/slow", interval=0.1)
    threads = [
        threading.Thread(target=fast.run, name="railway-fast-probe", daemon=True),
        threading.Thread(target=slow.run, name="railway-slow-probe", daemon=True),
    ]
    for thread in threads:
        thread.start()

    try:
        _deploy(args.project, args.environment, args.service, "Pounce graceful redeploy smoke")
        second_id = _wait_for_new_success(
            args.project,
            args.environment,
            args.service,
            first_id,
            args.timeout,
        )
        _wait_ready(origin)
    finally:
        fast.stop.set()
        slow.stop.set()
        for thread in threads:
            thread.join(timeout=15)

    failures = [*fast.failures, *slow.failures]
    if failures:
        details = "\n".join(failures[:10])
        raise RuntimeError(f"traffic dropped during redeploy:\n{details}")
    if fast.successes < 5 or slow.successes < 1:
        raise RuntimeError(
            f"redeploy traffic proof was too short: fast={fast.successes}, slow={slow.successes}"
        )

    print(
        "Railway smoke passed: "
        f"initial={first_id}, redeploy={second_id}, "
        f"fast_requests={fast.successes}, slow_requests={slow.successes}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
