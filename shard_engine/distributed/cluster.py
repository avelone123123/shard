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
