import json
import logging
import os
import threading
import time
import uuid
from collections import deque
log = logging.getLogger("shard.resilience")
HEALTHY = "HEALTHY"
SUSPECT = "SUSPECT"
FAILED = "FAILED"
RECOVERING = "RECOVERING"
_DEFAULTS = {
    "heartbeat_interval_s": 2.4,
    "suspect_missed": 2,
    "dead_missed": 5,
    "monitor_tick_s": 1.0,
    "connect_timeout_s": 5.0,
    "load_timeout_s": 300.0,
    "prefill_timeout_s": 180.0,
    "decode_step_timeout_s": 30.0,
    "release_timeout_s": 2.0,
    "poll_slice_s": 0.25,
    "slow_step_warn_s": 10.0,
    "stall_timeout_s": 120.0,
    "request_deadline_s": 0.0,
    "max_recovery_attempts": 1,
    "recovery_wait_s": 60.0,
    "retry_backoff_s": 0.5,
    "retry_backoff_max_s": 30.0,
    "recovery_probation_s": 5.0,
    "enable_resume": True,
}
class ResiliencePolicy:
    def __init__(self, **overrides):
        self._lock = threading.Lock()
