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
        for k, v in _DEFAULTS.items():
            setattr(self, k, v)
        for k, v in _DEFAULTS.items():
            raw = os.environ.get("SHARD_RESILIENCE_" + k.upper())
            if raw is not None:
                self._set(k, raw)
        self.configure(**overrides)
    def _set(self, key, value):
        default = _DEFAULTS[key]
        if isinstance(default, bool):
            val = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(default, int):
            val = int(float(value))
        else:
            val = float(value)
        if not isinstance(default, bool) and val < 0:
            raise ValueError(f"{key} must be non-negative")
        if key in ("poll_slice_s", "heartbeat_interval_s", "slow_step_warn_s") and val <= 0:
            raise ValueError(f"{key} must be positive")
        if key in ("suspect_missed", "dead_missed") and val < 1:
            raise ValueError(f"{key} must be at least 1")
        setattr(self, key, val)
    def configure(self, **kw):
        with self._lock:
            for k, v in kw.items():
                if k not in _DEFAULTS:
                    raise KeyError(f"Unknown resilience setting: {k}")
                if v is not None:
                    self._set(k, v)
            if self.dead_missed <= self.suspect_missed:
                self.dead_missed = self.suspect_missed + 1
        return self.as_dict()
    def reset(self):
        with self._lock:
            for k, v in _DEFAULTS.items():
                setattr(self, k, v)
    @property
    def suspect_after_s(self):
        return float(self.heartbeat_interval_s) * int(self.suspect_missed)
    @property
    def dead_after_s(self):
        return float(self.heartbeat_interval_s) * int(self.dead_missed)
    def probation_s(self, fail_count):
        return min(float(self.recovery_probation_s) * (2 ** max(0, int(fail_count) - 1)), 600.0)
    def backoff_s(self, attempt):
        return min(float(self.retry_backoff_s) * (2 ** max(0, int(attempt))), float(self.retry_backoff_max_s))
    def as_dict(self):
        out = {k: getattr(self, k) for k in _DEFAULTS}
