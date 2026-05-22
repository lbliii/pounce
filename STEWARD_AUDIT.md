# Steward Self-Audit

This records the Phase 4 swarm performed while bootstrapping the steward
network. Each factual finding carries a verification status. Findings marked
fixed were applied before this file was finalized.

## Swarm Coverage

- Runtime and public API: completed.
- Protocol: completed.
- ASGI bridge: completed.
- Operator output templates: completed.
- Design and troubleshooting: completed.
- Examples: completed.
- Performance evidence: completed.
- Planning: completed.
- Transport and evidence stewards: subagents timed out; residual audit coverage is
  manual-confirmation-needed.
- Public site and CI/release stewards: subagents were shut down before returning
  findings; residual audit coverage is manual-confirmation-needed.

## Convergence

Two independent stewards flagged the stale `/info` redaction ADR. The finding was
promoted to P0 under the convergence rule and fixed by updating
`docs/design/info-endpoint-redaction.md` to point to `_config_schema.INFO_ALLOWLIST`.

Steward: Runtime and Design
Area: `/info` redaction source of truth
Severity: P0
Invariant: Security/redaction design docs must route agents to the implementation
owner and tests that actually enforce exposure rules.
Evidence: `docs/design/info-endpoint-redaction.md` now names
`_config_schema.INFO_ALLOWLIST`; `tests/unit/test_config_schema.py` and
`tests/unit/test_introspect.py` cover allowlist and introspection behavior.
User Impact: Following the old ADR would send agents to the wrong module for a
security-sensitive allowlist.
Required Fix: Replace stale `_INFO_ALLOWLIST` guidance with `_config_schema.INFO_ALLOWLIST`.
Required Proof: `rg -n "_INFO_ALLOWLIST|INFO_ALLOWLIST|test_config_schema|test_introspect" docs/design/info-endpoint-redaction.md docs/AGENTS.md`.
Collateral: `docs/design/info-endpoint-redaction.md`, `docs/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

## Fixed Findings

Steward: Runtime and public API
Area: Public testing API coverage
Severity: P2
Invariant: The runtime steward must own public testing helpers and pytest plugin
registration.
Evidence: `pyproject.toml` registers the `pytest11` plugin; `src/pounce/testing.py`
exports `TestServer`, `serve()`, and `pounce_server`.
Required Fix: Add testing API ownership and checklist routing to `src/pounce/AGENTS.md`.
Required Proof: `rg -n "testing|pytest11|TestServer|pounce_server" src/pounce/AGENTS.md pyproject.toml`.
Collateral: `src/pounce/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Runtime and public API
Area: Runtime worker bootstrap coverage
Severity: P2
Invariant: Worker-mode and subinterpreter support files must be routed by the
runtime steward.
Evidence: `src/pounce/_runtime.py` and `src/pounce/_subinterpreter_bootstrap.py`
participate in runtime mode and bootstrap behavior.
Required Fix: Add those files and related tests to `src/pounce/AGENTS.md`.
Required Proof: `rg -n "_runtime|_subinterpreter_bootstrap|test_runtime|test_subinterpreter" src/pounce/AGENTS.md`.
Collateral: `src/pounce/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Protocol
Area: HTTP/3 boundary honesty
Severity: P2
Invariant: H3 docs and steward text must not claim `_base.py` typed event output
when the implementation uses asyncio datagram integration around zoomies.
Evidence: `_h3_handler.py` uses datagram integration and H3 ASGI helpers rather
than `_base.py` `ProtocolEvent` output.
Required Fix: Reword protocol steward and site extension docs around H3 status.
Required Proof: `rg -n "ProtocolEvent|Datagram|zoomies|H3 boundary" src/pounce/protocols/AGENTS.md site/content/docs/extending/asgi-bridge.md src/pounce/_h3_handler.py`.
Collateral: `src/pounce/protocols/AGENTS.md`, `site/content/docs/extending/asgi-bridge.md`.
Confidence: High
Verification Status: machine-verified

Steward: Protocol
Area: Response framing and sendfile
Severity: P2
Invariant: Approved framing bypasses must be routed to tests and docs rather than
forbidden as parser violations.
Evidence: `_response_frame.py` and `pounce.sendfile` intentionally bypass h11
through guarded helper paths.
Required Fix: Add approved framing bypass guidance and ownership entries.
Required Proof: `rg -n "_response_frame|sendfile|Approved framing" src/pounce/protocols/AGENTS.md`.
Collateral: `src/pounce/protocols/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: ASGI bridge
Area: WebSocket and sync-handoff routing
Severity: P2
Invariant: The ASGI steward must route WebSocket bridge behavior and sync worker
handoff proof to the actual files and tests.
Evidence: `ws_bridge.py`, `test_ws_protocol.py`, `test_tenant_scope_matrix.py`,
and `tests/unit/test_sync_worker.py::TestSyncWorkerHandoffs` cover those surfaces.
Required Fix: Add those paths to `src/pounce/asgi/AGENTS.md`.
Required Proof: `rg -n "ws_bridge|test_ws_protocol|test_tenant_scope_matrix|TestSyncWorkerHandoffs" src/pounce/asgi/AGENTS.md`.
Collateral: `src/pounce/asgi/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Operator output templates
Area: Health and info endpoint routing
Severity: P2
Invariant: The output steward must own rendered operator endpoints as well as
template files.
Evidence: `_health.py`, `_introspect.py`, `test_health.py`, and `test_introspect.py`
govern health/info payloads and exposure.
Required Fix: Add endpoint files, tests, and redaction docs to `src/pounce/templates/AGENTS.md`.
Required Proof: `rg -n "_health|_introspect|test_health|test_introspect|info-endpoint-redaction" src/pounce/templates/AGENTS.md`.
Collateral: `src/pounce/templates/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Operator output templates
Area: Startup output mode language
Severity: P3
Invariant: Steward guidance should describe current output modes, not stale
quiet/verbose terminology.
Evidence: The steward now routes pretty, JSON, text, plain, and non-TTY output.
Required Fix: Replace stale mode wording in `src/pounce/templates/AGENTS.md`.
Required Proof: `rg -n "pretty|JSON|text|plain|non-TTY|quiet|verbose" src/pounce/templates/AGENTS.md`.
Collateral: `src/pounce/templates/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Design and troubleshooting
Area: Redaction and introspection proof routing
Severity: P2
Invariant: Redaction docs must route to the tests that enforce the contract.
Evidence: `tests/unit/test_config_schema.py` covers `INFO_ALLOWLIST`; `tests/unit/test_introspect.py`
covers `/_pounce/info` redaction and public-bind warnings.
Required Fix: Add those tests to `docs/AGENTS.md`.
Required Proof: `rg -n "test_config_schema|test_introspect|INFO_ALLOWLIST|redaction" docs/AGENTS.md`.
Collateral: `docs/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Design and troubleshooting
Area: HTTP/3 roadmap status
Severity: P3
Invariant: Historical roadmap material must not outrank current core contract and
proof-ledger state.
Evidence: `docs/design/http3-roadmap.md` preserves historical roadmap content;
current HTTP/3 status lives in `core-contract.md` and `protocol-proof-ledger.json`.
Required Fix: Reword `docs/AGENTS.md` to mark roadmap files as historical context
unless current tests and ledgers agree.
Required Proof: `rg -n "http3-roadmap|historical|core-contract|protocol-proof-ledger" docs/AGENTS.md`.
Collateral: `docs/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Examples
Area: HTTP/3 prototype status
Severity: P2
Invariant: Optional-limited protocol examples must state prototype status and
known caveats.
Evidence: `docs/design/core-contract.md` and `docs/design/protocol-proof-ledger.json`
mark HTTP/3 as optional-limited with lifecycle, reload/drain, shutdown, 0-RTT,
benchmark, and WebSocket-over-H3 gaps.
Required Fix: Update `examples/README.md` and `examples/http3_prototype.py`.
Required Proof: `rg -n "HTTP/3|limited|optional-limited|prototype|reload|drain|WebSocket over HTTP/3" examples/README.md examples/http3_prototype.py`.
Collateral: `examples/README.md`, `examples/http3_prototype.py`.
Confidence: High
Verification Status: machine-verified

Steward: Examples
Area: Smoke-test routing
Severity: P2
Invariant: Steward proof claims must match what tests exercise.
Evidence: `tests/integration/test_examples.py` does not execute all run commands;
`tests/integration/test_subinterpreter.py` covers subinterpreter example apps.
Required Fix: Narrow example-steward wording to selected import smoke tests and
route subinterpreter example coverage.
Required Proof: `rg -n "selected import|test_subinterpreter|snippet smoke" examples/AGENTS.md`.
Collateral: `examples/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Performance evidence
Area: Benchmark JSON output vs artifact policy
Severity: P2
Invariant: Runner JSON output must not be mistaken for artifact-schema output.
Evidence: `benchmarks/run_benchmark.py --output` writes structured runner output;
`benchmarks/artifact-schema.json` requires additional metadata.
Required Fix: Label runner output as non-artifact unless schema fields are present.
Required Proof: `rg -n "structured runner output|not a benchmark artifact|artifact-schema" benchmarks/README.md benchmarks/AGENTS.md`.
Collateral: `benchmarks/README.md`, `benchmarks/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Performance evidence
Area: Benchmark task routing
Severity: P2
Invariant: The repo task for benchmarks must run benchmark-owned tests.
Evidence: Benchmark-marked tests live under `benchmarks/`; the old poe task ran
`pytest tests/ -m benchmark --benchmark-only`.
Required Fix: Change the poe `bench` task to `pytest benchmarks/ -m benchmark --benchmark-only`
and add steward checklist routing.
Required Proof: `rg -n "bench =|pytest benchmarks|pyproject.toml" pyproject.toml benchmarks/AGENTS.md`.
Collateral: `pyproject.toml`, `benchmarks/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Performance evidence
Area: Release-note numeric claim coverage
Severity: P2
Invariant: Release-note performance numbers are governed by the same public claim
policy as README and site docs.
Evidence: `site/content/releases/0.3.0.md` contains `req/s` claims; the claim
ledger now includes a historical snapshot entry and tests include that release page.
Required Fix: Add the release note to `docs/design/public-claims.json` and
`tests/unit/test_public_contract.py`.
Required Proof: `rg -n "req/s|release-0.3.0|site/content/releases/0.3.0" site/content/releases/0.3.0.md docs/design/public-claims.json tests/unit/test_public_contract.py`.
Collateral: `docs/design/public-claims.json`, `tests/unit/test_public_contract.py`.
Confidence: High
Verification Status: machine-verified

