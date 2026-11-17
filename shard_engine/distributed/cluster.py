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
