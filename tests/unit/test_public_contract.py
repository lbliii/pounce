"""Public contract parity checks.

These tests intentionally encode surface policy rather than behavior. They keep
public docs, config entrypoints, and optional dependency declarations from
quietly drifting apart.
"""

from __future__ import annotations

import inspect
import json
import re
import tomllib
from dataclasses import fields
from pathlib import Path
from typing import ClassVar

from pounce import _cli
from pounce._config_file import _EXCLUDED_FIELDS as TOML_EXCLUDED_FIELDS
from pounce._config_file import _VALID_KEYS as TOML_VALID_KEYS
from pounce._config_schema import build_schema
from pounce._output import _OPTIONAL_DEPS
from pounce.config import _IIC_SKIP_FIELDS, ServerConfig

ROOT = Path(__file__).resolve().parents[2]
CLAIM_LEDGER = ROOT / "docs/design/public-claims.json"
PROTOCOL_LEDGER = ROOT / "docs/design/protocol-proof-ledger.json"
BENCHMARK_SCHEMA = ROOT / "benchmarks/artifact-schema.json"


def _server_config_field_names() -> set[str]:
    return {field.name for field in fields(ServerConfig) if field.init}


class TestConfigSurfaceParity:
    def test_toml_keys_match_documented_policy(self) -> None:
        expected = _server_config_field_names() - TOML_EXCLUDED_FIELDS
        assert expected == TOML_VALID_KEYS

    def test_schema_keys_match_documented_policy(self) -> None:
        expected = _server_config_field_names() - _IIC_SKIP_FIELDS
        assert set(build_schema()["properties"]) == expected

    def test_serve_and_check_expose_same_flags(self) -> None:
        serve = set(inspect.signature(_cli.serve).parameters)
        check = set(inspect.signature(_cli.check).parameters)
        assert check == serve

    def test_config_show_documents_limited_overrides(self) -> None:
        params = set(inspect.signature(_cli.config_show).parameters)
        assert params == {"config", "output_format", "host", "port", "workers"}


class TestOptionalProtocolParity:
    def test_optional_deps_match_pyproject_extras(self) -> None:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        extras = pyproject["project"]["optional-dependencies"]

        expected_packages = {
            "h2": "h2",
            "ws": "wsproto",
            "tls": "truststore",
            "h3": "bengal-zoomies",
        }
        expected_modules = {
            "h2": "h2",
            "ws": "wsproto",
            "tls": "truststore",
            "h3": "zoomies",
        }
        actual = {
            extra: deps[0].split(">=", 1)[0]
            for extra, deps in extras.items()
            if extra in expected_packages
        }
        output_modules = {dep["module"] for dep in _OPTIONAL_DEPS}

        assert actual == expected_packages
        assert output_modules == set(expected_modules.values())
        assert set(extras["full"]) == set(
            extras["h2"] + extras["ws"] + extras["tls"] + extras["h3"]
        )

    def test_install_docs_name_every_protocol_extra(self) -> None:
        docs = [
            ROOT / "README.md",
            ROOT / "site/content/_index.md",
            ROOT / "site/content/docs/get-started/installation.md",
            ROOT / "site/content/docs/protocols/_index.md",
        ]
        expected = {
            "bengal-pounce[h2]",
            "bengal-pounce[ws]",
            "bengal-pounce[tls]",
            "bengal-pounce[h3]",
        }

        for path in docs:
            text = path.read_text()
            missing = {extra for extra in expected if extra not in text}
            assert missing == set(), f"{path} missing {sorted(missing)}"

    def test_readme_and_site_docs_include_http3_protocol_summary(self) -> None:
        docs = [
            ROOT / "README.md",
            ROOT / "site/content/_index.md",
            ROOT / "site/content/docs/_index.md",
            ROOT / "site/content/docs/protocols/_index.md",
        ]
        for path in docs:
            text = path.read_text()
            assert re.search(r"HTTP/1\.1", text), path
            assert "HTTP/2" in text, path
            assert "WebSocket" in text, path
            assert "HTTP/3" in text, path


