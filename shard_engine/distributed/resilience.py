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
        out["suspect_after_s"] = round(self.suspect_after_s, 3)
        out["dead_after_s"] = round(self.dead_after_s, 3)
        return out
POLICY = ResiliencePolicy()
_CODES = {
    "WORKER_LOST": (503, True),
    "WORKER_TIMEOUT": (504, True),
    "WORKER_STALLED": (504, True),
    "REQUEST_DEADLINE_EXCEEDED": (504, True),
    "CLUSTER_UNAVAILABLE": (503, True),
    "RECOVERY_FAILED": (503, True),
    "REQUEST_CANCELLED": (499, False),
    "STAGE_ERROR": (502, False),
    "STALE_RESPONSE": (502, True),
    "BACKEND_ERROR": (502, True),
    "INFERENCE_ERROR": (500, False),
}
class ResumeState:
    def __init__(self, generated_ids=None, delivered_text=""):
        self.generated_ids = list(generated_ids or [])
        self.delivered_text = str(delivered_text or "")
class InferenceFailure(RuntimeError):
    def __init__(self, code, message, request_id=None, worker_id=None, stage=None, phase=None, retryable=None, recovery_attempted=False, cluster_available=None, tokens_delivered=0, detail=None):
        super().__init__(message)
        self.code = code if code in _CODES else "INFERENCE_ERROR"
        self.message = str(message)
        self.request_id = request_id
        self.worker_id = worker_id
        self.stage = stage
        self.phase = phase
        self.retryable = _CODES[self.code][1] if retryable is None else bool(retryable)
        self.recovery_attempted = bool(recovery_attempted)
        self.cluster_available = cluster_available
        self.tokens_delivered = int(tokens_delivered or 0)
        self.detail = dict(detail or {})
        self.resume = None
        self.created_at = time.monotonic()
    @property
    def http_status(self):
        return _CODES[self.code][0]
    def clone(self, **over):
        out = type(self)(self.code, self.message, self.request_id, self.worker_id, self.stage, self.phase, self.retryable, self.recovery_attempted, self.cluster_available, self.tokens_delivered, self.detail)
        out.resume = self.resume
        for k, v in over.items():
            setattr(out, k, v)
        return out
    def to_error(self):
        return {
            "message": self.message,
            "type": "server_error",
            "param": None,
            "code": self.code,
            "request_id": self.request_id,
            "worker_id": self.worker_id,
            "stage": self.stage,
            "phase": self.phase,
            "retryable": self.retryable,
            "recovery_attempted": self.recovery_attempted,
            "cluster_available": self.cluster_available,
            "tokens_delivered": self.tokens_delivered,
        }
class RequestCancelled(InferenceFailure):
    def __init__(self, request_id=None, message="Request cancelled by client"):
        super().__init__("REQUEST_CANCELLED", message, request_id=request_id)
class CancelEvent(threading.Event):
    def __init__(self, request_id=None, deadline_s=None):
        super().__init__()
        self.request_id = request_id
        self.deadline_s = deadline_s
_SENSITIVE = ("prompt", "messages", "content", "token", "worker_token", "api_key", "authorization", "key", "secret")
class Metrics:
    def __init__(self):
