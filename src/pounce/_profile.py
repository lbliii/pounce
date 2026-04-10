"""Request profiling — enabled via POUNCE_PROFILE=1.

Samples every Nth request and logs read/parse/app/drain timings to stderr
for bottleneck analysis. See docs/benchmark-pounce-chirp-deep-dive.md.
"""

import os
import sys
from dataclasses import dataclass


def _enabled() -> bool:
    return os.environ.get("POUNCE_PROFILE", "").lower() in ("1", "true", "yes")


def _sample_interval() -> int:
    try:
        return max(1, int(os.environ.get("POUNCE_PROFILE_INTERVAL", "50")))
    except ValueError:
        return 50


@dataclass(slots=True)
class RequestProfile:
    """Per-request timing samples."""

    read_ms: float = 0.0
    parse_ms: float = 0.0
    app_ms: float = 0.0
    drain_ms: float = 0.0


class ProfileCollector:
    """Collects sampled request timings and logs summaries."""

    __slots__ = ("_count", "_enabled", "_interval", "_samples", "_worker_id")

    def __init__(self, worker_id: int = 0) -> None:
        self._enabled = _enabled()
        self._interval = _sample_interval()
        self._count = 0
        self._samples: list[RequestProfile] = []
        self._worker_id = worker_id

    def should_sample(self) -> bool:
        if not self._enabled:
            return False
        self._count += 1
        return self._count % self._interval == 0

    def record(self, sample: RequestProfile) -> None:
        if not self._enabled:
            return
        self._samples.append(sample)
        if len(self._samples) >= 5:
            self._flush()

    def _flush(self) -> None:
        if not self._samples:
            return
        n = len(self._samples)
        read_avg = sum(s.read_ms for s in self._samples) / n
        parse_avg = sum(s.parse_ms for s in self._samples) / n
        app_avg = sum(s.app_ms for s in self._samples) / n
        drain_avg = sum(s.drain_ms for s in self._samples) / n
        p99_idx = min(int(n * 0.99), n - 1)
        sorted_read = sorted(s.read_ms for s in self._samples)
        sorted_drain = sorted(s.drain_ms for s in self._samples)
        read_p99 = sorted_read[p99_idx]
        drain_p99 = sorted_drain[p99_idx]
        msg = (
            f"POUNCE_PROFILE worker={self._worker_id} n={n} "
            f"read_avg={read_avg:.2f}ms read_p99={read_p99:.2f}ms "
            f"parse_avg={parse_avg:.2f}ms app_avg={app_avg:.2f}ms "
            f"drain_avg={drain_avg:.2f}ms drain_p99={drain_p99:.2f}ms\n"
        )
        sys.stderr.write(msg)
        sys.stderr.flush()
        self._samples.clear()
