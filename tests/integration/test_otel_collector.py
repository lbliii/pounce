"""OTLP/HTTP collector-boundary proof for Pounce request spans."""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status


class _CollectorHandler(BaseHTTPRequestHandler):
    """Capture one real OTLP protobuf export without an external service."""

    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers["content-length"])
        payload = self.rfile.read(length)
        self.server.payloads.put((self.path, self.headers["content-type"], payload))
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep the integration test output quiet."""


class _CollectorServer(ThreadingHTTPServer):
    payloads: queue.Queue[tuple[str, str, bytes]]


def _attribute_map(attributes: object) -> dict[str, object]:
    values: dict[str, object] = {}
    for attribute in attributes:
        value = attribute.value
        field = value.WhichOneof("value")
        values[attribute.key] = getattr(value, field)
    return values


@pytest.mark.integration
def test_otlp_collector_receives_semantic_request_span() -> None:
    """The configured exporter sends the documented span contract over OTLP."""
    collector = _CollectorServer(("127.0.0.1", 0), _CollectorHandler)
    collector.payloads = queue.Queue()
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{collector.server_port}"
    child = """
import sys

from pounce._otel import RequestSpanManager, configure_otel, flush_otel

configure_otel(endpoint=sys.argv[1], service_name="pounce-semantic-proof")
manager = RequestSpanManager(service_name="pounce-semantic-proof")
span = manager.create_request_span(
    method="POST",
    path="/widgets/42",
    headers=[
        (
            b"traceparent",
            b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01",
        )
    ],
    scheme="https",
    server_host="api.example.test",
    server_port=443,
)
manager.record_exception(span, ValueError("collector proof"))
manager.record_response(span, status_code=503, response_size=321)
manager.end_span(span)
flush_otel()
"""

    try:
        subprocess.run(
            [sys.executable, "-c", child, endpoint],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        path, content_type, payload = collector.payloads.get(timeout=5)
    finally:
        collector.shutdown()
        thread.join(timeout=5)
        collector.server_close()

    assert path == "/v1/traces"
    assert content_type == "application/x-protobuf"

    export = ExportTraceServiceRequest()
    export.ParseFromString(payload)
    [resource_spans] = export.resource_spans
    resources = _attribute_map(resource_spans.resource.attributes)
    assert resources["service.name"] == "pounce-semantic-proof"

    [scope_spans] = resource_spans.scope_spans
    [span] = scope_spans.spans
    attributes = _attribute_map(span.attributes)
    assert span.name == "POST"
    assert span.kind == Span.SPAN_KIND_SERVER
    assert span.trace_id == bytes.fromhex("0af7651916cd43dd8448eb211c80319c")
    assert span.parent_span_id == bytes.fromhex("b7ad6b7169203331")
    assert span.status.code == Status.STATUS_CODE_ERROR
    assert attributes == {
        "http.request.method": "POST",
        "http.response.body.size": 321,
        "http.response.status_code": 503,
        "server.address": "api.example.test",
        "server.port": 443,
        "url.path": "/widgets/42",
        "url.scheme": "https",
    }
    assert [event.name for event in span.events] == ["exception"]
