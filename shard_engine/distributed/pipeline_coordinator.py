import json
import os
import socket
import threading
import time
import uuid
try:
    import torch
    from transformers import AutoConfig, AutoTokenizer
    _TORCH_READY = True
except Exception:
    torch = None
    AutoConfig = None
    AutoTokenizer = None
    _TORCH_READY = False
from . import pipeline_proto as proto
from . import pipeline_worker as pw
from . import resilience as _res
def _parse_endpoint(ep):
    s = str(ep or "").strip()
    if ":" not in s:
        raise ValueError(f"Invalid endpoint: {ep}")
    host, port_s = s.rsplit(":", 1)
    return host.strip("[]"), int(port_s)
class _StepTimeout(Exception):
    pass
class _RemoteStageClient:
    def __init__(self, stage_info, auth_token="", cluster_id="", timeout=60.0, policy=None):
        self.stage_info = dict(stage_info)
        self.auth_token = str(auth_token or "")
        self.cluster_id = str(cluster_id or "")
        self.timeout = float(timeout)
        self.policy = policy or _res.POLICY
        self.worker_id = self.stage_info.get("worker_id") or self.stage_info.get("node_id")
        self.stage_idx = self.stage_info.get("stage_idx")
        self.sock = None
        self.lock = threading.Lock()
        self.on_failure = None
        self.on_slow_step = None
        self._closed_by_us = False
        self._seq = 0
    def connect(self):
        with self.lock:
            if self.sock is not None:
                return
            host, port = _parse_endpoint(self.stage_info["endpoint"])
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.policy.connect_timeout_s)
            try:
                s.connect((host, port))
                proto.client_auth_handshake(s, self.auth_token, self.cluster_id)
            except Exception:
                try:
                    s.close()
                except Exception:
                    pass
                raise
            self.sock = s
    def _drop_sock(self):
        s = self.sock
        self.sock = None
        if s is None:
            return
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            s.close()
        except OSError:
            pass
    def abort(self):
        self._closed_by_us = True
        self._drop_sock()
    def close(self):
        self._closed_by_us = True
        self._drop_sock()
    def _notify_failure(self, failure):
        cb = self.on_failure
        if cb is not None:
            try:
                cb(self.worker_id, failure)
            except Exception:
                pass
    def _fail(self, code, message, ctx, phase, t0, cause=None):
        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        if ctx is not None and ctx.cancelled():
            return _res.RequestCancelled(ctx.request_id)
        err = ctx.abort_error if ctx is not None else None
        if err is not None:
            return err.clone(request_id=ctx.request_id, tokens_delivered=ctx.tokens_delivered)
        if self._closed_by_us:
            return _res.InferenceFailure("CLUSTER_UNAVAILABLE", "Pipeline was shut down while the request was in flight", request_id=ctx.request_id if ctx is not None else None, phase=phase, tokens_delivered=ctx.tokens_delivered if ctx is not None else 0, cluster_available=False)
        out = _res.InferenceFailure(code, message, request_id=ctx.request_id if ctx is not None else None, worker_id=self.worker_id, stage=self.stage_idx, phase=phase, tokens_delivered=ctx.tokens_delivered if ctx is not None else 0, detail={"elapsed_ms": elapsed_ms, "cause": str(cause)[:160] if cause else None})
        _res.METRICS.observe("failure_detection_ms", elapsed_ms)
        _res.METRICS.inc("worker_data_plane_failures_total")
        if phase in ("prefill", "decode", "load"):
            self._notify_failure(out)
        return out
    def _roundtrip(self, send_fn, ctx, timeout_s, phase):
        if ctx is not None:
            ctx.poll()
        t_conn = time.monotonic()
        try:
            self.connect()
        except PermissionError:
            raise _res.InferenceFailure("STAGE_ERROR", f"Worker {self.worker_id} rejected authentication", request_id=ctx.request_id if ctx is not None else None, worker_id=self.worker_id, stage=self.stage_idx, phase=phase, retryable=False)
        except (OSError, EOFError, proto.ProtocolError) as exc:
            raise self._fail("WORKER_LOST", f"Cannot connect to worker {self.worker_id}", ctx, phase, t_conn, exc)
        if ctx is not None:
            ctx.poll()
        with self.lock:
            sock = self.sock
            t0 = time.monotonic()
            if sock is None:
                raise self._fail("WORKER_LOST", f"Connection to worker {self.worker_id} is closed", ctx, phase, t0)
            deadline = t0 + float(timeout_s)
            def poll():
                if ctx is not None:
                    ctx.poll()
                if time.monotonic() > deadline:
                    raise _StepTimeout()
            try:
                sock.settimeout(max(self.policy.poll_slice_s, float(timeout_s)))
                send_fn(sock)
                sock.settimeout(self.policy.poll_slice_s)
                return proto.recv_frame_poll(sock, poll)
            except _res.InferenceFailure:
                self._drop_sock()
                raise
            except (_StepTimeout, socket.timeout) as exc:
                self._drop_sock()
                raise self._fail("WORKER_TIMEOUT", f"Worker {self.worker_id} did not answer within {float(timeout_s):.1f}s during {phase}", ctx, phase, t0, exc)
            except (ConnectionError, OSError, EOFError, proto.ProtocolError) as exc:
                self._drop_sock()
                raise self._fail("WORKER_LOST", f"Lost connection to worker {self.worker_id} during {phase}", ctx, phase, t0, exc)
    def load_stage(self, model_path_or_repo, config_dict=None, dtype="float32"):
        payload = {
            "model_path_or_repo": model_path_or_repo,
            "layer_start": int(self.stage_info["layer_start"]),
            "layer_end": int(self.stage_info["layer_end"]),
            "has_embed": bool(self.stage_info["has_embed"]),
            "has_lm_head": bool(self.stage_info["has_lm_head"]),
            "shard_files": list(self.stage_info.get("shard_files") or []),
            "dtype": dtype,
            "config_dict": config_dict,
        }
        mtype, _, raw = self._roundtrip(lambda s: proto.send_json_frame(s, proto.MSG_LOAD_STAGE, payload), None, self.policy.load_timeout_s, "load")
        obj = json.loads(raw.decode("utf-8")) if raw else {}
        if mtype == proto.MSG_ERROR:
            self._drop_sock()
            raise RuntimeError(obj.get("error") or "Worker failed to load stage")
        if mtype != proto.MSG_STAGE_READY:
            self._drop_sock()
            raise RuntimeError(f"Unexpected worker response type {mtype}")
        return obj
    def forward(self, request_id, tensor, use_cache=True, return_logits=True, ctx=None, phase="decode"):
        self._seq += 1
        seq = self._seq
        meta = {
            "request_id": str(request_id),
            "use_cache": bool(use_cache),
            "return_logits": bool(return_logits),
            "seq": seq,
        }
        if ctx is not None:
            meta["execution_id"] = ctx.execution_id
        frame_bytes = proto.encode_tensor_payload(meta, tensor)
        timeout_s = self.policy.prefill_timeout_s if phase == "prefill" else self.policy.decode_step_timeout_s
        t0 = time.perf_counter()
        mtype, _, resp_payload = self._roundtrip(lambda s: proto.send_frame(s, proto.MSG_FORWARD, frame_bytes), ctx, timeout_s, phase)
        total_ms = (time.perf_counter() - t0) * 1000.0
        if mtype == proto.MSG_ERROR:
            self._drop_sock()
            try:
                msg = json.loads(resp_payload.decode("utf-8")).get("error")
            except Exception:
                msg = None
            raise _res.InferenceFailure("STAGE_ERROR", msg or "Worker forward error", request_id=ctx.request_id if ctx is not None else None, worker_id=self.worker_id, stage=self.stage_idx, phase=phase, tokens_delivered=ctx.tokens_delivered if ctx is not None else 0)
        if mtype != proto.MSG_FORWARD_RES:
            self._drop_sock()
            raise _res.InferenceFailure("STALE_RESPONSE", f"Unexpected forward response type {mtype}", request_id=str(request_id), worker_id=self.worker_id, stage=self.stage_idx, phase=phase)
        out_meta, out_tensor = proto.decode_tensor_payload(resp_payload)
        bad_req = out_meta.get("request_id") is not None and out_meta.get("request_id") != str(request_id)
        bad_seq = out_meta.get("seq") is not None and out_meta.get("seq") != seq
        bad_exe = ctx is not None and out_meta.get("execution_id") is not None and out_meta.get("execution_id") != ctx.execution_id
        if bad_req or bad_seq or bad_exe:
            self._drop_sock()
            _res.METRICS.inc("stale_responses_rejected_total")
            _res.METRICS.event("stale_response_rejected", level=30, request_id=ctx.request_id if ctx is not None else str(request_id), worker_id=self.worker_id, stage=self.stage_idx)
            raise _res.InferenceFailure("STALE_RESPONSE", "Worker response does not match the in-flight request", request_id=ctx.request_id if ctx is not None else str(request_id), worker_id=self.worker_id, stage=self.stage_idx, phase=phase)
        if total_ms > float(self.policy.slow_step_warn_s) * 1000.0:
            _res.METRICS.inc("slow_steps_total")
            _res.METRICS.event("slow_worker_step", level=30, request_id=ctx.request_id if ctx is not None else str(request_id), worker_id=self.worker_id, stage=self.stage_idx, phase=phase, duration_ms=round(total_ms, 1))
            cb_slow = self.on_slow_step
            if cb_slow is not None:
                try:
                    cb_slow(self.worker_id, phase, round(total_ms, 1))
                except Exception:
                    pass
        elif self.on_slow_step is not None:
            try:
                self.on_slow_step(self.worker_id, phase, round(total_ms, 1))
            except Exception:
                pass
        compute_ms = float(out_meta.get("stage_compute_ms") or 0.0)
        transfer_ms = max(0.0, total_ms - compute_ms)
        return out_tensor, compute_ms, transfer_ms, len(frame_bytes) + len(resp_payload)
    def release_kv(self, request_id):
        if self.sock is None:
            return
        try:
            self._roundtrip(lambda s: proto.send_json_frame(s, proto.MSG_RELEASE_KV, {"request_id": str(request_id)}), None, self.policy.release_timeout_s, "release")
        except Exception:
            pass
    def ping(self):
        t0 = time.perf_counter()
        mtype, _, raw = self._roundtrip(lambda s: proto.send_json_frame(s, proto.MSG_PING, {}), None, self.policy.connect_timeout_s, "ping")
        rtt = round((time.perf_counter() - t0) * 1000.0, 2)
        return {"rtt_ms": rtt, "stats": json.loads(raw.decode("utf-8")) if raw else {}}