class TestPublicClaimLedger:
    ACTIVE_PUBLIC_DOCS: ClassVar[list[Path]] = [
        ROOT / "README.md",
        ROOT / "site/content/_index.md",
        ROOT / "site/content/docs/_index.md",
        ROOT / "site/content/docs/about/_index.md",
        ROOT / "site/content/docs/about/architecture.md",
        ROOT / "site/content/docs/about/comparison.md",
        ROOT / "site/content/docs/about/faq.md",
        ROOT / "site/content/docs/about/performance.md",
        ROOT / "site/content/docs/deployment/backpressure.md",
        ROOT / "site/content/docs/features/lifecycle-logging.md",
        ROOT / "site/content/docs/features/middleware.md",
        ROOT / "site/content/docs/features/static-files.md",
        ROOT / "site/content/docs/features/websocket-compression.md",
        ROOT / "site/content/docs/protocols/http1.md",
    ]
    RISKY_PATTERNS: ClassVar[list[re.Pattern[str]]] = [
        re.compile(r"\bfull support\b", re.IGNORECASE),
        re.compile(r"\bfull ASGI\b", re.IGNORECASE),
        re.compile(r"\bzero-downtime\b", re.IGNORECASE),
        re.compile(r"\bproduction apps\b", re.IGNORECASE),
        re.compile(r"\bhigh-performance\b", re.IGNORECASE),
        re.compile(r"\bthread-safe(?:ty)?\b", re.IGNORECASE),
        re.compile(r"\bfree-threading safe\b", re.IGNORECASE),
        re.compile(r"\bRFC compliant\b", re.IGNORECASE),
        re.compile(r"\bevery response\b", re.IGNORECASE),
        re.compile(r"\balways\b", re.IGNORECASE),
        re.compile(r"\d+(?:\.\d+)?k?\s*req/s", re.IGNORECASE),
        re.compile(r"\d+(?:\.\d+)?\s*(?:us|µs)/request", re.IGNORECASE),
        re.compile(r"\d+(?:\.\d+)?\s*µs/req", re.IGNORECASE),
        re.compile(r"\d+(?:\.\d+)?\s*%"),
    ]

    def _ledger(self) -> dict[str, object]:
        return json.loads(CLAIM_LEDGER.read_text())

    def test_claim_ledger_entries_have_required_fields(self) -> None:
        ledger = self._ledger()
        claim_ids: set[str] = set()
        for claim in ledger["claims"]:
            assert set(claim) >= {"id", "type", "status", "files", "patterns", "proof", "notes"}
            assert claim["id"] not in claim_ids
            claim_ids.add(claim["id"])
            for rel in claim["files"]:
                assert (ROOT / rel).exists(), rel

    def test_risky_public_claims_are_in_ledger(self) -> None:
        ledger = self._ledger()
        allowed: dict[Path, list[str]] = {}
        for claim in ledger["claims"]:
            for rel in claim["files"]:
                allowed.setdefault(ROOT / rel, []).extend(claim["patterns"])
        for item in ledger["allowlist"]:
            allowed.setdefault(ROOT / item["file"], []).append(item["pattern"])

        failures: list[str] = []
        for path in self.ACTIVE_PUBLIC_DOCS:
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                if not any(pattern.search(line) for pattern in self.RISKY_PATTERNS):
                    continue
                if any(pattern.lower() in line.lower() for pattern in allowed.get(path, [])):
                    continue
                failures.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()}")

        assert failures == []