Steward: Planning
Area: Backlog and roadmap swarm routing
Severity: P2
Invariant: Roadmap and prioritization work consults all scoped stewards unless an
omission is explicit.
Evidence: Root swarm rules require raw signals, convergence, minority reports,
ranked backlog, and not-now items.
Required Fix: Add all-steward consultation and omission rationale to `plan/AGENTS.md`.
Required Proof: `rg -n "consults all scoped stewards|manual-confirmation-needed|raw steward signals" plan/AGENTS.md`.
Collateral: `plan/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Planning
Area: Roadmap file routing
Severity: P2
Invariant: Roadmap-adjacent files outside `plan/` need explicit routing because
closest-AGENTS loading will not pick up the planning steward.
Evidence: `ROADMAP.md` and `docs/design/*roadmap*.md` are routed in root and
planning steward checklists.
Required Fix: Add explicit roadmap routing to `AGENTS.md`, `plan/AGENTS.md`, and
`docs/AGENTS.md`.
Required Proof: `rg -n "ROADMAP.md|docs/design/.*roadmap" AGENTS.md plan/AGENTS.md docs/AGENTS.md`.
Collateral: `AGENTS.md`, `plan/AGENTS.md`, `docs/AGENTS.md`.
Confidence: High
Verification Status: machine-verified

Steward: Planning
Area: Implemented-record link discipline
Severity: P3
Invariant: Implemented records point to shipped proof when available.
Evidence: `plan/implemented/rfc-client-disconnect-detection.md` and
`plan/implemented/rfc-per-worker-lifecycle.md` now name shipped tests and collateral.
Required Fix: Add direct proof references.
Required Proof: `rg -n "tests/unit/test_disconnect|tests/unit/test_worker_lifecycle|CHANGELOG|docs/design" plan/implemented/*.md`.
Collateral: `plan/implemented/*.md`.
Confidence: High
Verification Status: machine-verified

## Deferred Findings

Steward: ASGI bridge
Area: WebSocket rejection header sanitization
Severity: P1
Invariant: App-provided headers that are serialized onto the wire should pass
through the same safety guard used by HTTP/H2/H3 response paths.
Evidence: The ASGI subagent reported that WebSocket rejection response headers in
`src/pounce/asgi/ws_bridge.py` do not use `_sanitize_headers`, while other HTTP
paths do.
User Impact: A malformed WebSocket rejection header could bypass the response
header guard.
Required Fix: Route `ws_bridge.py` rejection responses through the header
sanitizer or add an equivalent guard.
Required Proof: Add a unit test for malformed WebSocket rejection headers and
run the targeted WebSocket bridge tests.
Collateral: `src/pounce/asgi/AGENTS.md` now routes the risk; runtime code fix is
deferred outside the steward bootstrap.
Confidence: Medium
Verification Status: manual-confirmation-needed

Steward: Operator output templates
Area: `pounce info` non-pretty parity
Severity: P2
Invariant: Operator endpoints and CLI output modes should not silently omit
important install/framework fields in non-pretty modes.
Evidence: The output steward reported a non-pretty parity gap for install path
and frameworks in `pounce info`.
User Impact: Operators using machine-readable or non-pretty output may miss
diagnostic context.
Required Fix: Audit `pounce info` mode parity and update output/tests if the gap
is confirmed.
Required Proof: Compare pretty, text, JSON, plain, and non-TTY `pounce info`
tests after a targeted code audit.
Collateral: `src/pounce/templates/AGENTS.md` now routes health/info output; code
fix is deferred outside the steward bootstrap.
Confidence: Medium
Verification Status: manual-confirmation-needed

## Discarded Findings

No subagent finding was discarded as disproven. The only incomplete results were
from timed-out or shut-down agents, and those are recorded as residual audit risk
instead of converted into findings.