class _LocalStageRunner:
    def __init__(self, stage_info, model_path_or_repo, config, dtype="float32"):
        self.stage_info = dict(stage_info)
        self.worker_id = self.stage_info.get("worker_id") or self.stage_info.get("node_id") or "coordinator"
        self.model_path_or_repo = model_path_or_repo
        self.config = config
        self.dtype = torch.float16 if (dtype == "float16" and torch.cuda.is_available()) else torch.float32
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.stage = None
        self.caches = {}
        self.lock = threading.Lock()
    def load(self):
        base_dir, weight_files = pw._download_or_locate_files(self.model_path_or_repo, self.stage_info.get("shard_files") or [])
        cfg = AutoConfig.from_pretrained(base_dir) if os.path.isfile(os.path.join(base_dir, "config.json")) else self.config
        mtype = str(getattr(cfg, "model_type", "") or "").lower()
        st = pw.PipelineStageModule(
            cfg,
            mtype,
            int(self.stage_info["layer_start"]),
            int(self.stage_info["layer_end"]),
            bool(self.stage_info["has_embed"]),
            bool(self.stage_info["has_lm_head"]),
            self.dtype,
            self.device,
        )
        n_loaded = st.load_stage_weights(weight_files)
        param_bytes = sum(p.numel() * p.element_size() for p in st.parameters())
        with self.lock:
            self.stage = st
        return {
            "status": "ready",
            "layer_start": self.stage_info["layer_start"],
            "layer_end": self.stage_info["layer_end"],
            "tensors_loaded": n_loaded,
            "param_mb": round(param_bytes / (1024 * 1024), 2),
            "device": str(self.device),
        }
    def forward(self, request_id, tensor, use_cache=True, return_logits=True, ctx=None, phase="decode"):
        with self.lock:
            if self.stage is None:
                raise RuntimeError("Local stage not loaded")
            cache = None
            if use_cache:
                cache = self.caches.get(request_id)
                if cache is None:
                    cache = self.stage.new_cache()
                    self.caches[request_id] = cache
            t0 = time.perf_counter()
            with torch.inference_mode():
                out = self.stage.forward_stage(tensor, cache=cache, use_cache=use_cache, return_logits=return_logits)
            compute_ms = (time.perf_counter() - t0) * 1000.0
        return out, compute_ms, 0.0, 0
    def release_kv(self, request_id):
        with self.lock:
            self.caches.pop(request_id, None)
    def abort(self):
        return None
    def close(self):
        with self.lock:
            self.caches.clear()
            self.stage = None
