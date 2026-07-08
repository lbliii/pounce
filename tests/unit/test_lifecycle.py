"""Tests for pounce.lifecycle — connection lifecycle events and collectors."""

import threading

from pounce.lifecycle import (
    BufferedCollector,
    ClientDisconnected,
    ConnectionCompleted,
    ConnectionOpened,
    LifecycleEvent,
    NoopCollector,
    RequestStarted,
    ResponseCompleted,
    StreamClosed,
    StreamOpened,
    monotonic_ns,
    next_connection_id,
)


class TestEventTypes:
    """Lifecycle event dataclasses are frozen, slotted, and correct."""

    def test_connection_opened_frozen(self):
        event = ConnectionOpened(
            connection_id=1,
            worker_id=0,
            client_addr="127.0.0.1",
            client_port=5000,
            server_addr="0.0.0.0",
            server_port=8000,
            protocol="h1",
            timestamp_ns=monotonic_ns(),
        )
        assert event.connection_id == 1
        assert event.protocol == "h1"
        # Frozen — assignment raises
        try:
            event.connection_id = 2  # type: ignore[misc]
            raise AssertionError("Should have raised")
        except AttributeError:
            pass

    def test_request_started_fields(self):
        event = RequestStarted(
            connection_id=1,
            worker_id=0,
            method="GET",
            path="/api/health",
            http_version="1.1",
            timestamp_ns=monotonic_ns(),
        )
        assert event.method == "GET"
        assert event.path == "/api/health"

    def test_response_completed_fields(self):
        event = ResponseCompleted(
            connection_id=1,
            worker_id=0,
            status=200,
            bytes_sent=1024,
            duration_ms=12.3,
            timestamp_ns=monotonic_ns(),
        )
        assert event.status == 200
        assert event.bytes_sent == 1024

    def test_client_disconnected_fields(self):
        event = ClientDisconnected(
            connection_id=1,
            worker_id=0,
            during_streaming=True,
            timestamp_ns=monotonic_ns(),
        )
        assert event.during_streaming is True

    def test_stream_lifecycle_fields(self):
        opened = StreamOpened(
            connection_id=1,
            worker_id=2,
            method="GET",
            path="/events",
            timestamp_ns=monotonic_ns(),
        )
        closed = StreamClosed(
            connection_id=1,
            worker_id=2,
            duration_ms=250.0,
            reason="drain",
            timestamp_ns=monotonic_ns(),
        )
        assert opened.path == "/events"
        assert closed.reason == "drain"

    def test_connection_closed_fields(self):
        event = ConnectionCompleted(
            connection_id=1,
            worker_id=0,
            requests_served=5,
            total_bytes_sent=4096,
            duration_ms=1500.0,
            reason="complete",
            timestamp_ns=monotonic_ns(),
        )
        assert event.requests_served == 5
        assert event.reason == "complete"


class TestConnectionIdGenerator:
    """next_connection_id() produces unique, monotonically increasing IDs."""

    def test_monotonically_increasing(self):
        id1 = next_connection_id()
        id2 = next_connection_id()
        id3 = next_connection_id()
        assert id1 < id2 < id3

    def test_thread_safety(self):
        """IDs are unique even when generated from multiple threads."""
        ids: list[int] = []
        lock = threading.Lock()

        def generate(count: int) -> None:
            local_ids = [next_connection_id() for _ in range(count)]
            with lock:
                ids.extend(local_ids)

        threads = [threading.Thread(target=generate, args=(100,)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(ids) == 400
        assert len(set(ids)) == 400  # All unique


class TestNoopCollector:
    """NoopCollector discards events silently."""

    def test_record_does_nothing(self):
        collector = NoopCollector()
        event = ConnectionOpened(
            connection_id=1,
            worker_id=0,
            client_addr="127.0.0.1",
            client_port=5000,
            server_addr="0.0.0.0",
            server_port=8000,
            protocol="h1",
            timestamp_ns=monotonic_ns(),
        )
        # Should not raise
        collector.record(event)


class TestBufferedCollector:
    """BufferedCollector accumulates events for batch processing."""

    def _make_event(self, conn_id: int = 1) -> ConnectionOpened:
        return ConnectionOpened(
            connection_id=conn_id,
            worker_id=0,
            client_addr="127.0.0.1",
            client_port=5000,
            server_addr="0.0.0.0",
            server_port=8000,
            protocol="h1",
            timestamp_ns=monotonic_ns(),
        )

    def test_record_and_flush(self):
        collector = BufferedCollector()
        collector.record(self._make_event(1))
        collector.record(self._make_event(2))

        events = collector.flush()
        assert len(events) == 2
        assert events[0].connection_id == 1
        assert events[1].connection_id == 2

    def test_flush_clears_buffer(self):
        collector = BufferedCollector()
        collector.record(self._make_event())

        first = collector.flush()
        assert len(first) == 1

        second = collector.flush()
        assert len(second) == 0

    def test_len(self):
        collector = BufferedCollector()
        assert len(collector) == 0
        collector.record(self._make_event())
        assert len(collector) == 1

    def test_auto_flush_on_max_size(self):
        flushed: list[list[LifecycleEvent]] = []
        collector = BufferedCollector(
            max_buffer_size=3,
            on_flush=lambda batch: flushed.append(batch),
        )

        collector.record(self._make_event(1))
        collector.record(self._make_event(2))
        assert len(flushed) == 0  # Not yet

        collector.record(self._make_event(3))
        assert len(flushed) == 1  # Auto-flushed
        assert len(flushed[0]) == 3

    def test_on_flush_callback(self):
        batches: list[list[LifecycleEvent]] = []
        collector = BufferedCollector(on_flush=lambda b: batches.append(b))

        collector.record(self._make_event())
        collector.flush()

        assert len(batches) == 1
        assert len(batches[0]) == 1

    def test_thread_safety(self):
        """Multiple threads can record concurrently without data loss."""
        collector = BufferedCollector()

        def record_batch(start: int) -> None:
            for i in range(50):
                collector.record(self._make_event(start + i))

        threads = [threading.Thread(target=record_batch, args=(i * 50,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        events = collector.flush()
        assert len(events) == 200

    def test_mixed_event_types(self):
        collector = BufferedCollector()
        ts = monotonic_ns()

        collector.record(
            ConnectionOpened(
                connection_id=1,
                worker_id=0,
                client_addr="127.0.0.1",
                client_port=5000,
                server_addr="0.0.0.0",
                server_port=8000,
                protocol="h1",
                timestamp_ns=ts,
            )
        )
        collector.record(
            RequestStarted(
                connection_id=1,
                worker_id=0,
                method="GET",
                path="/",
                http_version="1.1",
                timestamp_ns=ts,
            )
        )
        collector.record(
            ResponseCompleted(
                connection_id=1,
                worker_id=0,
                status=200,
                bytes_sent=256,
                duration_ms=5.0,
                timestamp_ns=ts,
            )
        )
        collector.record(
            ConnectionCompleted(
                connection_id=1,
                worker_id=0,
                requests_served=1,
                total_bytes_sent=256,
                duration_ms=10.0,
                reason="complete",
                timestamp_ns=ts,
            )
        )

        events = collector.flush()
        assert len(events) == 4
        assert isinstance(events[0], ConnectionOpened)
        assert isinstance(events[1], RequestStarted)
        assert isinstance(events[2], ResponseCompleted)
        assert isinstance(events[3], ConnectionCompleted)
