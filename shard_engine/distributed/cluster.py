import copy
import os
import secrets
import socket
import threading
import time
import uuid
from .. import gguf_backend as _gguf
from .. import hardware as _hardware
from .. import lan as _lan
from . import capabilities as _cap
from . import pipeline_coordinator as _pcoord
from . import pipeline_worker as _pworker
from . import planner as _planner
from . import resilience as _res
from . import scheduler as _sched
HEARTBEAT_TIMEOUT_S = _res.POLICY.dead_after_s
WORKER_TOKEN_PREFIX = "sk-shard-wrk-"
def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p
class ClusterManager:
    def __init__(self):
        self._lock = threading.RLock()
        self.role = "idle"
        self.state = "IDLE"
        self.cluster_id = None
        self.name = socket.gethostname() or "shard-cluster"
        self.pairing_code = None
        self.open_net = False
        self.access_mode = "invite_only"
        self.target_model = None
        self.strategy = "auto"
        self.ctx_tokens = 4096
        self.parallel_slots = 4
        self.coordinator_contributes = True
        self.coordinator_quota = {}
        self.model_spec = None
        self.plan = None
        self.last_error = None
        self.created_at = None
        self.ready_at = None
        self.workers = {}
        self.blocked_ids = set()
        self._pipeline_coord = None
        self._gguf_backend = None
        self._monitor_stop = threading.Event()
        self._monitor_thread = None
        self._rebalance_needed = threading.Event()
        self._inflight = {}
        self._inflight_lock = threading.Lock()
        self._newly_failed = []
        self._reform_active = False
        self._reform_requested = False
        self._reform_infeasible = False
        self._reform_failures = 0
        self._next_reform_at = 0.0
        self._reform_thread = None
        self._capacity_returned = False
        self._deploy_lock = threading.RLock()
        self._cluster_epoch = 0
        self._starting_backend = None
        self._local_worker = {
            "active": False,
            "coordinator_url": None,
            "worker_id": None,
            "worker_token": None,
            "quota": {},
            "rpc_proc": None,
            "rpc_port": None,
            "pipeline_srv": None,
            "pipeline_port": None,
            "hb_stop": threading.Event(),
            "hb_thread": None,
            "via_tunnel": False,
        }
    def _ensure_monitor(self):
        if self._monitor_thread is None or not self._monitor_thread.is_alive():
            self._monitor_stop.clear()
            self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._monitor_thread.start()
    def _stop_monitor(self):
        self._monitor_stop.set()
        self._rebalance_needed.set()
        th = self._monitor_thread
        self._monitor_thread = None
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2.0)
        rth = self._reform_thread
        self._reform_thread = None
        if rth is not None and rth.is_alive() and rth is not threading.current_thread():
            rth.join(timeout=3.0)
    def _coordinator_node_record(self, caps=None):
        caps = caps or _cap.probe_node_capabilities()
        hw = caps["hw"]
        quota = dict(self.coordinator_quota or {})
        if not self.coordinator_contributes:
            quota = {"max_vram_mb": 0, "max_ram_mb": 0, "max_threads": 1}
        eff = _hardware.effective_usable_resources(hw, quota=quota)
        return {
            "node_id": "coordinator",
            "name": f"{self.name} (Coordinator)",
            "role": "coordinator",
            "hw": hw,
            "backends": caps["backends"],
            "local_offload_devices": caps["local_offload_devices"],
            "quota": quota,
            "effective": eff,
            "rtt_ms": 0.1,
            "bandwidth_mbps": 10000.0,
            "alive": True,
            "consented": self.coordinator_contributes,
        }
    def _set_worker_state_locked(self, w, new_state, reason, now=None):
        now = time.time() if now is None else now
        prev = w.get("state") or _res.HEALTHY
        if prev == new_state:
            return False
        w["state"] = new_state
        w["state_since"] = now
        w["state_reason"] = str(reason)[:160]
        w["alive"] = new_state in (_res.HEALTHY, _res.SUSPECT)
        _res.METRICS.inc("worker_state_transitions_total")
        if new_state == _res.FAILED:
            w["fail_count"] = int(w.get("fail_count") or 0) + 1
            w["failed_at"] = now
            _res.METRICS.inc("worker_failures_total")
        _res.METRICS.event("worker_state", level=30 if new_state in (_res.SUSPECT, _res.FAILED) else 20, worker_id=w.get("worker_id"), prev=prev, state=new_state, reason=reason, fail_count=w.get("fail_count", 0))
        return True
    def _refresh_worker_states_locked(self, now=None):
        now = time.time() if now is None else now
        pol = _res.POLICY
        for wid, w in self.workers.items():
            if w.get("revoked"):
                continue
            age = now - float(w.get("last_heartbeat") or 0)
            st = w.get("state") or _res.HEALTHY
            if st in (_res.HEALTHY, _res.SUSPECT):
                new = _res.classify_heartbeat_age(age, pol)
                if new == st:
                    continue
                self._set_worker_state_locked(w, new, f"heartbeat age {age:.1f}s", now)
                if new == _res.SUSPECT:
                    _res.METRICS.inc("heartbeat_missed_total")
                elif new == _res.FAILED:
                    _res.METRICS.inc("heartbeat_loss_total")
                    _res.METRICS.observe("failure_detection_ms", age * 1000.0)
                    self._newly_failed.append((wid, age))
            elif st == _res.RECOVERING:
                if age > pol.suspect_after_s:
                    self._set_worker_state_locked(w, _res.FAILED, "heartbeat lost during probation", now)
                elif now - float(w.get("recovering_since") or now) >= pol.probation_s(w.get("fail_count") or 1):
                    self._set_worker_state_locked(w, _res.HEALTHY, "probation passed", now)
                    self._capacity_returned = True
                    self._reform_infeasible = False
    def _alive_worker_records(self, now=None):
        now = time.time() if now is None else now
        out = []
        with self._lock:
            self._refresh_worker_states_locked(now)
            for wid, w in self.workers.items():
                if w.get("revoked"):
                    continue
                if w.get("token_expires_at") and now > w["token_expires_at"]:
                    continue
                if (w.get("state") or _res.HEALTHY) == _res.HEALTHY:
                    out.append(copy.deepcopy(w))
        return out
    def _register_execution(self, ctx):
        with self._inflight_lock:
            self._inflight[ctx.request_id] = ctx
    def _unregister_execution(self, ctx):
        with self._inflight_lock:
            self._inflight.pop(ctx.request_id, None)
    def _abort_executions(self, worker_id, failure):
        with self._inflight_lock:
            ctxs = [c for c in self._inflight.values() if worker_id is None or worker_id in c.workers]
        n = 0
        for c in ctxs:
            err = failure.clone(request_id=c.request_id, stage=c.stage_map.get(worker_id), phase=c.phase, tokens_delivered=c.tokens_delivered)
            if c.abort(err):
                n += 1
                _res.METRICS.inc("generations_aborted_total")
                _res.METRICS.event("generation_aborted", level=30, request_id=c.request_id, worker_id=worker_id, code=failure.code, phase=c.phase, tokens_delivered=c.tokens_delivered)
        return n
    def _handle_assigned_worker_lost(self, worker_id, reason, code="WORKER_LOST"):
        with self._lock:
            if self.state == "READY":
                self.state = "DEGRADED"
                self.last_error = f"Assigned worker {worker_id} lost ({reason}); cluster degraded"
            self._reform_requested = True
        failure = _res.InferenceFailure(code, f"Worker {worker_id} was lost during generation: {reason}", worker_id=worker_id, cluster_available=False)
        self._abort_executions(worker_id, failure)
        self._rebalance_needed.set()
    def mark_worker_failed(self, worker_id, reason="unreachable", code="WORKER_LOST"):
        wid = str(worker_id or "")
        assigned = False
        changed = False
        with self._lock:
            w = self.workers.get(wid)
            if w is not None and not w.get("revoked"):
                changed = self._set_worker_state_locked(w, _res.FAILED, reason)
                assigned = bool(w.get("assigned_stage"))
        if assigned:
            self._handle_assigned_worker_lost(wid, reason, code)
        return changed
    def _on_data_plane_failure(self, worker_id, failure):
        if failure.code in ("WORKER_LOST", "WORKER_TIMEOUT", "WORKER_STALLED"):
            self.mark_worker_failed(worker_id, reason=f"data plane {failure.code.lower()}", code=failure.code)
    def _on_slow_step(self, worker_id, phase, duration_ms):
        wid = str(worker_id or "")
        is_slow = float(duration_ms) > float(_res.POLICY.slow_step_warn_s) * 1000.0
        with self._lock:
            w = self.workers.get(wid)
            if w is not None:
                w["last_step_ms"] = float(duration_ms)
                w["slow"] = is_slow
    def _probe_assigned_workers(self):
        targets = []
        with self._lock:
            for wid, w in self.workers.items():
                if w.get("revoked") or not w.get("assigned_stage"):
                    continue
                if (w.get("state") or _res.HEALTHY) in (_res.HEALTHY, _res.SUSPECT):
                    ep = w.get("pipeline_endpoint") or w.get("rpc_endpoint")
                    if ep and ":" in ep:
                        targets.append((wid, ep))
        for wid, ep in targets:
            host, port_s = ep.rsplit(":", 1)
            link = _cap.measure_tcp_link(host, int(port_s), timeout=1.0)
            if not link.get("reachable"):
                self.mark_worker_failed(wid, reason="tcp probe failed after backend failure", code="WORKER_LOST")
    def _maybe_start_reform(self, capacity_returned=False):
        with self._lock:
            if self.role != "coordinator" or self._reform_active:
                return False
            need = self._reform_requested or (capacity_returned and self.state == "DEGRADED")
            if not need or time.time() < self._next_reform_at:
                return False
            self._reform_active = True
            self._reform_requested = False
            th = threading.Thread(target=self._reform_cluster, daemon=True)
            self._reform_thread = th
        th.start()
        return True
    def _reform_cluster(self):
        t0 = time.monotonic()
        with self._lock:
            epoch = self._cluster_epoch
            if self.role != "coordinator":
                self._reform_active = False
                return
        _res.METRICS.inc("reform_attempts_total")
        _res.METRICS.event("reform_started", state=self.state)
        try:
            self._stop_execution_backends()
            with self._lock:
                if self._cluster_epoch != epoch or self.role != "coordinator":
                    return
            plan = self.recompute_plan()
            if not plan.get("feasible"):
                with self._lock:
                    if self._cluster_epoch != epoch or self.role != "coordinator":
                        return
                    self.state = "DEGRADED"
                    self.last_error = plan.get("reason")
                    self._reform_infeasible = True
                _res.METRICS.inc("reform_infeasible_total")
                _res.METRICS.event("reform_infeasible", level=30, reason=str(plan.get("reason")))
                return
            self.deploy_cluster(verify=True)
            with self._lock:
                if self._cluster_epoch != epoch or self.role != "coordinator":
                    return
                self._reform_failures = 0
                self._next_reform_at = 0.0
            _res.METRICS.inc("reform_success_total")
            _res.METRICS.observe("reform_ms", (time.monotonic() - t0) * 1000.0)
            _res.METRICS.event("reform_completed", duration_ms=round((time.monotonic() - t0) * 1000.0, 1))
        except Exception as e:
            with self._lock:
                if self._cluster_epoch != epoch or self.role != "coordinator":
                    return
                self.state = "DEGRADED"
                self.last_error = str(e)[:240]
                self._reform_failures += 1
                self._next_reform_at = time.time() + _res.POLICY.backoff_s(self._reform_failures - 1)
                self._reform_requested = True
            _res.METRICS.inc("reform_failure_total")
            _res.METRICS.event("reform_failed", level=40, error=str(e)[:160], failures=self._reform_failures)
        finally:
            with self._lock:
                self._reform_active = False
            self._rebalance_needed.set()
    def _cluster_available(self):
        try:
            return bool(self.is_ready())
        except Exception:
            return False
    def resilience_status(self):
        now = time.monotonic()
        with self._inflight_lock:
            inflight = [
                {"request_id": c.request_id, "execution_id": c.execution_id, "attempt": c.attempt, "phase": c.phase, "tokens_delivered": c.tokens_delivered, "workers": sorted(c.workers), "age_s": round(now - c.started, 2)}
                for c in self._inflight.values()
            ]
        with self._lock:
            workers = [
                {"worker_id": wid, "state": w.get("state") or _res.HEALTHY, "reason": w.get("state_reason"), "fail_count": int(w.get("fail_count") or 0), "slow": bool(w.get("slow")), "last_step_ms": w.get("last_step_ms")}
                for wid, w in self.workers.items()
            ]
            reform = {"active": self._reform_active, "requested": self._reform_requested, "infeasible": self._reform_infeasible, "failures": self._reform_failures}
        return {"policy": _res.POLICY.as_dict(), "metrics": _res.METRICS.snapshot(), "inflight": inflight, "workers": workers, "reform": reform}
    def create_cluster(
        self,
        name=None,
        model_id=None,
        strategy="auto",
        ctx_tokens=4096,
        parallel_slots=4,
        access_mode="invite_only",
        open_net=False,
        coordinator_contributes=True,
        coordinator_quota=None,
        auto_deploy=True,
        fetch_model_meta=True,
    ):
        target_m = str(model_id or "").strip()
        if not target_m:
            raise ValueError("model_id is required to create a distributed AI cluster")
        self.stop_cluster()
        spec = _planner.inspect_model_architecture(target_m, ctx_tokens=int(ctx_tokens), fetch=bool(fetch_model_meta))
        with self._lock:
            self.role = "coordinator"
            self.state = "FORMING"
            self.cluster_id = f"cls-{uuid.uuid4().hex[:12]}"
            self.name = (str(name or socket.gethostname() or "shard-cluster").strip())[:64]
            self.pairing_code = _lan.gen_pairing_code()
            self.open_net = bool(open_net)
            self.access_mode = "open" if self.open_net else str(access_mode or "invite_only")
            self.target_model = target_m
            self.strategy = str(strategy or "auto")
            self.ctx_tokens = max(256, int(ctx_tokens))
            self.parallel_slots = max(1, min(32, int(parallel_slots)))
            self.coordinator_contributes = bool(coordinator_contributes)
            self.coordinator_quota = dict(coordinator_quota or {})
            self.model_spec = spec
            self.plan = None
            self.last_error = None
            self.created_at = time.time()
            self.ready_at = None
            self.workers.clear()
            _sched.SCHEDULER.configure(max_concurrent=self.parallel_slots)
            self._ensure_monitor()
        plan = self.recompute_plan()
        if auto_deploy and plan.get("feasible"):
            try:
                self.deploy_cluster()
            except Exception as e:
                with self._lock:
                    self.last_error = str(e)[:240]
                    self.state = "FORMING"
        return self.status(include_secrets=True)
    def stop_cluster(self):
        with self._lock:
            self._cluster_epoch += 1
            self.role = "idle"
            self.state = "IDLE"
            self.cluster_id = None
            self.pairing_code = None
            self.plan = None
            self.model_spec = None
            self.last_error = None
            self.workers.clear()
            self._newly_failed.clear()
            self._reform_requested = False
            self._reform_infeasible = False
            self._reform_failures = 0
            self._next_reform_at = 0.0
            self._capacity_returned = False
            starting = self._starting_backend
            self._starting_backend = None
        self._abort_executions(None, _res.InferenceFailure("CLUSTER_UNAVAILABLE", "Cluster was stopped", cluster_available=False, retryable=True))
        if starting is not None:
            try:
                starting.stop()
            except Exception:
                pass
        self._stop_execution_backends()
        self._stop_monitor()
        try:
            from .. import net_strategy
            mgr = getattr(net_strategy, "_DEFAULT_MANAGER", None)
            if mgr is not None:
                mgr.close_worker_proxies()
        except Exception:
            pass
        return {"status": "ok", "state": "IDLE"}
    def _stop_execution_backends(self):
        with self._lock:
            pc = self._pipeline_coord
            gb = self._gguf_backend
            self._pipeline_coord = None
            self._gguf_backend = None
        if pc is not None:
            try:
                pc.stop()
            except Exception:
                pass
        if gb is not None:
            try:
                gb.stop()
            except Exception:
                pass
    def _build_planner_nodes(self):
        coord = self._coordinator_node_record()
        nodes = [coord]
        for w in self._alive_worker_records():
            nodes.append({
                "node_id": w["worker_id"],
                "name": w["name"],
                "role": "worker",
                "hw": w.get("hw") or {},
                "backends": w.get("backends") or {},
                "quota": w.get("quota") or {},
                "rpc_endpoint": w.get("rpc_endpoint"),
                "pipeline_endpoint": w.get("pipeline_endpoint"),
                "rtt_ms": w.get("rtt_ms"),
                "bandwidth_mbps": w.get("bandwidth_mbps"),
            })
        return nodes
    def recompute_plan(self):
        with self._lock:
            if self.role != "coordinator" or not self.model_spec:
                return {"feasible": False, "reason": "No active cluster", "stages": []}
            nodes = self._build_planner_nodes()
            plan = _planner.plan_cluster_partition(
                self.model_spec,
                nodes,
                strategy=self.strategy,
                ctx_tokens=self.ctx_tokens,
                parallel_slots=self.parallel_slots,
            )
            self.plan = plan
            assigned = {s["node_id"]: s for s in (plan.get("stages") or [])}
            for wid, w in self.workers.items():
                st = assigned.get(wid)
                w["assigned_stage"] = st
            return copy.deepcopy(plan)
    def register_worker(
        self,
        worker_id=None,
        name=None,
        hw=None,
        backends=None,
        quota=None,
        consented=False,
        auth_code=None,
        rpc_port=None,
        pipeline_port=None,
        rpc_host=None,
        pipeline_host=None,
        transport="lan",
        tunnel_peer=None,
        remote_ip=None,
        ttl_seconds=604800.0,
    ):
        if not consented:
            raise PermissionError("Explicit user consent is required before contributing worker resources")
        with self._lock:
            if self.role != "coordinator":
                raise RuntimeError("This node is not an active cluster coordinator")
            wid = str(worker_id or tunnel_peer or f"wrk-{uuid.uuid4().hex[:10]}").strip()[:64]
            if wid in self.blocked_ids or (remote_ip and remote_ip in self.blocked_ids) or (tunnel_peer and tunnel_peer in self.blocked_ids):
                raise PermissionError("Worker is blocked from this cluster")
            if not self.open_net:
                norm = _lan.normalize_code(auth_code)
                if not norm or not self.pairing_code or not secrets.compare_digest(norm, self.pairing_code):
                    raise PermissionError("Invalid cluster pairing code")
            now = time.time()
            eff_quota = dict(quota or {})
            eff = _hardware.effective_usable_resources(hw or {}, quota=eff_quota)
            eff_rpc_ep = None
            eff_pipe_ep = None
            rtt_ms = None
            if tunnel_peer:
                try:
                    from .. import net_strategy
                    mgr = net_strategy.get_network_manager()
                    if rpc_port:
                        px = mgr.open_worker_proxy(tunnel_peer, int(rpc_port))
                        if px:
                            eff_rpc_ep = f"127.0.0.1:{px}"
                    if pipeline_port:
                        px_p = mgr.open_worker_proxy(tunnel_peer, int(pipeline_port))
                        if px_p:
                            eff_pipe_ep = f"127.0.0.1:{px_p}"
                except Exception:
                    pass
            else:
                r_host = str(rpc_host or remote_ip or "127.0.0.1").strip()
                p_host = str(pipeline_host or rpc_host or remote_ip or "127.0.0.1").strip()
                if rpc_port:
                    eff_rpc_ep = f"{r_host}:{int(rpc_port)}"
                if pipeline_port:
                    eff_pipe_ep = f"{p_host}:{int(pipeline_port)}"
            probe_target = eff_pipe_ep or eff_rpc_ep
            if probe_target and ":" in probe_target:
                ph, pp = probe_target.rsplit(":", 1)
                link = _cap.measure_tcp_link(ph, int(pp), timeout=1.5)
                if link.get("reachable"):
                    rtt_ms = link.get("rtt_ms")
            prev_rec = self.workers.get(wid)
            was_assigned_live = bool(prev_rec and prev_rec.get("assigned_stage") and self.state in ("READY", "LOADING", "VERIFYING"))
            tok = WORKER_TOKEN_PREFIX + secrets.token_hex(16)
            rec = {
                "worker_id": wid,
                "name": (str(name or wid).strip())[:64],
                "worker_token": tok,
                "token_expires_at": now + max(60.0, float(ttl_seconds)),
                "consented": True,
                "joined_at": now,
                "last_heartbeat": now,
                "alive": True,
                "state": _res.HEALTHY,
                "state_since": now,
                "fail_count": 0,
                "revoked": False,
                "transport": str(tunnel_peer and "tunnel" or transport or "lan"),
                "tunnel_peer": tunnel_peer,
                "remote_ip": remote_ip,
                "hw": dict(hw or {}),
                "backends": dict(backends or (hw or {}).get("backends") or {}),
                "quota": eff_quota,
                "effective": eff,
                "rpc_port": int(rpc_port) if rpc_port else None,
                "pipeline_port": int(pipeline_port) if pipeline_port else None,
                "rpc_endpoint": eff_rpc_ep,
                "pipeline_endpoint": eff_pipe_ep,
                "rtt_ms": rtt_ms,
                "bandwidth_mbps": None,
                "workload": {"active_tasks": 0, "utilization_pct": 0.0},
                "assigned_stage": None,
            }
            self.workers[wid] = rec
            self._reform_infeasible = False
            self._capacity_returned = True
            self._rebalance_needed.set()
        if was_assigned_live:
            self._handle_assigned_worker_lost(wid, "worker re-registered", "WORKER_LOST")
        self.recompute_plan()
        return {"status": "ok", "worker_id": wid, "worker_token": tok, "cluster_id": self.cluster_id, "plan": self.plan}
    def heartbeat_worker(self, worker_id, worker_token, hw=None, quota=None, workload=None, rtt_ms=None, bandwidth_mbps=None):
        now = time.time()
        pol = _res.POLICY
        with self._lock:
            w = self.workers.get(str(worker_id or ""))
            if not w or w.get("revoked"):
                return False, "Unknown or revoked worker"
            expected = str(w.get("worker_token") or "")
            if not expected or not secrets.compare_digest(str(worker_token or ""), expected):
                return False, "Invalid worker token"
            if w.get("token_expires_at") and now > w["token_expires_at"]:
                return False, "Worker session expired"
            w["last_heartbeat"] = now
            st = w.get("state") or _res.HEALTHY
            if st == _res.FAILED:
                w["recovering_since"] = now
                self._set_worker_state_locked(w, _res.RECOVERING, "heartbeat resumed after failure", now)
                _res.METRICS.inc("worker_reconnects_total")
                st = _res.RECOVERING
            elif st == _res.SUSPECT:
                self._set_worker_state_locked(w, _res.HEALTHY, "heartbeat resumed", now)
            if st == _res.RECOVERING and now - float(w.get("recovering_since") or now) >= pol.probation_s(w.get("fail_count") or 1):
                self._set_worker_state_locked(w, _res.HEALTHY, "probation passed", now)
                self._capacity_returned = True
                self._reform_infeasible = False
                self._rebalance_needed.set()
            if isinstance(hw, dict) and hw:
                w["hw"] = hw
            if isinstance(quota, dict):
                w["quota"] = quota
            w["effective"] = _hardware.effective_usable_resources(w.get("hw") or {}, quota=w.get("quota") or {})
            if isinstance(workload, dict):
                w["workload"] = workload
            if rtt_ms is not None:
                w["rtt_ms"] = float(rtt_ms)
            if bandwidth_mbps is not None:
                w["bandwidth_mbps"] = float(bandwidth_mbps)
            return True, {"cluster_state": self.state, "assigned_stage": w.get("assigned_stage"), "worker_state": w.get("state"), "heartbeat_interval_s": _res.POLICY.heartbeat_interval_s}
    def leave_worker(self, worker_id, worker_token):
        with self._lock:
            w = self.workers.get(str(worker_id or ""))
            if not w:
                return False
            expected = str(w.get("worker_token") or "")
            if not expected or not secrets.compare_digest(str(worker_token or ""), expected):
                return False
            tpeer = w.get("tunnel_peer")
            was_assigned = bool(w.get("assigned_stage"))
            self.workers.pop(worker_id, None)
        if was_assigned:
            self._handle_assigned_worker_lost(str(worker_id), "worker left the cluster", "WORKER_LOST")
        if tpeer:
            try:
                from .. import net_strategy
                net_strategy.get_network_manager().close_worker_proxies(tpeer)
            except Exception:
                pass
        self.recompute_plan()
        return True
    def kick_worker(self, worker_id, block=False):
        wid = str(worker_id or "").strip()
        with self._lock:
            w = self.workers.pop(wid, None)
            if block:
                self.blocked_ids.add(wid)
                if w and w.get("remote_ip"):
                    self.blocked_ids.add(w["remote_ip"])
                if w and w.get("tunnel_peer"):
                    self.blocked_ids.add(w["tunnel_peer"])
            tpeer = w.get("tunnel_peer") if w else None
            was_assigned = bool(w and w.get("assigned_stage"))
        if was_assigned:
            self._handle_assigned_worker_lost(wid, "worker was removed by the owner", "WORKER_LOST")
        if tpeer:
            try:
                from .. import net_strategy
                net_strategy.get_network_manager().close_worker_proxies(tpeer)
            except Exception:
                pass
        self.recompute_plan()
        return {"status": "ok", "removed": w is not None, "blocked": bool(block)}
    def deploy_cluster(self, verify=True):
        with self._deploy_lock:
            with self._lock:
                my_epoch = self._cluster_epoch
                prev_ready = self.ready_at is not None
                if self.role != "coordinator":
                    raise RuntimeError("Cluster is not active")
            plan = self.recompute_plan()
            if not plan.get("feasible"):
                with self._lock:
                    if self._cluster_epoch == my_epoch:
                        self.last_error = plan.get("reason")
                raise RuntimeError(plan.get("reason") or "Cluster partition plan is not feasible")
            with self._lock:
                if self._cluster_epoch != my_epoch or self.role != "coordinator":
                    raise RuntimeError("Cluster stopped before deployment")
                self.state = "LOADING"
                self.last_error = None
                strategy = plan["strategy"]
                model_id = self.target_model
                spec = dict(self.model_spec or {})
                worker_tokens = {wid: w["worker_token"] for wid, w in self.workers.items()}
                cid = self.cluster_id
            self._stop_execution_backends()
            backend_obj = None
            try:
                if strategy == "torch_pipeline_parallel":
                    model_ref = spec.get("local_hf_dir") or model_id
                    pc = _pcoord.PipelineCoordinator(model_ref, plan, worker_tokens=worker_tokens, cluster_id=cid)
                    pc.on_worker_failure = self._on_data_plane_failure
                    pc.on_slow_step = self._on_slow_step
                    backend_obj = pc
                    with self._lock:
                        if self._cluster_epoch != my_epoch:
                            raise RuntimeError("Cluster stopped during deployment")
                        self._starting_backend = pc
                    pc.start()
                    with self._lock:
                        if self._cluster_epoch != my_epoch:
                            raise RuntimeError("Cluster stopped during deployment")
                    if verify:
                        with self._lock:
                            self.state = "VERIFYING"
                        res = self._verify_pipeline(pc)
                        if not res:
                            raise RuntimeError("Distributed pipeline verification failed")
                    with self._lock:
                        self._starting_backend = None
                        if self._cluster_epoch != my_epoch or self.role != "coordinator":
                            raise RuntimeError("Cluster stopped during deployment")
                        self._pipeline_coord = pc
                        self.state = "READY"
                        self.ready_at = time.time()
                        self._reform_requested = False
                        self._reform_infeasible = False
                        self._reform_failures = 0
                        self._next_reform_at = 0.0
                    return self.status(include_secrets=False)
                gguf_path = spec.get("local_gguf_path")
                if not gguf_path:
                    repo_id = _gguf.find_gguf_repo(model_id)
                    if not repo_id:
                        raise RuntimeError(f"No GGUF repository found for {model_id}")
                    files = _gguf.list_gguf_files(repo_id)
                    sel = _gguf.select_gguf_file(files, wanted_quant=spec.get("quant"))
                    if not sel:
                        raise RuntimeError(f"No matching GGUF file in {repo_id}")
                    gguf_path = _gguf.download_gguf(repo_id, sel)
                srv_exe = _gguf.ensure_llama_server()
                gb = _gguf.LlamaBackend()
                backend_obj = gb
                with self._lock:
                    if self._cluster_epoch != my_epoch:
                        raise RuntimeError("Cluster stopped during deployment")
                    self._starting_backend = gb
                threads = _gguf.default_threads()
                if self.coordinator_quota.get("max_threads"):
                    threads = max(1, int(self.coordinator_quota["max_threads"]))
                gb.start(
                    gguf_path=gguf_path,
                    ctx_tokens=self.ctx_tokens,
                    ngl=int(plan.get("ngl") or 0),
                    threads=threads,
                    model_id=model_id,
                    server_path=srv_exe,
                    wait_timeout=360.0 if plan.get("rpc_arg") else 180.0,
                    rpc_servers=plan.get("rpc_arg"),
                    tensor_split=plan.get("tensor_split_arg"),
                    parallel_slots=self.parallel_slots,
                    split_mode="layer",
                )
                with self._lock:
                    if self._cluster_epoch != my_epoch:
                        raise RuntimeError("Cluster stopped during deployment")
                if verify:
                    with self._lock:
                        self.state = "VERIFYING"
                    ok = self._verify_gguf(gb)
                    if not ok:
                        raise RuntimeError("Distributed GGUF self-test verification failed")
                with self._lock:
                    self._starting_backend = None
                    if self._cluster_epoch != my_epoch or self.role != "coordinator":
                        raise RuntimeError("Cluster stopped during deployment")
                    self._gguf_backend = gb
                    self.state = "READY"
                    self.ready_at = time.time()
                    self._reform_requested = False
                    self._reform_infeasible = False
                    self._reform_failures = 0
                    self._next_reform_at = 0.0
                return self.status(include_secrets=False)
            except Exception as e:
                with self._lock:
                    if self._starting_backend is backend_obj:
                        self._starting_backend = None
                if backend_obj is not None:
                    try:
                        backend_obj.stop()
                    except Exception:
                        pass
                with self._lock:
                    if self._cluster_epoch == my_epoch and self.role == "coordinator":
                        self.state = "DEGRADED" if prev_ready else "FORMING"
                        self.last_error = str(e)[:240]
                raise
    def _verify_pipeline(self, pc):
        out = []
        for ev in pc.generate_stream([{"role": "user", "content": "ping"}], max_tokens=2, temperature=0.0):
            if ev.get("type") == "done":
                out.append(ev)
        return bool(out)
    def _verify_gguf(self, gb):
        for kind, _ in gb.chat_stream([{"role": "user", "content": "ping"}], max_tokens=4, temperature=0.0):
            if kind == "error":
                return False
        return gb.is_running()
    def is_ready(self):
        with self._lock:
            if self.state != "READY":
                return False
            if self._pipeline_coord is not None and self._pipeline_coord.is_running():
                return True
            if self._gguf_backend is not None and self._gguf_backend.is_running():
                return True
            return False
    def _gguf_stream(self, gb, messages, max_tokens, temperature, ctx):
        from ..engine import _StreamSanitizer
        pol = _res.POLICY
        t0 = time.perf_counter()
        sanitizer = _StreamSanitizer()
        token_count = 0
        ttft_ms = None
        usage = None
        ctx.phase = "generate"
        def _fail(code, message):
            return _res.InferenceFailure(code, message, request_id=ctx.request_id, phase="generate", tokens_delivered=ctx.tokens_delivered)
        for kind, payload in gb.chat_stream_guarded(messages, max_tokens=int(max_tokens), temperature=float(temperature), poll=ctx.poll, stall_timeout_s=pol.stall_timeout_s, poll_slice_s=pol.poll_slice_s):
            if kind == "delta" and payload:
                for ev_type, ev_tok in sanitizer.feed(payload):
                    if ev_tok:
                        if ttft_ms is None:
                            ttft_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                        token_count += 1
                        ctx.tokens_delivered += 1
                        yield {"type": ev_type, "token": ev_tok, "done": False}
                if sanitizer.stopped:
                    break
            elif kind == "stalled":
                raise _fail("WORKER_STALLED", f"Inference backend produced no data for {pol.stall_timeout_s:.0f}s")
            elif kind == "truncated":
                raise _fail("BACKEND_ERROR", "Inference backend closed the stream before completion")
            elif kind == "error":
                raise _fail("BACKEND_ERROR", f"Inference backend error: {str(payload)[:160]}")
            elif kind == "usage" and isinstance(payload, dict):
                usage = payload
        for ev_type, ev_tok in sanitizer.flush():
            if ev_tok:
                token_count += 1
                ctx.tokens_delivered += 1
                yield {"type": ev_type, "token": ev_tok, "done": False}
        total_s = max(0.001, time.perf_counter() - t0)
        yield {
            "type": "done",
            "token": "",
            "done": True,
            "telemetry": {
                "thinking_seconds": sanitizer.think_duration,
                "total_seconds": round(total_s, 3),
                "ttft_ms": ttft_ms or round(total_s * 1000.0, 2),
                "tokens_per_sec": round(token_count / total_s, 2),
                "inter_worker_transfer_ms": 0.0,
            },
            "usage": usage or {"prompt_tokens": 1, "completion_tokens": token_count, "total_tokens": 1 + token_count},
        }
    def _current_backend(self):
        with self._lock:
            pc = self._pipeline_coord
            gb = self._gguf_backend
        if pc is not None and pc.is_running():
            return "pipeline", pc
        if gb is not None and gb.is_running():
            return "gguf", gb
        return None, None
    def _backend_workers(self, kind, backend):
        if kind == "pipeline":
            return set(backend.worker_ids())
        with self._lock:
            return {wid for wid, w in self.workers.items() if w.get("assigned_stage") and (w.get("state") or _res.HEALTHY) in (_res.HEALTHY, _res.SUSPECT)}
    def _await_recovery(self, ctx, old_backend, wait_s):
        start_t = time.monotonic()
        end = start_t + float(wait_s)
        while time.monotonic() < end:
            ctx.poll(check_abort=False)
            kind, backend = self._current_backend()
            with self._lock:
                ready = self.state == "READY"
                pending = self._reform_active or self._reform_requested
                infeasible = self._reform_infeasible
                stopped = self.role != "coordinator"
            if stopped:
                return None, None
            if kind is not None and ready and backend is not old_backend:
                return kind, backend
            if infeasible and not pending:
                return None, None
            if not pending and not ready and (time.monotonic() - start_t) > 0.35:
                return None, None
            time.sleep(0.05)
        return None, None
    def generate_stream(self, messages, max_tokens=512, temperature=0.7, cancel_check=None, request_id=None, deadline_s=None):
        pol = _res.POLICY
        eff_dl = _res.resolve_deadline(deadline_s, pol.request_deadline_s)
        ctx = _res.ExecutionContext(request_id, cancel_check, eff_dl)
        kind, backend = self._current_backend()
        if kind is None:
            raise _res.InferenceFailure("CLUSTER_UNAVAILABLE", "Distributed cluster backend is not ready", request_id=ctx.request_id, cluster_available=False)
        self._register_execution(ctx)
        attempt = 0
        resume = None
        stream = None
        try:
            while True:
                ctx.poll(check_abort=False)
                ctx.workers = self._backend_workers(kind, backend)
                ctx.stage_map = {wid: i for i, wid in enumerate(backend.worker_ids())} if kind == "pipeline" else {}
                try:
                    if kind == "pipeline":
                        stream = backend.generate_stream(messages, max_tokens=max_tokens, temperature=temperature, ctx=ctx, resume=resume)
                    else:
                        stream = self._gguf_stream(backend, messages, max_tokens, temperature, ctx)
                    for ev in stream:
                        yield ev
                    if attempt > 0:
                        _res.METRICS.inc("recovery_success_total")
                        _res.METRICS.event("recovery_succeeded", request_id=ctx.request_id, attempts=attempt, tokens_delivered=ctx.tokens_delivered, resumed=kind == "pipeline" and bool(resume and resume.generated_ids))
                    return
                except _res.RequestCancelled:
                    _res.METRICS.inc("generations_cancelled_total")
                    return
                except _res.InferenceFailure as f:
                    f.request_id = ctx.request_id
                    f.tokens_delivered = ctx.tokens_delivered
                    f.recovery_attempted = f.recovery_attempted or attempt > 0
                    f.cluster_available = self._cluster_available()
                    transient = f.code in ("WORKER_LOST", "WORKER_TIMEOUT", "WORKER_STALLED", "STALE_RESPONSE", "BACKEND_ERROR", "CLUSTER_UNAVAILABLE")
                    can_resume = kind == "pipeline" and pol.enable_resume
                    can_restart = ctx.tokens_delivered == 0
                    if not (transient and (can_resume or can_restart) and attempt < pol.max_recovery_attempts):
                        _res.METRICS.event("recovery_not_possible", level=30, request_id=ctx.request_id, code=f.code, backend_kind=kind, tokens_delivered=ctx.tokens_delivered, attempt=attempt)
                        raise
                    if kind == "gguf" and f.code in ("BACKEND_ERROR", "WORKER_STALLED"):
                        self._probe_assigned_workers()
                        with self._lock:
                            if self.role == "coordinator" and not (self._reform_active or self._reform_requested) and not backend.is_running():
                                if self.state == "READY":
                                    self.state = "DEGRADED"
                                    self.last_error = f.message[:240]
                                self._reform_requested = True
                                self._rebalance_needed.set()
                    attempt += 1
                    _res.METRICS.inc("recovery_attempts_total")
                    _res.METRICS.inc("retries_total")
                    _res.METRICS.event("recovery_started", request_id=ctx.request_id, worker_id=f.worker_id, code=f.code, attempt=attempt, tokens_delivered=ctx.tokens_delivered, mode="resume" if can_resume and ctx.tokens_delivered else "restart")
                    try:
                        new_kind, new_backend = self._await_recovery(ctx, backend, pol.recovery_wait_s)
                        if new_kind is None:
                            _res.METRICS.inc("recovery_failed_total")
                            _res.METRICS.event("recovery_failed", level=40, request_id=ctx.request_id, attempt=attempt, reason="cluster did not re-form in time")
                            raise _res.InferenceFailure("RECOVERY_FAILED", "A worker was lost during generation and the cluster did not re-form in time", request_id=ctx.request_id, worker_id=f.worker_id, stage=f.stage, phase=f.phase, retryable=True, recovery_attempted=True, cluster_available=self._cluster_available(), tokens_delivered=ctx.tokens_delivered)
                        same_model = kind == new_kind and (kind != "pipeline" or backend.model_path_or_repo == new_backend.model_path_or_repo)
                        if ctx.tokens_delivered and not (can_resume and same_model):
                            _res.METRICS.inc("recovery_failed_total")
                            raise _res.InferenceFailure("RECOVERY_FAILED", "Generation state cannot be carried over to the re-formed cluster", request_id=ctx.request_id, worker_id=f.worker_id, stage=f.stage, phase=f.phase, retryable=True, recovery_attempted=True, cluster_available=self._cluster_available(), tokens_delivered=ctx.tokens_delivered)
                        resume = (f.resume or _res.ResumeState()) if new_kind == "pipeline" else None
                        kind, backend = new_kind, new_backend
                        ctx.reset_for_retry()
                        ctx.sleep(pol.backoff_s(attempt - 1), check_abort=False)
                    except _res.RequestCancelled:
                        _res.METRICS.inc("generations_cancelled_total")
                        return
        finally:
            if stream is not None:
                stream.close()
            self._unregister_execution(ctx)
            if ctx.aborted_at is not None:
                _res.METRICS.observe("request_cleanup_ms", (time.monotonic() - ctx.aborted_at) * 1000.0)
    def _monitor_loop(self):
        while not self._monitor_stop.is_set():
            self._rebalance_needed.wait(timeout=_res.POLICY.monitor_tick_s)
            self._rebalance_needed.clear()
            if self._monitor_stop.is_set():
                break
            now = time.time()
            with self._lock:
                if self.role != "coordinator":
                    break
                self._refresh_worker_states_locked(now)
                lost = list(self._newly_failed)
                self._newly_failed.clear()
                capacity = self._capacity_returned
                self._capacity_returned = False
                assigned = {wid for wid, _ in lost if (self.workers.get(wid) or {}).get("assigned_stage")}
            for wid, age in lost:
                if wid in assigned:
                    self._handle_assigned_worker_lost(wid, f"no heartbeat for {age:.1f}s", "WORKER_LOST")
            self._maybe_start_reform(capacity)
    def join_cluster_as_worker(
        self,
        coordinator_url,
        auth_code="",
        name=None,
        quota=None,
        consented=False,
        start_rpc=True,
        start_pipeline=True,
        via_tunnel=False,
    ):
        if not consented:
            raise PermissionError("Worker consent must be explicitly approved")
        import requests
        self.stop_local_worker()
        base = str(coordinator_url or "").strip().rstrip("/")
        caps = _cap.probe_node_capabilities(force_refresh=True)
        hw = caps["hw"]
        q = dict(quota or {})
        eff = _hardware.effective_usable_resources(hw, quota=q)
        rpc_port = None
        rpc_proc = None
        pipe_srv = None
        pipe_port = None
        bind_host = "127.0.0.1" if via_tunnel else "0.0.0.0"
        if start_rpc and _lan.find_rpc_server():
            rpc_port = _free_port()
            cmd = _lan.worker_cmd(rpc_port, exe=_lan.find_rpc_server(), host=bind_host, threads=eff["usable_threads"])
            kw = {"creationflags": _lan._CREATE_NO_WINDOW} if os.name == "nt" else {}
            rpc_proc = _lan.subprocess.Popen(cmd, stdin=_lan.subprocess.DEVNULL, stdout=_lan.subprocess.DEVNULL, stderr=_lan.subprocess.PIPE, **kw)
        worker_id = f"wrk-{_lan.LAN.node_id() or uuid.uuid4().hex[:10]}"
        if start_pipeline and (hw.get("backends") or {}).get("torch_pipeline"):
            pipe_srv = _pworker.PipelineWorkerServer(
                host=bind_host,
                port=0,
                auth_token="",
                max_threads=eff["usable_threads"],
                allow_cuda=eff["usable_vram_mb"] >= 256,
                max_vram_mb=eff["usable_vram_mb"],
            )
            pipe_port = pipe_srv.start()
        if via_tunnel:
            try:
                from .. import net_strategy
                ports = [p for p in (rpc_port, pipe_port) if p]
                net_strategy.get_network_manager().set_worker_allowed_ports(ports)
            except Exception:
                pass
        payload = {
            "worker_id": worker_id,
            "name": name or _lan.LAN.name() or socket.gethostname(),
            "hw": hw,
            "backends": caps["backends"],
            "quota": q,
            "consented": True,
            "auth_code": auth_code,
            "rpc_port": rpc_port,
            "pipeline_port": pipe_port,
            "rpc_host": None if via_tunnel else caps["primary_ip"],
            "pipeline_host": None if via_tunnel else caps["primary_ip"],
            "transport": "tunnel" if via_tunnel else "lan",
        }
        try:
            r = requests.post(f"{base}/api/cluster/workers/register", json=payload, timeout=8.0)
            if r.status_code != 200:
                raise RuntimeError(f"Coordinator rejected worker registration ({r.status_code}): {r.text[:200]}")
            data = r.json()
        except Exception:
            if rpc_proc is not None:
                try:
                    rpc_proc.terminate()
                except Exception:
                    pass
            if pipe_srv is not None:
                pipe_srv.stop()
            raise
        wtok = data["worker_token"]
        if pipe_srv is not None:
            pipe_srv.auth_token = wtok
        hb_stop = threading.Event()
        with self._lock:
            self.role = "worker"
            self._local_worker = {
                "active": True,
                "coordinator_url": base,
                "worker_id": data["worker_id"],
                "worker_token": wtok,
                "quota": q,
                "rpc_proc": rpc_proc,
                "rpc_port": rpc_port,
                "pipeline_srv": pipe_srv,
                "pipeline_port": pipe_port,
                "hb_stop": hb_stop,
                "hb_thread": None,
                "via_tunnel": bool(via_tunnel),
            }
        hb_th = threading.Thread(target=self._worker_hb_loop, daemon=True)
        with self._lock:
            self._local_worker["hb_thread"] = hb_th
        hb_th.start()
        return {"status": "ok", "worker_id": data["worker_id"], "rpc_port": rpc_port, "pipeline_port": pipe_port}
    def _worker_hb_loop(self):
        import requests
        hb_interval = _res.POLICY.heartbeat_interval_s
        while True:
            with self._lock:
                lw = dict(self._local_worker)
            stop_ev = lw.get("hb_stop")
            if not lw.get("active") or stop_ev is None or stop_ev.is_set():
                break
            try:
                hw = _hardware.get_hardware_profile()
                pipe_stats = lw["pipeline_srv"].stats() if lw.get("pipeline_srv") else {}
                requests.post(
                    f"{lw['coordinator_url']}/api/cluster/workers/heartbeat",
                    json={
                        "worker_id": lw["worker_id"],
                        "worker_token": lw["worker_token"],
                        "hw": hw,
                        "quota": lw["quota"],
                        "workload": {
                            "active_tasks": pipe_stats.get("active_kv_sessions", 0),
                            "requests_handled": pipe_stats.get("requests_handled", 0),
                            "last_compute_ms": pipe_stats.get("last_compute_ms", 0.0),
                        },
                    },
                    timeout=3.0,
                )
            except Exception:
                pass
            if stop_ev.wait(2.5):
                break
    def stop_local_worker(self):
        with self._lock:
            lw = dict(self._local_worker)
            if lw.get("hb_stop"):
                lw["hb_stop"].set()
            self._local_worker["active"] = False
            if self.role == "worker":
                self.role = "idle"
        th = lw.get("hb_thread")
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2.0)
        if lw.get("active") and lw.get("coordinator_url") and lw.get("worker_token"):
            try:
                import requests
                requests.post(
                    f"{lw['coordinator_url']}/api/cluster/workers/leave",
                    json={"worker_id": lw["worker_id"], "worker_token": lw["worker_token"]},
                    timeout=2.0,
                )
            except Exception:
                pass
        if lw.get("rpc_proc") is not None:
            try:
                lw["rpc_proc"].terminate()
                lw["rpc_proc"].wait(timeout=3.0)
            except Exception:
                try:
                    lw["rpc_proc"].kill()
                except Exception:
                    pass
        if lw.get("pipeline_srv") is not None:
            try:
                lw["pipeline_srv"].stop()
            except Exception:
                pass
        return {"status": "ok", "stopped": True}
    def preview_cluster(self):
        with self._lock:
            nodes = self._build_planner_nodes() if self.role == "coordinator" else []
            agg_vram = sum((_hardware.effective_usable_resources(n.get("hw"), n.get("quota")).get("usable_vram_mb", 0)) for n in nodes)
            agg_ram = sum((_hardware.effective_usable_resources(n.get("hw"), n.get("quota")).get("usable_ram_mb", 0)) for n in nodes)
            return {
                "cluster_active": self.role == "coordinator",
                "cluster_id": self.cluster_id,
                "name": self.name,
                "state": self.state,
                "target_model": self.target_model,
                "strategy": self.strategy,
                "open": self.open_net,
                "access_mode": self.access_mode,
                "worker_count": len(self._alive_worker_records()),
                "aggregate_usable_vram_mb": agg_vram,
                "aggregate_usable_ram_mb": agg_ram,
                "model_requirements": {
                    "params_b": (self.model_spec or {}).get("params_b"),
                    "num_layers": (self.model_spec or {}).get("num_layers"),
                    "quant": (self.model_spec or {}).get("quant"),
                    "required_mb": (self.plan or {}).get("total_required_mb"),
                } if self.model_spec else None,
                "consent_required": True,
                "privacy_notice": "Resources are never allocated without explicit consent. Quotas limit maximum VRAM, RAM, and CPU threads.",
            }
    def status(self, include_secrets=False):
        now = time.time()
        with self._lock:
            coord = self._coordinator_node_record()
            workers_out = []
            for wid, w in self.workers.items():
                alive = (now - float(w.get("last_heartbeat") or 0)) <= HEARTBEAT_TIMEOUT_S
                w["alive"] = alive
                rec = copy.deepcopy(w)
                rec.pop("worker_token", None)
                rec["heartbeat_age_s"] = round(max(0.0, now - float(w.get("last_heartbeat") or now)), 1)
                workers_out.append(rec)
            alive_workers = [w for w in workers_out if w["alive"] and not w.get("revoked")]
            all_nodes = ([coord] if self.coordinator_contributes or self.role == "coordinator" else []) + alive_workers
            agg_raw_vram = sum(int((n.get("hw") or {}).get("vram_total_mb") or 0) for n in all_nodes)
            agg_usable_vram = sum(int((n.get("effective") or {}).get("usable_vram_mb") or 0) for n in all_nodes)
            agg_raw_ram = sum(int((n.get("hw") or {}).get("ram_total_mb") or 0) for n in all_nodes)
            agg_usable_ram = sum(int((n.get("effective") or {}).get("usable_ram_mb") or 0) for n in all_nodes)
            agg_threads = sum(int((n.get("effective") or {}).get("usable_threads") or 0) for n in all_nodes)
            rtts = [float(w["rtt_ms"]) for w in alive_workers if w.get("rtt_ms") is not None]
            avg_rtt = round(sum(rtts) / len(rtts), 2) if rtts else 0.0
            slots = self._gguf_backend.slots_status() if self._gguf_backend else []
            lw = {
                "active": bool(self._local_worker.get("active")),
                "coordinator_url": self._local_worker.get("coordinator_url"),
                "worker_id": self._local_worker.get("worker_id"),
                "rpc_port": self._local_worker.get("rpc_port"),
                "pipeline_port": self._local_worker.get("pipeline_port"),
                "quota": copy.deepcopy(self._local_worker.get("quota") or {}),
            }
            return {
                "role": self.role,
                "state": self.state,
                "ready": self.is_ready(),
                "cluster_id": self.cluster_id,
                "name": self.name,
                "open": self.open_net,
                "access_mode": self.access_mode,
                "pairing_code": self.pairing_code if include_secrets else None,
                "target_model": self.target_model,
                "strategy": self.strategy,
                "ctx_tokens": self.ctx_tokens,
                "parallel_slots": self.parallel_slots,
                "coordinator_contributes": self.coordinator_contributes,
                "last_error": self.last_error,
                "created_at": self.created_at,
                "ready_at": self.ready_at,
                "cluster_overview": {
                    "worker_count": len(alive_workers),
                    "total_workers_registered": len(workers_out),
                    "aggregate_raw_vram_mb": agg_raw_vram,
                    "aggregate_usable_vram_mb": agg_usable_vram,
                    "aggregate_raw_ram_mb": agg_raw_ram,
                    "aggregate_usable_ram_mb": agg_usable_ram,
                    "aggregate_usable_threads": agg_threads,
                    "avg_worker_rtt_ms": avg_rtt,
                    "note": "Aggregate values reflect bounded usable resources across separate nodes, not a single unified GPU.",
                },
                "coordinator": coord,
                "workers": workers_out,
                "plan": copy.deepcopy(self.plan),
                "model_spec": {k: v for k, v in (self.model_spec or {}).items() if k not in ("weight_map", "config")} if self.model_spec else None,
                "telemetry": _sched.SCHEDULER.telemetry(),
                "gguf_slots": slots,
                "local_worker": lw,
            }
CLUSTER = ClusterManager()
