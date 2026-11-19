import asyncio
import threading
import time
import uuid
from collections import deque
from . import resilience as _res
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
        self._queued = {}
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
        self._failures_by_code = {}
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
        wait_limit = _res.resolve_deadline(timeout_s or self.default_timeout_s, _res.POLICY.request_deadline_s) or float(self.default_timeout_s)
        cancel_ev = _res.CancelEvent(request_id=req_id, deadline_s=wait_limit)
        deadline = time.monotonic() + wait_limit
        with self._cond:
            self._total_requests += 1
            if len(self._active) >= self.max_concurrent:
                if len(self._waiting) >= self.max_queue_size:
                    self._failed_requests += 1
                    self._recent_errors.append({"ts": time.time(), "request_id": req_id, "error": "Request queue full"})
                    raise QueueFullError("Cluster inference queue is full")
                self._waiting.append(req_id)
                self._queued[req_id] = cancel_ev
                try:
                    while (len(self._active) >= self.max_concurrent or self._waiting[0] != req_id) and not cancel_ev.is_set():
                        rem = deadline - time.monotonic()
                        if rem <= 0:
                            raise TimeoutError("Timed out waiting in inference queue")
                        self._cond.wait(timeout=min(0.5, rem))
                    if cancel_ev.is_set():
                        raise RuntimeError("Request cancelled while queued")
                except Exception as e:
                    self._queued.pop(req_id, None)
                    if req_id in self._waiting:
                        self._waiting.remove(req_id)
                    self._failed_requests += 1
                    self._recent_errors.append({"ts": time.time(), "request_id": req_id, "error": str(e)[:160]})
                    self._cond.notify_all()
                    raise
                self._queued.pop(req_id, None)
                if req_id in self._waiting:
                    self._waiting.remove(req_id)
            self._active[req_id] = {
                "request_id": req_id,
                "client_key": str(client_key or "anonymous"),
                "model_id": model_id,
                "started_at": time.time(),
                "tokens": 0,
                "cancel_event": cancel_ev,
            }
            self._cond.notify_all()
        return req_id, cancel_ev
    def record_token(self, req_id, count=1):
        with self._lock:
            self._total_tokens += int(count)
            rec = self._active.get(req_id)
            if rec is not None:
                rec["tokens"] += int(count)
    def finish_slot(self, req_id, ok=True, cancelled=False, ttft_ms=None, tps=None, transfer_ms=None, error=None, error_code=None):
        with self._cond:
            was_active = self._active.pop(req_id, None) is not None
            if not was_active:
                self._cond.notify_all()
                return
            if cancelled:
                self._cancelled_requests += 1
            elif ok:
                self._completed_requests += 1
            else:
                self._failed_requests += 1
            if error:
                self._recent_errors.append({"ts": time.time(), "request_id": req_id, "error": str(error)[:160], "code": error_code})
            if error_code:
                self._failures_by_code[error_code] = self._failures_by_code.get(error_code, 0) + 1
            if ttft_ms is not None and ttft_ms >= 0:
                self._ttft_samples.append(float(ttft_ms))
            if tps is not None and tps >= 0:
                self._tps_samples.append(float(tps))
            if transfer_ms is not None and transfer_ms >= 0:
                self._xfer_samples.append(float(transfer_ms))
            self._cond.notify_all()
    def cancel_request(self, req_id):
        with self._cond:
            rec = self._active.get(req_id)
            if rec is not None:
                rec["cancel_event"].set()
                self._cond.notify_all()
                return True
            qev = self._queued.get(req_id)
            if qev is not None:
                qev.set()
                self._cond.notify_all()
                return True
            return False
    def cancel_all(self):
        with self._cond:
            for rec in self._active.values():
                rec["cancel_event"].set()
            for qev in self._queued.values():
                qev.set()
            self._cond.notify_all()
    async def stream_async(self, stream_factory, client_key="anonymous", model_id=None, timeout_s=None, is_disconnected=None, rpm_override=None):
        loop = asyncio.get_running_loop()
        req_id, cancel_ev = await loop.run_in_executor(
            None,
            lambda: self.acquire_slot(client_key=client_key, model_id=model_id, timeout_s=timeout_s, rpm_override=rpm_override),
        )
        q = asyncio.Queue(maxsize=256)
        eff_timeout = _res.resolve_deadline(timeout_s or self.default_timeout_s, _res.POLICY.request_deadline_s) or float(self.default_timeout_s)
        t_dispatch = time.perf_counter()
        state = {"ttft_ms": None, "tps": None, "xfer_ms": None, "ok": True, "err": None, "cancelled": False, "code": None, "detail": None}
        consumer_done = threading.Event()
        def _deadline_failure():
            return _res.InferenceFailure("REQUEST_DEADLINE_EXCEEDED", "Inference timeout exceeded", request_id=req_id, retryable=True)
        def _put_queue(entry, limit_s=2.0):
            end = time.monotonic() + limit_s
            while not consumer_done.is_set() and (not cancel_ev.is_set() or entry[0] in ("error", "end")):
                if loop.is_closed():
                    return False
                coro = q.put(entry)
                try:
                    fut = asyncio.run_coroutine_threadsafe(coro, loop)
                except Exception:
                    coro.close()
                    return False
                try:
                    fut.result(timeout=0.1)
                    return True
                except Exception as exc:
                    if isinstance(exc, TimeoutError) or "TimeoutError" in type(exc).__name__:
                        fut.cancel()
                        if time.monotonic() < end and not consumer_done.is_set():
                            continue
                    return False
            return False
        def _worker():
            gen = None
            try:
                gen = stream_factory(cancel_ev)
                for item in gen:
                    if cancel_ev.is_set():
                        state["cancelled"] = True
                        break
                    if (time.perf_counter() - t_dispatch) > eff_timeout:
                        raise _deadline_failure()
                    itype = item.get("type")
                    if itype in ("content", "thought") and item.get("token"):
                        if state["ttft_ms"] is None:
                            state["ttft_ms"] = round((time.perf_counter() - t_dispatch) * 1000.0, 2)
                        self.record_token(req_id, 1)
                    elif itype == "done":
                        tel = item.get("telemetry") or {}
                        if tel.get("ttft_ms") is not None:
                            state["ttft_ms"] = float(tel["ttft_ms"])
                        if tel.get("tokens_per_sec") is not None:
                            state["tps"] = float(tel["tokens_per_sec"])
                        if tel.get("inter_worker_transfer_ms") is not None:
                            state["xfer_ms"] = float(tel["inter_worker_transfer_ms"])
                    if not _put_queue(("item", item), limit_s=5.0):
                        break
            except _res.RequestCancelled:
                if state["code"] is None:
                    state["cancelled"] = True
                    _res.METRICS.inc("generations_cancelled_total")
            except Exception as exc:
                failure = exc if isinstance(exc, _res.InferenceFailure) else _res.InferenceFailure("INFERENCE_ERROR", str(exc), request_id=req_id)
                if failure.request_id is None:
                    failure.request_id = req_id
                state["ok"] = False
                state["err"] = failure.message
                state["code"] = failure.code
                state["detail"] = failure.to_error()
                _res.METRICS.inc("requests_failed_total")
                _res.METRICS.inc("requests_failed_" + failure.code.lower())
                _res.METRICS.event("request_failed", level=30, request_id=req_id, code=failure.code, worker_id=failure.worker_id, stage=failure.stage, phase=failure.phase, retryable=failure.retryable, recovery_attempted=failure.recovery_attempted, tokens_delivered=failure.tokens_delivered)
                _put_queue(("error", failure), limit_s=2.0)
            finally:
                if gen is not None and hasattr(gen, "close"):
                    try:
                        gen.close()
                    except Exception:
                        pass
                _put_queue(("end", None), limit_s=2.0)
        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        try:
            while True:
                if is_disconnected is not None:
                    try:
                        if await is_disconnected():
                            cancel_ev.set()
                            state["cancelled"] = True
                            break
                    except Exception:
                        pass
                try:
                    kind, payload = await asyncio.wait_for(q.get(), timeout=0.4)
                except asyncio.TimeoutError:
                    if (time.perf_counter() - t_dispatch) > eff_timeout:
                        cancel_ev.set()
                        failure = _deadline_failure()
                        state["ok"] = False
                        state["err"] = failure.message
                        state["code"] = failure.code
                        state["detail"] = failure.to_error()
                        _res.METRICS.inc("request_timeouts_total")
                        _res.METRICS.inc("requests_failed_total")
                        _res.METRICS.event("request_failed", level=30, request_id=req_id, code=failure.code, phase="stream", retryable=True)
                        yield {"type": "error", "error": failure.message, "error_detail": state["detail"]}
                        break
                    continue
                if kind == "item":
                    yield payload
                elif kind == "error":
                    yield {"type": "error", "error": payload.message, "error_detail": payload.to_error()}
                    break
                elif kind == "end":
                    break
        finally:
            consumer_done.set()
            cancel_ev.set()
            self.finish_slot(
                req_id,
                ok=state["ok"],
                cancelled=state["cancelled"],
                ttft_ms=state["ttft_ms"],
                tps=state["tps"],
                transfer_ms=state["xfer_ms"],
                error=state["err"],
                error_code=state["code"],
            )
    async def run_sync_async(self, stream_factory, client_key="anonymous", model_id=None, timeout_s=None, is_disconnected=None, rpm_override=None):
        thought_chunks = []
        content_chunks = []
        usage = {}
        telemetry = {}
        err_item = None
        async for item in self.stream_async(
            stream_factory,
            client_key=client_key,
            model_id=model_id,
            timeout_s=timeout_s,
            is_disconnected=is_disconnected,
            rpm_override=rpm_override,
        ):
            itype = item.get("type")
            if itype == "thought":
                thought_chunks.append(item.get("token") or "")
            elif itype == "content":
                content_chunks.append(item.get("token") or "")
            elif itype == "done":
                usage = item.get("usage", {})
                telemetry = item.get("telemetry", {})
            elif itype == "error":
                err_item = item
        if err_item is not None:
            detail = dict(err_item.get("error_detail") or {})
            code = detail.pop("code", None) or "INFERENCE_ERROR"
            failure = _res.InferenceFailure(code, err_item.get("error") or "Inference error", request_id=detail.get("request_id"), worker_id=detail.get("worker_id"), stage=detail.get("stage"), phase=detail.get("phase"), retryable=detail.get("retryable"), recovery_attempted=detail.get("recovery_attempted", False), cluster_available=detail.get("cluster_available"), tokens_delivered=detail.get("tokens_delivered", 0))
            failure.detail["partial_content_chars"] = sum(len(c) for c in content_chunks)
            raise failure
        return {
            "content": "".join(content_chunks),
            "reasoning_content": "".join(thought_chunks),
            "usage": usage,
            "telemetry": telemetry,
        }
    def telemetry(self):
        with self._lock:
            avg_ttft = round(sum(self._ttft_samples) / len(self._ttft_samples), 2) if self._ttft_samples else 0.0
            avg_tps = round(sum(self._tps_samples) / len(self._tps_samples), 2) if self._tps_samples else 0.0
            avg_xfer = round(sum(self._xfer_samples) / len(self._xfer_samples), 2) if self._xfer_samples else 0.0
            active_list = [
                {
                    "request_id": r["request_id"],
                    "model_id": r["model_id"],
                    "tokens": r["tokens"],
                    "elapsed_s": round(time.time() - r["started_at"], 2),
                }
                for r in self._active.values()
            ]
            return {
                "active_requests": len(self._active),
                "queue_length": len(self._waiting),
                "max_concurrent": self.max_concurrent,
                "max_queue_size": self.max_queue_size,
                "total_requests": self._total_requests,
                "completed_requests": self._completed_requests,
                "failed_requests": self._failed_requests,
                "cancelled_requests": self._cancelled_requests,
                "total_tokens_generated": self._total_tokens,
                "avg_ttft_ms": avg_ttft,
                "last_ttft_ms": round(self._ttft_samples[-1], 2) if self._ttft_samples else 0.0,
                "avg_tokens_per_sec": avg_tps,
                "last_tokens_per_sec": round(self._tps_samples[-1], 2) if self._tps_samples else 0.0,
                "avg_inter_worker_transfer_ms": avg_xfer,
                "last_inter_worker_transfer_ms": round(self._xfer_samples[-1], 2) if self._xfer_samples else 0.0,
                "active_list": active_list,
                "recent_errors": list(self._recent_errors),
                "failures_by_code": dict(self._failures_by_code),
            }
SCHEDULER = RequestScheduler()
