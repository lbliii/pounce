"""Tests for the HTTP/2 protocol handler."""

import pytest

try:
    import h2.config
    import h2.connection
    import h2.events

    _HAS_H2 = True
except ImportError:
    _HAS_H2 = False

pytestmark = pytest.mark.skipif(
    not _HAS_H2,
    reason="h2 not installed",
)


def _make_client_server():
    """Create a paired client/server H2 connection.

    Returns (client, server) with completed handshake.
    """
    from pounce.protocols.h2 import H2Connection

    server = H2Connection()
    server.initiate_connection()
    server_preface = server.data_to_send()

    client_config = h2.config.H2Configuration(
        client_side=True,
        header_encoding="utf-8",
    )
    client = h2.connection.H2Connection(config=client_config)
    client.initiate_connection()
    client.receive_data(server_preface)

    # Feed client preface to server
    server.receive_data(client.data_to_send())

    return client, server


class TestH2Connection:
    def test_init(self) -> None:
        from pounce.protocols.h2 import H2Connection

        conn = H2Connection()
        assert not conn.is_closed
        assert conn.active_stream_count == 0

    def test_initiate_connection(self) -> None:
        from pounce.protocols.h2 import H2Connection

        conn = H2Connection()
        conn.initiate_connection()
        preface = conn.data_to_send()
        assert len(preface) > 0

    def test_single_get_request(self) -> None:
        from pounce.protocols.h2 import H2BodyReceived, H2RequestReceived

        client, server = _make_client_server()

        # Client sends GET request
        client.send_headers(
            1,
            [
                (":method", "GET"),
                (":path", "/hello"),
                (":authority", "localhost"),
                (":scheme", "https"),
                ("accept", "text/html"),
            ],
            end_stream=True,
        )
        client_data = client.data_to_send()

        # Server receives it
        events = server.receive_data(client_data)
        server.data_to_send()  # consume any acks

        # Should get RequestReceived and BodyReceived (end of stream)
        assert len(events) >= 1
        req_events = [e for e in events if isinstance(e, H2RequestReceived)]
        assert len(req_events) == 1

        req = req_events[0]
        assert req.stream_id == 1
        assert req.request.method == b"GET"
        assert req.request.target == b"/hello"
        assert req.request.http_version == "2"

        # Should have body end event
        body_events = [e for e in events if isinstance(e, H2BodyReceived)]
        assert len(body_events) == 1
        assert not body_events[0].body.more

    def test_missing_required_pseudo_header_resets_stream(self) -> None:
        import h2.events

        from pounce.protocols.h2 import H2Connection, H2StreamReset

        class FakeConn:
            def __init__(self) -> None:
                self.reset_calls: list[tuple[int, int]] = []

            def receive_data(self, data: bytes) -> list[object]:
                return [
                    h2.events.RequestReceived(
                        stream_id=1,
                        headers=[
                            (":method", "GET"),
                            (":path", "/"),
                            (":authority", "example.test"),
                        ],
                    )
                ]

            def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
                self.reset_calls.append((stream_id, error_code))

        conn = H2Connection()
        fake = FakeConn()
        conn._conn = fake

        events = conn.receive_data(b"headers")

        assert [type(event) for event in events] == [H2StreamReset]
        assert fake.reset_calls == [(1, 1)]

    def test_authority_host_conflict_resets_stream(self) -> None:
        import h2.events

        from pounce.protocols.h2 import H2Connection, H2StreamReset

        class FakeConn:
            def __init__(self) -> None:
                self.reset_calls: list[tuple[int, int]] = []

            def receive_data(self, data: bytes) -> list[object]:
                return [
                    h2.events.RequestReceived(
                        stream_id=1,
                        headers=[
                            (":method", "GET"),
                            (":path", "/"),
                            (":scheme", "https"),
                            (":authority", "tenant-a.test"),
                            ("host", "tenant-b.test"),
                        ],
                    )
                ]

            def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
                self.reset_calls.append((stream_id, error_code))

        conn = H2Connection()
        fake = FakeConn()
        conn._conn = fake

        events = conn.receive_data(b"headers")

        assert [type(event) for event in events] == [H2StreamReset]
        assert fake.reset_calls == [(1, 1)]

    def test_post_request_with_body(self) -> None:
        from pounce.protocols.h2 import H2BodyReceived, H2RequestReceived

        client, server = _make_client_server()

        # Client sends POST with body
        client.send_headers(
            1,
            [
                (":method", "POST"),
                (":path", "/api/data"),
                (":authority", "localhost"),
                (":scheme", "https"),
                ("content-type", "application/json"),
            ],
        )
        body = b'{"key": "value"}'
        client.send_data(1, body, end_stream=True)
        client_data = client.data_to_send()

        events = server.receive_data(client_data)
        server.data_to_send()

        req_events = [e for e in events if isinstance(e, H2RequestReceived)]
        assert len(req_events) == 1
        assert req_events[0].request.method == b"POST"

        body_events = [e for e in events if isinstance(e, H2BodyReceived)]
        # Should have at least one data event and one end-stream event
        all_body = b"".join(e.body.data for e in body_events)
        assert body in all_body

    def test_multiplexed_requests(self) -> None:
        from pounce.protocols.h2 import H2RequestReceived

        client, server = _make_client_server()

        # Client sends two concurrent requests
        for stream_id, path in [(1, "/first"), (3, "/second")]:
            client.send_headers(
                stream_id,
                [
                    (":method", "GET"),
                    (":path", path),
                    (":authority", "localhost"),
                    (":scheme", "https"),
                ],
                end_stream=True,
            )
        client_data = client.data_to_send()

        events = server.receive_data(client_data)
        server.data_to_send()

        req_events = [e for e in events if isinstance(e, H2RequestReceived)]
        assert len(req_events) == 2
        paths = {e.request.target for e in req_events}
        assert paths == {b"/first", b"/second"}

    def test_send_response(self) -> None:
        client, server = _make_client_server()

        # Client sends request
        client.send_headers(
            1,
            [
                (":method", "GET"),
                (":path", "/"),
                (":authority", "localhost"),
                (":scheme", "https"),
            ],
            end_stream=True,
        )
        server.receive_data(client.data_to_send())
        server.data_to_send()

        # Server sends response
        server.send_response_headers(
            1,
            200,
            [(b"content-type", b"text/plain")],
        )
        server.send_data(1, b"Hello!", end_stream=True)
        response_data = server.data_to_send()

        # Client receives response
        client_events = client.receive_data(response_data)
        response_received = [e for e in client_events if isinstance(e, h2.events.ResponseReceived)]
        assert len(response_received) == 1
        headers_dict = dict(response_received[0].headers)
        assert headers_dict[":status"] == "200"

    def test_stream_reset(self) -> None:
        from pounce.protocols.h2 import H2StreamReset

        client, server = _make_client_server()

        # Client sends request
        client.send_headers(
            1,
            [
                (":method", "GET"),
                (":path", "/slow"),
                (":authority", "localhost"),
                (":scheme", "https"),
            ],
            end_stream=True,
        )
        server.receive_data(client.data_to_send())
        server.data_to_send()

        # Client cancels the request
        client.reset_stream(1, error_code=8)  # CANCEL
        server_events = server.receive_data(client.data_to_send())
        server.data_to_send()

        reset_events = [e for e in server_events if isinstance(e, H2StreamReset)]
        assert len(reset_events) == 1
        assert reset_events[0].stream_id == 1
        assert reset_events[0].error_code == 8

    def test_goaway(self) -> None:
        from pounce.protocols.h2 import H2GoAway

        client, server = _make_client_server()

        # Client sends GOAWAY
        client.close_connection(error_code=0)
        server_events = server.receive_data(client.data_to_send())
        server.data_to_send()

        goaway_events = [e for e in server_events if isinstance(e, H2GoAway)]
        assert len(goaway_events) == 1
        assert goaway_events[0].error_code == 0
        assert server.is_closed

    def test_authority_becomes_host_header(self) -> None:
        from pounce.protocols.h2 import H2RequestReceived

        client, server = _make_client_server()

        client.send_headers(
            1,
            [
                (":method", "GET"),
                (":path", "/"),
                (":authority", "example.com"),
                (":scheme", "https"),
            ],
            end_stream=True,
        )
        events = server.receive_data(client.data_to_send())
        server.data_to_send()

        req_events = [e for e in events if isinstance(e, H2RequestReceived)]
        assert len(req_events) == 1
        # :authority should be mapped to host header
        headers_dict = dict(req_events[0].request.headers)
        assert headers_dict[b"host"] == b"example.com"


class TestH2Availability:
    def test_is_h2_available(self) -> None:
        from pounce.protocols.h2 import is_h2_available

        assert is_h2_available() is True
