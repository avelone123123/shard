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
def api_hf_models(req: Request, query: str = None, category: str = "all", limit: int = 80):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return browse_models(query=query, category=category, limit=limit)
@app.get("/api/hf/model/{model_id:path}")
def api_hf_model_card(model_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return fetch_model_card(model_id)
@app.post("/api/models/load")
async def api_load_model(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    data = await req.json()
    model_id = str(data.get("model_id", "Qwen/Qwen2.5-Coder-1.5B-Instruct")).strip()
    if engine_instance.is_model_active(model_id):
        return {"status": "ok", "loaded": engine_instance.loaded_info, "already_loaded": True}
    try:
        info = engine_instance.load_model(model_id)
        return {"status": "ok", "loaded": info}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/models/status")
@app.get("/api/engine/status")
def api_models_status(req: Request, model_id: str = None):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    st = engine_instance.get_status()
    if model_id:
        st["lifecycle_state"] = engine_instance.lifecycle_state(model_id)
        st["detailed_lifecycle_state"] = engine_instance.detailed_lifecycle_state(model_id)
        st["is_model_active"] = engine_instance.is_model_active(model_id)
    return st
@app.get("/api/models/quants/{model_id:path}")
def api_model_quants(model_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    mid = (model_id or "").strip()
    if not mid:
        return JSONResponse(status_code=400, content={"status": "error", "message": "model_id required"})
    return {"status": "ok", **gguf_backend.list_available_quants(mid)}
@app.post("/api/models/unload")
def api_unload_model(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return engine_instance.unload_model()
@app.post("/api/models/cancel")
def api_cancel_model(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return engine_instance.cancel_operation()
@app.get("/api/models/downloaded")
def api_get_downloaded_models(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    candidate_roots = [
        os.environ.get("HF_HOME"),
        settings.models_dir(),
        paths.default_models_dir(),
        os.path.join(os.path.expanduser("~"), ".shard", "models"),
        os.path.join(os.path.expanduser("~"), ".cache", "huggingface"),
        str(paths.data_dir() / "models"),
    ]
    hub_dirs = []
    for d in candidate_roots:
        if d:
            h = os.path.join(d, "hub") if not str(d).endswith("hub") else d
            norm = os.path.normcase(os.path.abspath(h))
            if os.path.isdir(h) and norm not in [os.path.normcase(os.path.abspath(x)) for x in hub_dirs]:
                hub_dirs.append(h)
    hw = get_hardware_profile()
    models = []
    seen_ids = set()
    total_bytes = 0
    for hub_dir in hub_dirs:
        try:
            from huggingface_hub import scan_cache_dir
            cache = scan_cache_dir(hub_dir)
            for repo in cache.repos:
                if repo.repo_id.lower() in seen_ids:
                    continue
                seen_ids.add(repo.repo_id.lower())
                total_bytes += repo.size_on_disk
                sz_mb = round(repo.size_on_disk / (1024 * 1024), 1)
                sz_str = f"{round(sz_mb / 1024, 2)} ГБ" if sz_mb >= 1024 else f"{sz_mb} МБ"
                author = repo.repo_id.split("/")[0] if "/" in repo.repo_id else "Community"
                name = repo.repo_id.split("/")[-1]
                comp = evaluate_compatibility(repo.repo_id, hw)
                is_act = engine_instance.is_model_active(repo.repo_id)
                models.append({
                    "id": repo.repo_id,
                    "author": author,
                    "name": name,
                    "size_bytes": repo.size_on_disk,
                    "size_mb": sz_mb,
                    "size_str": sz_str,
                    "is_active": is_act,
                    "last_modified": repo.last_modified,
                    "compatibility": comp
                })
        except Exception:
            for item in os.listdir(hub_dir):
                if item.startswith("models--"):
                    clean_id = item.replace("models--", "").replace("--", "/")
                    if clean_id.lower() in seen_ids:
                        continue
                    seen_ids.add(clean_id.lower())
                    full_p = os.path.join(hub_dir, item)
                    sz = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(full_p) for f in fs if os.path.exists(os.path.join(r, f)))
                    sz_mb = round(sz / (1024 * 1024), 1)
                    sz_str = f"{round(sz_mb / 1024, 2)} ГБ" if sz_mb >= 1024 else f"{sz_mb} МБ"
                    total_bytes += sz
                    models.append({
                        "id": clean_id,
                        "author": clean_id.split("/")[0] if "/" in clean_id else "Community",
                        "name": clean_id.split("/")[-1],
                        "size_bytes": sz,
                        "size_mb": sz_mb,
                        "size_str": sz_str,
                        "is_active": engine_instance.is_model_active(clean_id),
                        "last_modified": int(os.path.getmtime(full_p)),
                        "compatibility": evaluate_compatibility(clean_id, hw)
                    })
    models.sort(key=lambda x: x["size_bytes"], reverse=True)
    return {
        "models": models,
        "total_models": len(models),
        "total_size_gb": round(total_bytes / (1024 ** 3), 2),
        "disk_free_gb": hw.get("disk_free_gb", 0)
    }
@app.delete("/api/models/downloaded")
@app.post("/api/models/delete")
async def api_delete_downloaded_model(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    model_id = ""
    try:
        data = await req.json()
        model_id = str(data.get("model_id", "")).strip()
    except Exception:
        pass
    if not model_id:
        model_id = str(req.query_params.get("model_id", "")).strip()
    if not model_id:
        return JSONResponse(status_code=400, content={"status": "error", "message": "model_id required"})
    if "\\" in model_id or ".." in model_id or not _SAFE_HF_REPO_RE.match(model_id):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid model_id format"})
    if engine_instance.active_model_id == model_id or engine_instance.current_loaded_id == model_id:
        engine_instance.unload_model()
    hf_home = os.environ.get("HF_HOME") or settings.models_dir()
    hub_dir = os.path.join(hf_home, "hub")
    freed_bytes = 0
    try:
        from huggingface_hub import scan_cache_dir
        cache = scan_cache_dir(hub_dir)
        target_repo = None
        for r in cache.repos:
            if r.repo_id.lower() == model_id.lower() or r.repo_id.split("/")[-1].lower() == model_id.lower():
                target_repo = r
                break
        if target_repo:
            freed_bytes = target_repo.size_on_disk
            rev_hashes = [rev.commit_hash for rev in target_repo.revisions]
            if rev_hashes:
                strategy = cache.delete_revisions(*rev_hashes)
                strategy.execute()
    except Exception:
        pass
    norm_name = "models--" + model_id.replace("/", "--")
    hub_root = Path(hub_dir).resolve()
    folder_path = (hub_root / norm_name).resolve()
    if folder_path.is_relative_to(hub_root) and folder_path != hub_root and folder_path.exists():
        if freed_bytes == 0:
            freed_bytes = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(str(folder_path)) for f in fs if os.path.exists(os.path.join(r, f)))
        shutil.rmtree(str(folder_path), ignore_errors=True)
    sz_mb = round(freed_bytes / (1024 * 1024), 1)
    freed_str = f"{round(sz_mb / 1024, 2)} ГБ" if sz_mb >= 1024 else f"{sz_mb} МБ"
    return {"status": "ok", "deleted": model_id, "freed_str": freed_str, "freed_bytes": freed_bytes}
@app.get("/api/keys")
def api_get_keys(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    keys = load_keys()
    return {"keys": keys}
@app.post("/api/keys/generate")
def api_gen_key(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    keys = load_keys()
    new_key = f"sk-shard-{secrets.token_hex(8)}"
    keys.append(new_key)
    save_keys(keys)
    return {"key": new_key, "keys": keys}
@app.delete("/api/keys")
@app.post("/api/keys/delete")
async def api_del_key(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    target = ""
    try:
        data = await req.json()
        target = str(data.get("key", "")).strip()
    except Exception:
        pass
    if not target:
        target = str(req.query_params.get("key", "")).strip()
    keys = load_keys()
    matched = None
