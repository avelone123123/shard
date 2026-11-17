import asyncio
import threading
import time
import uuid
from collections import deque
class RateLimitError(RuntimeError):
    def __init__(self, message, retry_after=5):
        super().__init__(message)
        self.retry_after = int(retry_after)
class QueueFullError(RuntimeError):
    pass
class RequestScheduler:
    def __init__(self, max_concurrent=4, max_queue_size=64, default_rpm=120, default_timeout_s=300.0):
        self._lock = threading.RLock()
        self.max_concurrent = max(1, int(max_concurrent))
        self.max_queue_size = max(1, int(max_queue_size))
        self.default_rpm = max(0, int(default_rpm))
        self.default_timeout_s = max(5.0, float(default_timeout_s))
        self._active = {}
        self._waiting = deque()
        self._cond = threading.Condition(self._lock)
        self._rate_windows = {}
        self._total_requests = 0
        self._completed_requests = 0
        self._failed_requests = 0
        self._cancelled_requests = 0
        self._total_tokens = 0
        self._ttft_samples = deque(maxlen=100)
        self._tps_samples = deque(maxlen=100)
        self._xfer_samples = deque(maxlen=100)
        self._recent_errors = deque(maxlen=15)
    def configure(self, max_concurrent=None, max_queue_size=None, default_rpm=None, default_timeout_s=None):
        with self._cond:
            if max_concurrent is not None:
                self.max_concurrent = max(1, int(max_concurrent))
            if max_queue_size is not None:
                self.max_queue_size = max(1, int(max_queue_size))
            if default_rpm is not None:
                self.default_rpm = max(0, int(default_rpm))
            if default_timeout_s is not None:
                self.default_timeout_s = max(5.0, float(default_timeout_s))
            self._cond.notify_all()
    def _check_rate_limit(self, client_key, rpm_override=None):
        limit = self.default_rpm if rpm_override is None else int(rpm_override)
        if limit <= 0:
            return
        now = time.monotonic()
        k = str(client_key or "anonymous")
        with self._lock:
            if k not in self._rate_windows and len(self._rate_windows) >= 4096:
                oldest = next(iter(self._rate_windows))
                self._rate_windows.pop(oldest, None)
            win = self._rate_windows.setdefault(k, deque())
            cutoff = now - 60.0
            while win and win[0] < cutoff:
                win.popleft()
            if len(win) >= limit:
                retry = max(1, int(60.0 - (now - win[0])))
                raise RateLimitError(f"Rate limit exceeded ({limit} req/min)", retry_after=retry)
            win.append(now)
    def acquire_slot(self, client_key="anonymous", model_id=None, timeout_s=None, rpm_override=None):
        self._check_rate_limit(client_key, rpm_override=rpm_override)
        req_id = f"sreq-{uuid.uuid4().hex[:10]}"
        cancel_ev = threading.Event()
        wait_limit = float(timeout_s or self.default_timeout_s)
        deadline = time.monotonic() + wait_limit
        with self._cond:
            self._total_requests += 1
            if len(self._active) >= self.max_concurrent:
                if len(self._waiting) >= self.max_queue_size:
                    self._failed_requests += 1
                    self._recent_errors.append({"ts": time.time(), "request_id": req_id, "error": "Request queue full"})
