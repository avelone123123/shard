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
def _parse_endpoint(ep):
    s = str(ep or "").strip()
    if ":" not in s:
        raise ValueError(f"Invalid endpoint: {ep}")
    host, port_s = s.rsplit(":", 1)
    return host.strip("[]"), int(port_s)
class _RemoteStageClient:
    def __init__(self, stage_info, auth_token="", cluster_id="", timeout=60.0):
        self.stage_info = dict(stage_info)
        self.auth_token = str(auth_token or "")
        self.cluster_id = str(cluster_id or "")
        self.timeout = float(timeout)
        self.sock = None
        self.lock = threading.Lock()
    def connect(self):
        with self.lock:
            if self.sock is not None:
                return
            host, port = _parse_endpoint(self.stage_info["endpoint"])
