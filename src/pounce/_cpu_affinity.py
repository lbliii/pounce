"""
CPU affinity for worker threads (Linux only).

When enabled, pins each worker to a dedicated CPU core to reduce cache
thrashing and improve throughput on multi-core systems.

"""

import logging
import os
import sys

from pounce.config import ServerConfig

logger = logging.getLogger("pounce.cpu_affinity")


def maybe_pin_worker(worker_id: int, config: ServerConfig) -> None:
    """Pin the current thread/process to a CPU core when enabled on Linux.

    Called at worker startup. No-op when:
    - cpu_affinity is False
    - Not running on Linux (os.sched_setaffinity is Linux-only)
    - sched_setaffinity raises (e.g. restricted cpuset in containers)

    Args:
        worker_id: Worker index (0, 1, ...).
        config: Server configuration (reads cpu_affinity).

    """
    if not config.cpu_affinity:
        return
    if sys.platform != "linux":
        return
    if not hasattr(os, "sched_setaffinity"):
        return

    cpu_count = os.cpu_count() or 1
    core = worker_id % cpu_count
    try:
        os.sched_setaffinity(0, {core})
        logger.debug("Worker %d pinned to CPU %d", worker_id, core)
    except OSError:
        # Containers, restricted cpusets, or permission issues
        logger.debug(
            "Could not pin worker %d to CPU %d (sched_setaffinity failed)",
            worker_id,
            core,
        )
