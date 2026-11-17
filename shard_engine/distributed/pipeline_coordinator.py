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
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.timeout)
            s.connect((host, port))
            proto.client_auth_handshake(s, self.auth_token, self.cluster_id)
            self.sock = s
    def close(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except Exception:
                    pass
                self.sock = None
    def load_stage(self, model_path_or_repo, config_dict=None, dtype="float32"):
        self.connect()
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
        with self.lock:
            proto.send_json_frame(self.sock, proto.MSG_LOAD_STAGE, payload)
            mtype, _, obj = proto.recv_json_frame(self.sock)
        if mtype == proto.MSG_ERROR:
            raise RuntimeError(obj.get("error") or "Worker failed to load stage")
        if mtype != proto.MSG_STAGE_READY:
            raise RuntimeError(f"Unexpected worker response type {mtype}")
        return obj
    def forward(self, request_id, tensor, use_cache=True, return_logits=True):
        self.connect()
        meta = {
