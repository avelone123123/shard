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