class PipelineCoordinator:
    def __init__(self, model_path_or_repo, plan, worker_tokens=None, cluster_id="", dtype="float32", policy=None):
        self.model_path_or_repo = str(model_path_or_repo)
        self.plan = dict(plan)
        self.worker_tokens = dict(worker_tokens or {})
        self.cluster_id = str(cluster_id or "")
        self.dtype = dtype
        self.policy = policy or _res.POLICY
        self.tokenizer = None
        self.config = None
        self.runners = []
        self.stage_reports = []
        self.on_worker_failure = None
        self.on_slow_step = None
        self._stopped = False
        self._lock = threading.RLock()
        self._gen_lock = threading.Lock()
        self.last_metrics = {}
    def _relay_failure(self, worker_id, failure):
        if self._stopped:
            return
        cb = self.on_worker_failure
        if cb is not None:
            cb(worker_id, failure)
    def _relay_slow(self, worker_id, phase, duration_ms):
        if self._stopped:
            return
        cb = self.on_slow_step
        if cb is not None:
            cb(worker_id, phase, duration_ms)
    def worker_ids(self):
        with self._lock:
            return [getattr(r, "worker_id", None) for r in self.runners if getattr(r, "worker_id", None)]
    def start(self):
        if not _TORCH_READY:
            raise RuntimeError("PyTorch and transformers are required for PipelineCoordinator")
        stages = list(self.plan.get("stages") or [])
        if not stages:
            raise RuntimeError("Partition plan contains no stages")
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_path_or_repo, trust_remote_code=False)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token
        self.config = AutoConfig.from_pretrained(self.model_path_or_repo, trust_remote_code=False)
        cfg_dict = self.config.to_dict() if hasattr(self.config, "to_dict") else None
        runners = []
        for s in stages:
            if s.get("endpoint"):
                tok = self.worker_tokens.get(s.get("node_id") or s.get("worker_id") or "") or self.worker_tokens.get("*") or ""
                rc = _RemoteStageClient(s, auth_token=tok, cluster_id=self.cluster_id, policy=self.policy)
                rc.on_failure = self._relay_failure
                rc.on_slow_step = self._relay_slow
                runners.append(rc)
            else:
                runners.append(_LocalStageRunner(s, self.model_path_or_repo, self.config, dtype=self.dtype))
        reports = [None] * len(runners)
        errors = []
        def _load_idx(idx, runner):
            try:
                if isinstance(runner, _RemoteStageClient):
                    reports[idx] = runner.load_stage(self.model_path_or_repo, config_dict=cfg_dict, dtype=self.dtype)
                else:
                    reports[idx] = runner.load()
            except Exception as e:
                errors.append(f"Stage {idx}: {e}")
        threads = [threading.Thread(target=_load_idx, args=(i, r), daemon=True) for i, r in enumerate(runners)]
        for t in threads:
            t.start()
        load_deadline = time.monotonic() + float(self.policy.load_timeout_s) + 10.0
        for i, t in enumerate(threads):
            t.join(timeout=max(0.1, load_deadline - time.monotonic()))
            if t.is_alive():
                errors.append(f"Stage {i}: load did not finish within {self.policy.load_timeout_s:.0f}s")
        for i, rep in enumerate(reports):
            if rep is None and not any(e.startswith(f"Stage {i}:") for e in errors):
                errors.append(f"Stage {i}: load produced no report")
        if errors:
            for r in runners:
                try:
                    r.abort()
                except Exception:
                    pass
                try:
                    r.close()
                except Exception:
                    pass
            raise RuntimeError("; ".join(errors))
        with self._lock:
            self.runners = runners
            self.stage_reports = reports
        return {"status": "ready", "stages": reports}
    def stop(self):
        self._stopped = True
        with self._lock:
            runners = list(self.runners)
            self.runners = []
            self.stage_reports = []
        for r in runners:
            try:
                r.abort()
            except Exception:
                pass
        for r in runners:
            try:
                r.close()
            except Exception:
                pass
    def is_running(self):
        with self._lock:
            return len(self.runners) > 0
    def forward_logits(self, input_ids, request_id=None, use_cache=True, ctx=None, phase="decode"):
        rid = request_id or f"req-{uuid.uuid4().hex[:10]}"
        with self._lock:
            runners = list(self.runners)
        if not runners:
            raise _res.InferenceFailure("CLUSTER_UNAVAILABLE", "PipelineCoordinator is not loaded", request_id=ctx.request_id if ctx is not None else None, cluster_available=False)
        cur = input_ids
        total_compute_ms = 0.0
        total_transfer_ms = 0.0
        total_bytes = 0
        for r in runners:
            if ctx is not None:
                ctx.poll()
            cur, c_ms, t_ms, n_bytes = r.forward(rid, cur, use_cache=use_cache, return_logits=True, ctx=ctx, phase=phase)
            total_compute_ms += c_ms
            total_transfer_ms += t_ms
            total_bytes += n_bytes
        return cur, {
            "compute_ms": round(total_compute_ms, 2),
            "transfer_ms": round(total_transfer_ms, 2),
            "transferred_bytes": total_bytes,
        }
    def release_request(self, request_id):
        with self._lock:
            runners = list(self.runners)
        for r in runners:
            r.release_kv(request_id)
    def _build_prompt(self, messages):
        if self.tokenizer is not None and hasattr(self.tokenizer, "apply_chat_template") and getattr(self.tokenizer, "chat_template", None):
            try:
                return self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            except Exception:
                pass
        parts = []
        for m in messages:
            role = m.get("role", "user")
            c = m.get("content", "")
            parts.append(f"<|im_start|>{role}\n{c}<|im_end|>")
        parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)
    def _sample_next(self, logits_1d, temperature=0.7, top_p=0.95):
        if temperature is None or float(temperature) <= 0.05:
            return int(torch.argmax(logits_1d, dim=-1).item())
        scaled = logits_1d / max(0.05, float(temperature))
        probs = torch.softmax(scaled, dim=-1)
        if 0.0 < float(top_p) < 1.0:
            sorted_probs, sorted_idx = torch.sort(probs, descending=True)
            cum = torch.cumsum(sorted_probs, dim=-1)
            mask = (cum - sorted_probs) > float(top_p)
            sorted_probs[mask] = 0.0
            s_sum = sorted_probs.sum()
            if s_sum > 0:
                sorted_probs = sorted_probs / s_sum
                choice = torch.multinomial(sorted_probs, num_samples=1)
                return int(sorted_idx[choice].item())
        return int(torch.multinomial(probs, num_samples=1).item())
    def generate_stream(self, messages, max_tokens=256, temperature=0.7, cancel_check=None):
        if not self.is_running():
            raise RuntimeError("PipelineCoordinator is not running")
        t_start = time.perf_counter()
        prompt = self._build_prompt(messages)
        enc = self.tokenizer(prompt, return_tensors="pt")
        input_ids = enc["input_ids"]
        if input_ids.shape[-1] == 0:
            plain = " ".join(str(m.get("content", "")) for m in messages).strip()
            if plain:
                input_ids = self.tokenizer(plain, return_tensors="pt")["input_ids"]
            if input_ids.shape[-1] == 0:
                fallback_id = self.tokenizer.bos_token_id or self.tokenizer.eos_token_id or 0
                input_ids = torch.tensor([[int(fallback_id)]], dtype=torch.long)
        prompt_tokens = int(input_ids.shape[-1])
        rid = f"pipe-{uuid.uuid4().hex[:10]}"
        eos_id = self.tokenizer.eos_token_id
        generated_ids = []
        prev_text = ""
        ttft_ms = None
        total_compute_ms = 0.0
        total_transfer_ms = 0.0
        total_bytes = 0
        max_new = max(1, min(int(max_tokens), 2048))
        with self._gen_lock:
            try:
                cur_ids = input_ids
                for step in range(max_new):
                    if cancel_check and cancel_check():
                        break
                    logits, hop_stats = self.forward_logits(cur_ids, request_id=rid, use_cache=True)
                    total_compute_ms += hop_stats["compute_ms"]
                    total_transfer_ms += hop_stats["transfer_ms"]
                    total_bytes += hop_stats["transferred_bytes"]
                    next_id = self._sample_next(logits[0, -1, :], temperature=temperature)
                    if ttft_ms is None:
                        ttft_ms = round((time.perf_counter() - t_start) * 1000.0, 2)
                    if eos_id is not None and next_id == eos_id:
                        break
                    generated_ids.append(next_id)
                    full_txt = self.tokenizer.decode(generated_ids, skip_special_tokens=True)
                    delta = full_txt[len(prev_text):]
                    prev_text = full_txt
                    if delta:
                        yield {"type": "content", "token": delta, "done": False}
                    cur_ids = torch.tensor([[next_id]], dtype=torch.long)
            finally:
                self.release_request(rid)
        total_s = max(0.001, time.perf_counter() - t_start)
        completion_tokens = len(generated_ids)
        tps = round(completion_tokens / total_s, 2)
        metrics = {
            "thinking_seconds": 0.0,
            "total_seconds": round(total_s, 3),
            "ttft_ms": ttft_ms or round(total_s * 1000.0, 2),
            "tokens_per_sec": tps,
            "compute_ms": round(total_compute_ms, 2),
            "inter_worker_transfer_ms": round(total_transfer_ms, 2),
            "transferred_bytes": total_bytes,
            "stages": len(self.runners),
        }
        self.last_metrics = metrics
        yield {
            "type": "done",
            "token": "",
            "done": True,
            "telemetry": metrics,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
