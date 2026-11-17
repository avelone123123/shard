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
            "request_id": str(request_id),
            "use_cache": bool(use_cache),
            "return_logits": bool(return_logits),
        }
        frame_bytes = proto.encode_tensor_payload(meta, tensor)
        with self.lock:
            t0 = time.perf_counter()
            proto.send_frame(self.sock, proto.MSG_FORWARD, frame_bytes)
            mtype, _, resp_payload = proto.recv_frame(self.sock)
            total_ms = (time.perf_counter() - t0) * 1000.0
        if mtype == proto.MSG_ERROR:
            try:
                err_obj = json.loads(resp_payload.decode("utf-8"))
                raise RuntimeError(err_obj.get("error") or "Worker forward error")
            except Exception as exc:
                if isinstance(exc, RuntimeError):
                    raise
                raise RuntimeError("Worker forward error")
        if mtype != proto.MSG_FORWARD_RES:
            raise RuntimeError(f"Unexpected forward response type {mtype}")
        out_meta, out_tensor = proto.decode_tensor_payload(resp_payload)
        compute_ms = float(out_meta.get("stage_compute_ms") or 0.0)
        transfer_ms = max(0.0, total_ms - compute_ms)
        return out_tensor, compute_ms, transfer_ms, len(frame_bytes) + len(resp_payload)
    def release_kv(self, request_id):
        with self.lock:
            if self.sock is None:
                return
            try:
                proto.send_json_frame(self.sock, proto.MSG_RELEASE_KV, {"request_id": str(request_id)})
                proto.recv_json_frame(self.sock)
            except Exception:
                pass
    def ping(self):
        self.connect()
        with self.lock:
            t0 = time.perf_counter()
            proto.send_json_frame(self.sock, proto.MSG_PING, {})
            mtype, _, obj = proto.recv_json_frame(self.sock)
            rtt = round((time.perf_counter() - t0) * 1000.0, 2)
        return {"rtt_ms": rtt, "stats": obj}
class _LocalStageRunner:
    def __init__(self, stage_info, model_path_or_repo, config, dtype="float32"):
        self.stage_info = dict(stage_info)
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
