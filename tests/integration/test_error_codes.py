"""Integration tests for pounce error-response wiring.

Induces real parse failures on a live worker and asserts that pounce-generated
4xx/5xx responses carry the ``X-Pounce-Error-Code`` header. In debug mode, the
body also includes the code and hint for easier triage.
"""

import pytest

from pounce._types import Receive, Scope, Send
from pounce.config import ServerConfig
from tests.conftest import send_raw_request, start_worker, with_lifespan


@with_lifespan
async def _ok_app(scope: Scope, receive: Receive, send: Send) -> None:
    await receive()
    body = b"ok"
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _header_value(response: bytes, name: bytes) -> bytes | None:
    """Return the value of *name* from *response*'s headers, or None."""
    head, _, _ = response.partition(b"\r\n\r\n")
    for line in head.split(b"\r\n")[1:]:
        key, sep, val = line.partition(b":")
        if sep and key.strip().lower() == name.lower():
            return val.strip()
    return None


class TestErrorCodeHeader:
    """Pounce-generated error responses carry X-Pounce-Error-Code."""

    def test_malformed_request_line_emits_code(self) -> None:
        config = ServerConfig(host="127.0.0.1", port=0, access_log=False)
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()
        try:
            response = send_raw_request(addr, b"NOPE\r\n\r\n", timeout=2.0)
            assert response.startswith(b"HTTP/1.1 400"), response[:64]
            code = _header_value(response, b"x-pounce-error-code")
            # Either the fast parser's MALFORMED_REQUEST_LINE or h11's
            # H11_REJECTED is acceptable — both route back to the same
            # human-meaningful category. What we're asserting is that the
            # header is emitted at all for pounce-generated errors.
            assert code in {
                b"POUNCE_PARSE_MALFORMED_REQUEST_LINE",
                b"POUNCE_PARSE_H11_REJECTED",
            }, response
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()

    @pytest.mark.parametrize("debug", [False, True])
    def test_debug_mode_body_shape(self, debug: bool) -> None:
        """In debug mode the body appends the code; otherwise it stays terse."""
        config = ServerConfig(
            host="127.0.0.1", port=0, access_log=False, debug=debug
        )
        worker, sock, thread = start_worker(_ok_app, config=config)
        addr = sock.getsockname()
        try:
            response = send_raw_request(addr, b"NOPE\r\n\r\n", timeout=2.0)
            assert response.startswith(b"HTTP/1.1 400"), response[:64]
            _, _, body = response.partition(b"\r\n\r\n")
            if debug:
                assert b"Pounce error code:" in body, body
            else:
                assert b"Pounce error code:" not in body, body
        finally:
            worker.shutdown()
            thread.join(timeout=2)
            sock.close()
