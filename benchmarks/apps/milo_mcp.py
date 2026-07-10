"""Milo MCP workload for whole-stack Pounce benchmark evidence.

This app intentionally uses Milo's public ``CLI.asgi_app()`` boundary. Until
that API is available in a released Milo version, install the implementation
under review in milo-cli#127 before running this workload.
"""

from __future__ import annotations

import secrets
import time

from milo import CLI

_BENCHMARK_TOKEN = "pounce-benchmark-token"  # noqa: S105 - public benchmark fixture

cli = CLI(
    name="pounce-mcp-benchmark",
    description="Mixed CPU and blocking MCP benchmark workload",
    version="1.0",
)


@cli.command("cpu_digest")
def cpu_digest(iterations: int) -> str:
    """Perform deterministic CPU work without native extensions."""
    value = 0xCBF29CE484222325
    for index in range(iterations):
        value ^= index + 0x9E3779B97F4A7C15
        value = (value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
        value ^= value >> 33
    return f"{value:016x}"


@cli.command("blocking_lookup")
def blocking_lookup(delay_ms: int) -> str:
    """Model a blocking tool such as a subprocess or synchronous API call."""
    time.sleep(delay_ms / 1000)
    return "ready"


def _validate_benchmark_token(token: str) -> bool:
    return secrets.compare_digest(token, _BENCHMARK_TOKEN)


try:
    app = cli.asgi_app(
        token_validator=_validate_benchmark_token,
        protected_resource_metadata={
            "resource": "http://127.0.0.1/mcp",
            "authorization_servers": ["https://benchmark.invalid"],
            "scopes_supported": ["tools:call"],
        },
        allowed_origins=["https://benchmark.invalid"],
    )
except AttributeError as exc:  # pragma: no cover - only pre-release environments
    raise RuntimeError(
        "The MCP benchmark requires Milo CLI.asgi_app(); install milo-cli#127 "
        "until the API is available in a release."
    ) from exc
