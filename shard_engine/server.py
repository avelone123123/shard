import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from . import __version__
from . import feedback
from . import gguf_backend
from . import net_identity
from . import net_strategy
from . import net_tunnel
from . import net_wireguard
from . import paths
from . import settings
from . import soup_adapter
from . import storage
from . import training
from .engine import engine_instance
from .fit import fit_report
from .hardware import evaluate_compatibility, get_hardware_profile
from .hf_hub import browse_models, fetch_model_card, fetch_model_metadata, get_local_cached_ids
from .lan import DEFAULT_MASTER_PORT, LAN, is_loopback
_SAFE_HF_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}(?:/[A-Za-z0-9][A-Za-z0-9._-]{0,95})?$")
_SAFE_MODEL_RE = re.compile(r"^[A-Za-z0-9._/\-:]{1,160}$")
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9._\-]{4,128}$")
_VALID_TOOLS = {"claude", "opencode", "continue", "aider", "zed", "codex", "openrouter", "cursor", "env", "sdk"}
_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip", "forwarded", "cf-connecting-ip", "true-client-ip", "x-client-ip")
class _SlidingWindowLimiter:
    def __init__(self, limit, window_s=60.0, max_ips=4096):
        self.limit = int(limit)
        self.window_s = float(window_s)
        self.max_ips = int(max_ips)
        self._hits = {}
        self._lock = threading.Lock()
    def check(self, key, record=True):
        now = time.monotonic()
        k = str(key or "unknown")
        with self._lock:
            if k not in self._hits and len(self._hits) >= self.max_ips:
                oldest = next(iter(self._hits))
                self._hits.pop(oldest, None)
            q = self._hits.setdefault(k, deque())
            cutoff = now - self.window_s
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= self.limit:
                retry = max(1, int(self.window_s - (now - q[0])))
                return False, retry
            if record:
                q.append(now)
            return True, 0
    def record(self, key):
        return self.check(key, record=True)
_PAIR_FAIL_LIMITER = _SlidingWindowLimiter(limit=10, window_s=60.0)
_PAIR_TOTAL_LIMITER = _SlidingWindowLimiter(limit=30, window_s=60.0)
_OWNER_FAIL_LIMITER = _SlidingWindowLimiter(limit=8, window_s=60.0)
_FEEDBACK_LIMITER = _SlidingWindowLimiter(limit=40, window_s=60.0)
_NET_BIND_DIR = None
def _net_local_models():
    out = []
    if engine_instance.active_model_id:
        out.append(str(engine_instance.active_model_id))
    try:
        for mid in get_local_cached_ids():
            if mid and mid not in out:
                out.append(str(mid))
            if len(out) >= 10:
                break
    except Exception:
        pass
    return out
def _net_load_provider():
    try:
        st = str(engine_instance.status or "").lower()
        return 0.5 if st in ("loading", "busy", "generating") else 0.0
    except Exception:
        return 0.0
def _net_status_provider():
    is_loaded = engine_instance.model is not None or (engine_instance.gguf is not None and engine_instance.gguf.is_running())
    return {
        "active_model": engine_instance.active_model_id,
        "engine_status": engine_instance.status,
        "is_loaded": is_loaded,
        "lan_mode": LAN.mode(),
    }
def _net_mgr():
    global _NET_BIND_DIR
    cur_dir = str(paths.data_dir())
    cur_mgr = getattr(net_strategy, "_DEFAULT_MANAGER", None)
    if cur_mgr is not None and _NET_BIND_DIR is not None and _NET_BIND_DIR != cur_dir:
        if getattr(cur_mgr, "_config_path", "") == os.path.join(_NET_BIND_DIR, "network_config.json"):
            try:
                cur_mgr.shutdown()
            except Exception:
                pass
            with net_strategy._DEFAULT_MANAGER_LOCK:
                net_strategy._DEFAULT_MANAGER = None
    mgr = net_strategy.get_network_manager(
        asgi_app=app,
        model_provider=_net_local_models,
        load_provider=_net_load_provider,
        status_provider=_net_status_provider,
    )
    if _NET_BIND_DIR != cur_dir or getattr(mgr, "_asgi_app", None) is None:
        _NET_BIND_DIR = cur_dir
    if getattr(mgr, "_asgi_app", None) is None:
        mgr._asgi_app = app
    if getattr(mgr, "_model_provider", None) is None:
        mgr._model_provider = _net_local_models
    if getattr(mgr, "_load_provider", None) is None:
        mgr._load_provider = _net_load_provider
    if getattr(mgr, "_status_provider", None) is None:
        mgr._status_provider = _net_status_provider
    return mgr
def _safe_node_id():
    try:
        return _net_mgr().identity.node_id
    except Exception:
        return None
def _identity_payload(ident):
    pub_bytes = ident.pubkey_bytes()
    fp = hashlib.sha256(pub_bytes).hexdigest()
    return {
        "node_id": ident.node_id,
        "pubkey_b64": ident.pubkey_b64,
        "fingerprint": fp,
        "fingerprint_short": ":".join(fp[i:i + 4] for i in range(0, 32, 4)),
        "created_at": getattr(ident, "_created_at", None),
    }
@asynccontextmanager
async def lifespan(_app):
    LAN.start_runtime()
    try:
        _net_mgr()
    except Exception:
        pass
    try:
        yield
    finally:
        try:
            mgr = getattr(net_strategy, "_DEFAULT_MANAGER", None)
            if mgr is not None:
                mgr.shutdown()
        except Exception:
            pass
        try:
            LAN.stop_runtime()
        except Exception:
            pass
        try:
            training.shutdown_active_jobs()
        except Exception:
            pass
        try:
            engine_instance.unload_model()
        except Exception:
            pass
app = FastAPI(title="Shard Local Model Engine and Server", version=__version__, lifespan=lifespan)
def _extract_host_only(host_hdr):
    raw = str(host_hdr or "").strip()
    if not raw:
        return ""
    if any(c in raw for c in ("/", "\\", "@", " ", "\t", "\r", "\n")):
        return None
    if raw.startswith("[") and "]" in raw:
        return raw[1:raw.index("]")].lower()
    if ":" in raw and raw.count(":") == 1:
        return raw.split(":", 1)[0].lower()
    return raw.lower()
def _is_trusted_host(host_hdr):
    if not host_hdr:
        return True
    h = _extract_host_only(host_hdr)
    if h is None or not h:
        return False
    allowed_env = {x.strip().lower() for x in os.environ.get("SHARD_ALLOWED_HOSTS", "").split(",") if x.strip()}
    if h in allowed_env or h in ("localhost", "127.0.0.1", "::1", "testserver", "testclient"):
        return True
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        pass
    try:
        local_name = socket.gethostname().lower()
        if h == local_name or h == f"{local_name}.local" or h.endswith(".local"):
            return True
    except Exception:
        pass
    return False
def _allowed_cors_origin(origin_hdr, host_hdr):
    if not origin_hdr:
        return None
    try:
        parsed = urlparse(origin_hdr)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if host_hdr and parsed.netloc.lower() == str(host_hdr).strip().lower():
        return origin_hdr
    extra = [x.strip() for x in os.environ.get("SHARD_CORS_ORIGINS", "").split(",") if x.strip()]
    if origin_hdr in extra:
        return origin_hdr
    return None
def _apply_sec_headers(resp, cors_origin=None):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
