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
HEARTBEAT_TIMEOUT_S = 12.0
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
    def _alive_worker_records(self, now=None):
        now = time.time() if now is None else now
        out = []
        with self._lock:
            for wid, w in self.workers.items():
                if w.get("revoked"):
                    continue
                if w.get("token_expires_at") and now > w["token_expires_at"]:
                    continue
                alive = (now - float(w.get("last_heartbeat") or 0)) <= HEARTBEAT_TIMEOUT_S
                w["alive"] = alive
                if alive:
                    out.append(copy.deepcopy(w))
        return out
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
        self._stop_monitor()
        self._stop_execution_backends()
        try:
            from .. import net_strategy
            mgr = getattr(net_strategy, "_DEFAULT_MANAGER", None)
            if mgr is not None:
                mgr.close_worker_proxies()
        except Exception:
            pass
        with self._lock:
            self.role = "idle"
            self.state = "IDLE"
            self.cluster_id = None
            self.pairing_code = None
            self.plan = None
            self.model_spec = None
            self.last_error = None
            self.workers.clear()
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
            self._rebalance_needed.set()
        self.recompute_plan()
        return {"status": "ok", "worker_id": wid, "worker_token": tok, "cluster_id": self.cluster_id, "plan": self.plan}
    def heartbeat_worker(self, worker_id, worker_token, hw=None, quota=None, workload=None, rtt_ms=None, bandwidth_mbps=None):
        now = time.time()
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
            w["alive"] = True
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
            return True, {"cluster_state": self.state, "assigned_stage": w.get("assigned_stage")}
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
            if was_assigned and self.state == "READY":
                self.state = "DEGRADED"
                self._rebalance_needed.set()
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
            if was_assigned and self.state == "READY":
                self.state = "DEGRADED"
                self._rebalance_needed.set()
        if tpeer:
            try:
                from .. import net_strategy
                net_strategy.get_network_manager().close_worker_proxies(tpeer)
            except Exception:
                pass
        self.recompute_plan()
        return {"status": "ok", "removed": w is not None, "blocked": bool(block)}
    def deploy_cluster(self, verify=True):
        plan = self.recompute_plan()
        if not plan.get("feasible"):
            with self._lock:
                self.last_error = plan.get("reason")
            raise RuntimeError(plan.get("reason") or "Cluster partition plan is not feasible")
        with self._lock:
            self.state = "LOADING"
            self.last_error = None
            strategy = plan["strategy"]
            model_id = self.target_model
            spec = dict(self.model_spec or {})
            worker_tokens = {wid: w["worker_token"] for wid, w in self.workers.items()}
            cid = self.cluster_id
        self._stop_execution_backends()
        if strategy == "torch_pipeline_parallel":
            model_ref = spec.get("local_hf_dir") or model_id
            pc = _pcoord.PipelineCoordinator(model_ref, plan, worker_tokens=worker_tokens, cluster_id=cid)
            pc.start()
            if verify:
                with self._lock:
                    self.state = "VERIFYING"
                res = self._verify_pipeline(pc)
                if not res:
                    pc.stop()
                    raise RuntimeError("Distributed pipeline verification failed")
            with self._lock:
                self._pipeline_coord = pc
                self.state = "READY"
                self.ready_at = time.time()
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
        if verify:
            with self._lock:
                self.state = "VERIFYING"
            ok = self._verify_gguf(gb)
            if not ok:
                gb.stop()
                raise RuntimeError("Distributed GGUF self-test verification failed")
        with self._lock:
            self._gguf_backend = gb
            self.state = "READY"
            self.ready_at = time.time()
        return self.status(include_secrets=False)
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
    def generate_stream(self, messages, max_tokens=512, temperature=0.7, cancel_check=None):
        with self._lock:
            pc = self._pipeline_coord
            gb = self._gguf_backend
        if pc is not None and pc.is_running():
            yield from pc.generate_stream(messages, max_tokens=max_tokens, temperature=temperature, cancel_check=cancel_check)
            return
        if gb is not None and gb.is_running():
            from ..engine import _StreamSanitizer
            t0 = time.perf_counter()
            sanitizer = _StreamSanitizer()
            token_count = 0
            ttft_ms = None
            usage = None
            for kind, payload in gb.chat_stream(messages, max_tokens=int(max_tokens), temperature=float(temperature), cancel_check=cancel_check):
                if kind == "delta" and payload:
                    for ev_type, ev_tok in sanitizer.feed(payload):
                        if ev_tok:
                            if ttft_ms is None:
                                ttft_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                            token_count += 1
                            yield {"type": ev_type, "token": ev_tok, "done": False}
                    if sanitizer.stopped:
                        break
                elif kind == "error":
                    yield {"type": "content", "token": f"[Cluster inference error: {payload}]", "done": False}
                    break
                elif kind == "usage" and isinstance(payload, dict):
                    usage = payload
            for ev_type, ev_tok in sanitizer.flush():
                if ev_tok:
                    token_count += 1
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
            return
        raise RuntimeError("Distributed cluster backend is not ready")
    def _monitor_loop(self):
        while not self._monitor_stop.is_set():
            self._rebalance_needed.wait(timeout=2.0)
            self._rebalance_needed.clear()
            if self._monitor_stop.is_set():
                break
            now = time.time()
            lost_assigned = False
            with self._lock:
                if self.role != "coordinator":
                    break
                for w in self.workers.values():
                    if w.get("revoked"):
                        continue
                    alive = (now - float(w.get("last_heartbeat") or 0)) <= HEARTBEAT_TIMEOUT_S
                    prev = bool(w.get("alive", True))
                    w["alive"] = alive
                    if prev and not alive and w.get("assigned_stage"):
                        lost_assigned = True
                if lost_assigned and self.state == "READY":
                    self.state = "DEGRADED"
                    self.last_error = "Assigned worker timed out; cluster degraded"
            if lost_assigned:
                self._stop_execution_backends()
                plan = self.recompute_plan()
                if plan.get("feasible"):
                    try:
                        self.deploy_cluster(verify=True)
                    except Exception as e:
                        with self._lock:
                            self.state = "DEGRADED"
                            self.last_error = str(e)[:240]
                else:
                    with self._lock:
                        self.state = "DEGRADED"
                        self.last_error = plan.get("reason")
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
