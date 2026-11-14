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
    resp.headers["Referrer-Policy"] = "no-referrer"
    if cors_origin:
        resp.headers["Access-Control-Allow-Origin"] = cors_origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Shard-Owner-Token"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
    return resp
def _has_proxy_headers(req):
    for h in _PROXY_HEADERS:
        if req.headers.get(h):
            return True
    return False
def _token_in_pool(tok, pool):
    if not tok:
        return False
    t = str(tok)
    for cand in pool:
        if cand and secrets.compare_digest(t, str(cand)):
            return True
    return False
@app.middleware("http")
async def lan_gate(request, call_next):
    host_hdr = request.headers.get("host")
    if not _is_trusted_host(host_hdr):
        return _apply_sec_headers(JSONResponse(status_code=403, content={"status": "error", "message": "Untrusted Host header"}))
    origin_hdr = request.headers.get("origin")
    cors_ok = _allowed_cors_origin(origin_hdr, host_hdr)
    if request.method == "OPTIONS":
        return _apply_sec_headers(PlainTextResponse("", status_code=204), cors_origin=cors_ok)
    path = request.url.path or "/"
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        sec_site = (request.headers.get("sec-fetch-site") or "").strip().lower()
        if sec_site == "cross-site":
            return _apply_sec_headers(JSONResponse(status_code=403, content={"status": "error", "message": "Cross-site mutation blocked"}))
        if origin_hdr and not cors_ok:
            return _apply_sec_headers(JSONResponse(status_code=403, content={"status": "error", "message": "Cross-origin request blocked"}))
    is_public_ping = request.method == "GET" and path == "/v1/ping"
    if path.startswith("/v1") and not is_public_ping:
        try:
            if not _is_owner_request(request):
                is_tunnel = net_tunnel.is_tunnel_request(request)
                if not is_tunnel:
                    auth = request.headers.get("authorization") or ""
                    token = auth[7:].strip() if auth.lower().startswith("bearer ") else auth.strip()
                    valid_pool = set(load_keys())
                    if LAN.is_server():
                        valid_pool |= LAN.tokens()
                    if not _token_in_pool(token, valid_pool):
                        return _apply_sec_headers(
                            JSONResponse(
                                status_code=401,
                                content={"error": {"message": "Требуется действительный токен SHARD или участника сети", "type": "auth_error"}},
                            ),
                            cors_origin=cors_ok,
                        )
        except Exception:
            return _apply_sec_headers(
                JSONResponse(status_code=500, content={"error": {"message": "Authentication verification error", "type": "auth_error"}}),
                cors_origin=cors_ok,
            )
    resp = await call_next(request)
    return _apply_sec_headers(resp, cors_origin=cors_ok)
DATA_DIR = str(paths.data_dir())
KEYS_FILE = paths.keys_file()
def save_keys(keys):
    kf = paths.keys_file()
    clean = [str(k) for k in (keys or []) if str(k).strip()]
    storage.atomic_write_json(kf, clean, indent=2, fsync=True, backup=True, restrict=True)
    if isinstance(globals().get("API_KEYS"), list):
        API_KEYS[:] = clean
def load_keys():
    kf = paths.keys_file()
    with storage.file_lock(kf):
        d = storage.read_json_safe(kf, default=None, expected_type=list, quarantine=True, use_backup=True)
        if isinstance(d, list) and len(d) > 0:
            storage.restrict_file_permissions(kf)
            return [str(x) for x in d if str(x).strip()]
        initial = [f"sk-shard-{secrets.token_hex(8)}"]
        try:
            save_keys(initial)
        except Exception:
            pass
        return initial
API_KEYS = load_keys()
@app.get("/api/client/bootstrap")
def api_client_bootstrap(req: Request):
    is_owner = _is_owner_request(req)
    st = LAN.status(include_secrets=False)
    is_loaded = engine_instance.model is not None or (engine_instance.gguf is not None and engine_instance.gguf.is_running())
    return {
        "is_owner": is_owner,
        "client_mode": not is_owner,
        "version": __version__,
        "node_id": _safe_node_id(),
        "lan_mode": st.get("mode"),
        "lan_open": bool(st.get("open")),
        "server_name": st.get("name"),
        "active_model": engine_instance.active_model_id,
        "engine_status": engine_instance.status,
        "is_loaded": is_loaded,
    }
@app.get("/api/system/hardware")
def api_hardware(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    hw = get_hardware_profile()
    engine_st = engine_instance.get_status()
    return {"hardware": hw, "engine": engine_st, "version": __version__, "paths": paths.all_dirs(), "is_owner": True}
@app.get("/api/hf/models")