class TestProtocolProofLedger:
    def _ledger(self) -> dict[str, object]:
        return json.loads(PROTOCOL_LEDGER.read_text())

    def test_optional_protocols_have_proof_or_gaps(self) -> None:
        protocols = self._ledger()["protocols"]
        expected = {"http1", "http2", "websocket_h1", "websocket_h2", "http3", "websocket_h3"}
        assert set(protocols) == expected

        for name, entry in protocols.items():
            assert set(entry) == {"status", "install", "proof", "gaps"}, name
            if entry["status"] != "unsupported":
                assert entry["proof"], name
            if entry["status"].endswith("limited"):
                assert entry["gaps"], name

    def test_websocket_over_h3_is_explicitly_unsupported(self) -> None:
        protocols = self._ledger()["protocols"]
        assert protocols["websocket_h3"]["status"] == "unsupported"

        websocket_doc = (ROOT / "site/content/docs/protocols/websocket.md").read_text()
        http3_doc = (ROOT / "site/content/docs/protocols/http3.md").read_text()
        expected = "WebSocket over HTTP/3 is not currently supported"

        assert expected in websocket_doc
        assert expected in http3_doc


class TestBenchmarkArtifactPolicy:
    REQUIRED_FIELDS: ClassVar[set[str]] = {
        "artifact_id",
        "created_at",
        "git_sha",
        "command",
        "server_command",
        "workload",
        "python_version",
        "python_gil_mode",
        "os",
        "hardware",
        "worker_mode",
        "workers",
        "duration_seconds",
        "connections",
        "threads",
        "load_tool",
        "load_tool_version",
        "comparison_target",
        "comparison_target_version",
        "samples",
        "variance",
        "raw_output",
        "summary",
    }

    def test_benchmark_artifact_schema_names_required_metadata(self) -> None:
        schema = json.loads(BENCHMARK_SCHEMA.read_text())
        assert schema["version"] == 1
        assert set(schema["required_fields"]) == self.REQUIRED_FIELDS
        assert "not a product claim" in schema["notes"]

    def test_benchmark_policy_is_documented_for_contributors(self) -> None:
        contributing = (ROOT / "CONTRIBUTING.md").read_text()
        benchmarks = (ROOT / "benchmarks/README.md").read_text()

        assert "benchmarks/artifact-schema.json" in contributing
        assert "benchmarks/artifact-schema.json" in benchmarks
        assert "snapshot caveat" in benchmarks


class TestDocsCliSnippets:
    DOCS: ClassVar[list[Path]] = [
        ROOT / "README.md",
        ROOT / "benchmarks/README.md",
        *[
            path
            for path in (ROOT / "site/content").rglob("*.md")
            if "/releases/" not in path.as_posix()
        ],
    ]
    COMMAND_RE = re.compile(r"\bpounce(?:\s+serve|\s+bench|\s+config\s+\w+)?[^\n`|]*")
    FLAG_RE = re.compile(r"--[a-zA-Z0-9-]+")

    @staticmethod
    def _param_to_flag(name: str) -> str:
        return "--" + name.replace("_", "-")

    def test_serve_snippets_use_known_flags(self) -> None:
        serve_flags = {
            self._param_to_flag(name)
            for name in inspect.signature(_cli._serve_impl).parameters
            if name != "app"
        }
        # Milo exposes boolean negation flags from these implementation params.
        serve_flags |= {"--no-compression", "--no-access-log"}
        bench_flags = {"--workers", "--duration", "--compare"}
        config_flags = {"--output-format"}
        global_flags = {"--help"}

        failures: list[str] = []
        for path in self.DOCS:
            for lineno, line in enumerate(path.read_text().splitlines(), start=1):
                for command in self.COMMAND_RE.findall(line):
                    flags = set(self.FLAG_RE.findall(command))
                    if not flags:
                        continue
                    if re.search(r"\bpounce\s+bench(\s|$)", command):
                        allowed = bench_flags | global_flags
                    elif re.search(r"\bpounce\s+config\s+", command):
                        allowed = config_flags | global_flags
                    else:
                        allowed = serve_flags | global_flags
                    unknown = flags - allowed
                    if unknown:
                        failures.append(
                            f"{path.relative_to(ROOT)}:{lineno}: {sorted(unknown)} in {command}"
                        )

        assert failures == []
