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
