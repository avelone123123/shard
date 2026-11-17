import asyncio
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
from . import model_browser
from . import net_identity
from . import net_strategy
from . import net_tunnel
from . import net_wireguard
from . import paths
from . import session_registry
from . import settings
from . import soup_adapter
from . import storage
from . import training
from . import updater
from .distributed import CLUSTER, SCHEDULER
from .distributed.scheduler import QueueFullError, RateLimitError
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
        updater.check_for_updates_async(force=False, timeout=2.0)
    except Exception:
        pass
    try:
        threading.Thread(
            target=lambda: soup_adapter.check_latest_soup_version(force=False, timeout=2.5, allow_network=True),
            daemon=True,
        ).start()
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
            CLUSTER.stop_local_worker()
        except Exception:
            pass
        try:
            CLUSTER.stop_cluster()
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
    cls_ready = CLUSTER.is_ready()
    is_loaded = cls_ready or engine_instance.model is not None or (engine_instance.gguf is not None and engine_instance.gguf.is_running())
    upd = None
    if is_owner:
        try:
            upd = updater.check_for_updates(force=False, allow_network=False)
        except Exception:
            upd = None
    return {
        "is_owner": is_owner,
        "client_mode": not is_owner,
        "version": __version__,
        "node_id": _safe_node_id(),
        "lan_mode": st.get("mode"),
        "lan_open": bool(st.get("open")),
        "server_name": CLUSTER.name if CLUSTER.role == "coordinator" else st.get("name"),
        "active_model": CLUSTER.target_model if cls_ready else engine_instance.active_model_id,
        "engine_status": "ready" if cls_ready else engine_instance.status,
        "is_loaded": is_loaded,
        "cluster_state": CLUSTER.state,
        "cluster_role": CLUSTER.role,
        "update": upd,
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
    for k in keys:
        if target and secrets.compare_digest(k, target):
            matched = k
            break
    if matched is not None:
        keys.remove(matched)
        if not keys:
            keys.append(f"sk-shard-{secrets.token_hex(8)}")
        save_keys(keys)
        return {"status": "ok", "deleted": target, "keys": keys}
    return JSONResponse(status_code=404, content={"status": "error", "message": "Key not found"})
@app.get("/api/settings")
def api_get_settings(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {
        "models_dir": settings.models_dir(),
        "default_models_dir": settings.DEFAULT_MODELS_DIR,
        "is_custom": settings.is_custom_models_dir(),
        "disk": settings.disk_info()
    }
@app.post("/api/settings")
async def api_set_settings(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    data = await req.json()
    if data.get("reset"):
        path = settings.reset_models_dir()
        return {"status": "ok", "models_dir": path, "is_custom": False,
                "message": "Возвращена папка по умолчанию. Применится после перезапуска приложения"}
    path = (data.get("models_dir") or "").strip()
    try:
        saved = settings.set_models_dir(path)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
    return {"status": "ok", "models_dir": saved, "is_custom": True,
            "disk": settings.disk_info(saved),
            "message": f"Папка моделей сохранена: {saved}. Применится после перезапуска приложения"}
@app.get("/api/drives")
def api_drives(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {
        "drives": settings.available_drives(),
        "current": settings.models_dir(),
        "disk": settings.disk_info()
    }
@app.get("/api/system/update")
def api_system_update_check(req: Request, force: bool = False, prerelease: bool = False):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {
        "status": "ok",
        "install": updater.detect_installation_method(),
        "check": updater.check_for_updates(force=bool(force), timeout=3.5, allow_network=True, allow_prerelease=bool(prerelease)),
        "job": updater.get_update_status(),
    }
@app.post("/api/system/update/apply")
async def api_system_update_apply(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        st = updater.start_update_async(
            target_version=data.get("target_version"),
            force=bool(data.get("force", False)),
            allow_prerelease=bool(data.get("allow_prerelease", False)),
        )
        return {"status": "ok", "job": st}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/system/update/status")
def api_system_update_status(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {"status": "ok", "job": updater.get_update_status(), "install": updater.detect_installation_method()}
@app.post("/api/system/update/rollback")
def api_system_update_rollback(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        res = updater.rollback_update()
        return {"status": "ok", "rollback": res}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/models/browser/roots")
def api_models_browser_roots(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return model_browser.list_roots()
@app.get("/api/models/browser/list")
def api_models_browser_list(req: Request, path: str = None, filter_mode: str = "all", search: str = None, max_entries: int = 500):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        return model_browser.list_directory(raw_path=path, filter_mode=filter_mode, search=search, max_entries=max_entries)
    except PermissionError as e:
        return JSONResponse(status_code=403, content={"status": "error", "message": str(e)})
    except (FileNotFoundError, NotADirectoryError) as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.post("/api/models/browser/inspect")
async def api_models_browser_inspect(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    target = str(data.get("path") or "").strip()
    if not target:
        return JSONResponse(status_code=400, content={"status": "error", "message": "path is required"})
    try:
        return model_browser.inspect_model_target(target, remember=bool(data.get("remember", False)))
    except PermissionError as e:
        return JSONResponse(status_code=403, content={"status": "error", "message": str(e)})
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/fit/{model_id:path}")
def api_fit(model_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    mid = (model_id or "").strip()
    if not mid:
        return JSONResponse(status_code=400, content={"status": "error", "message": "model_id required"})
    try:
        hw = get_hardware_profile()
        return fit_report(mid, hw=hw)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Ошибка оценки: {e}"})
def _backup_and_write(target_path, content_str):
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    if os.path.exists(target_path):
        bak = target_path + ".shard.bak"
        if not os.path.exists(bak):
            try:
                shutil.copy2(target_path, bak)
            except Exception:
                pass
    storage.atomic_write_text(target_path, content_str, fsync=True, backup=False, restrict=True)
def _set_win_user_env(var_map):
    if os.name != "nt":
        return
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_SET_VALUE) as key_handle:
            for k, v in var_map.items():
                winreg.SetValueEx(key_handle, k, 0, winreg.REG_SZ, str(v))
    except Exception:
        pass
@app.post("/api/integrations/apply")
async def api_apply_integration(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    data = await req.json()
    tool = (data.get("tool") or "claude").lower().strip()
    if tool not in _VALID_TOOLS:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Неизвестная интеграция"})
    cur_keys = load_keys()
    key = (data.get("key") or (cur_keys[0] if cur_keys else "sk-shard-local")).strip()
    model = (data.get("model") or engine_instance.active_model_id or "Qwen/Qwen2.5-Coder-1.5B-Instruct").strip()
    if not _SAFE_KEY_RE.match(key):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Недопустимый формат ключа"})
    if not _SAFE_MODEL_RE.match(model) or ".." in model:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Недопустимый формат модели"})
    home = os.path.expanduser("~")
    port = os.environ.get("SHARD_PORT", "8000")
    base_url = f"http://localhost:{port}/v1"
    try:
        if tool == "claude":
            target = os.path.join(home, ".claude", "settings.json")
            existing = {}
            if os.path.exists(target):
                try:
                    with open(target, "r", encoding="utf-8") as tf:
                        existing = json.loads(tf.read())
                except Exception:
                    existing = {}
            existing["hasCompletedOnboarding"] = True
            env_blk = existing.get("env", {}) if isinstance(existing.get("env"), dict) else {}
            env_blk.update({
                "ANTHROPIC_BASE_URL": base_url,
                "ANTHROPIC_AUTH_TOKEN": key,
                "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
                "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": model
            })
            existing["env"] = env_blk
            _backup_and_write(target, json.dumps(existing, indent=2, ensure_ascii=False))
            return {"status": "ok", "tool": tool, "path": target, "message": f"Claude Code настроен на {model}"}
        if tool == "opencode":
            target = os.path.join(home, ".config", "opencode", "config.json")
            cfg = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                    "shard": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "Shard Local Engine",
                        "options": {"baseURL": base_url, "apiKey": key},
                        "models": {
                            model: {"name": model.split("/")[-1], "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 128000, "output": 8192}},
                            "Qwen/Qwen2.5-Coder-1.5B-Instruct": {"name": "Qwen2.5 Coder 1.5B", "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 128000, "output": 8192}},
                            "Qwen/Qwen2.5-Coder-7B-Instruct": {"name": "Qwen2.5 Coder 7B", "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 128000, "output": 8192}},
                            "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B": {"name": "DeepSeek R1 Distill 7B", "modalities": {"input": ["text"], "output": ["text"]}, "limit": {"context": 128000, "output": 8192}}
                        }
                    }
                },
                "model": f"shard/{model}"
            }
            _backup_and_write(target, json.dumps(cfg, indent=2, ensure_ascii=False))
            return {"status": "ok", "tool": tool, "path": target, "message": f"OpenCode настроен на shard/{model}"}
        if tool == "continue":
            target = os.path.join(home, ".continue", "config.json")
            existing = {"models": []}
            if os.path.exists(target):
                try:
                    with open(target, "r", encoding="utf-8") as tf:
                        existing = json.loads(tf.read())
                except Exception:
                    existing = {"models": []}
            models_arr = [m for m in existing.get("models", []) if isinstance(m, dict) and m.get("title") != "Shard Local AI"]
            models_arr.insert(0, {"title": "Shard Local AI", "provider": "openai", "model": model, "apiBase": base_url, "apiKey": key})
            existing["models"] = models_arr
            _backup_and_write(target, json.dumps(existing, indent=2, ensure_ascii=False))
            return {"status": "ok", "tool": tool, "path": target, "message": "Continue IDE подключен к Shard Local AI"}
        if tool == "aider":
            target = os.path.join(home, ".aider.conf.yml")
            yml = f"openai-api-base: {base_url}\nopenai-api-key: {key}\nmodel: openai/{model}\n"
            _backup_and_write(target, yml)
            return {"status": "ok", "tool": tool, "path": target, "message": f"Aider CLI настроен ({target})"}
        if tool == "zed":
            appdata = os.environ.get("APPDATA") or os.path.join(home, ".config")
            target = os.path.join(appdata, "Zed", "settings.json") if os.name == "nt" else os.path.join(home, ".config", "zed", "settings.json")
            existing = {}
            if os.path.exists(target):
                try:
                    with open(target, "r", encoding="utf-8") as tf:
                        existing = json.loads(tf.read())
                except Exception:
                    existing = {}
            lm = existing.get("language_models", {}) if isinstance(existing.get("language_models"), dict) else {}
            lm["openai"] = {"api_url": base_url, "available_models": [{"name": model, "display_name": f"Shard: {model.split('/')[-1]}", "max_tokens": 128000}]}
            existing["language_models"] = lm
            _backup_and_write(target, json.dumps(existing, indent=2, ensure_ascii=False))
            return {"status": "ok", "tool": tool, "path": target, "message": "Zed Editor подключен к локальному серверу Shard"}
        if tool == "codex":
            target = os.path.join(home, ".codex", "config.toml")
            toml_str = f'model = "{model}"\nmodel_provider = "shard"\n[model_providers.shard]\nname = "Shard Local"\nbase_url = "{base_url}"\nenv_key = "OPENAI_API_KEY"\n'
            _backup_and_write(target, toml_str)
            _set_win_user_env({"OPENAI_API_KEY": key})
            return {"status": "ok", "tool": tool, "path": target, "message": "Codex CLI настроен на провайдер Shard Local"}
        if tool in ("openrouter", "cursor", "env", "sdk"):
            env_path = os.path.join(str(paths.config_dir()), "shard_local_bridge.env")
            env_content = f"OPENAI_API_BASE={base_url}\nOPENAI_BASE_URL={base_url}\nOPENROUTER_BASE_URL={base_url}\nOPENAI_API_KEY={key}\nOPENROUTER_API_KEY={key}\nSHARD_MODEL={model}\n"
            _backup_and_write(env_path, env_content)
            _set_win_user_env({
                "OPENAI_API_BASE": base_url,
                "OPENAI_BASE_URL": base_url,
                "OPENAI_API_KEY": key,
                "ANTHROPIC_BASE_URL": base_url,
                "ANTHROPIC_AUTH_TOKEN": key
            })
            return {"status": "ok", "tool": tool, "path": env_path, "message": "Переменные окружения и локальный роутер активированы"}
        return JSONResponse(status_code=400, content={"status": "error", "message": f"Неизвестная интеграция: {tool}"})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e)})
def _client_rate_key(req):
    tpeer = net_tunnel.tunnel_peer_id(req)
    if tpeer:
        return f"peer:{tpeer}"
    tok = _extract_bearer(req)
    if tok:
        return f"tok:{hashlib.sha256(tok.encode('utf-8')).hexdigest()[:12]}"
    ip = req.client.host if req.client else "unknown"
    return f"ip:{ip}"
def _inference_stream_factory(messages, max_tokens, temperature):
    if CLUSTER.is_ready():
        return lambda cancel_ev: CLUSTER.generate_stream(messages, max_tokens, temperature, cancel_check=cancel_ev.is_set)
    return lambda cancel_ev: engine_instance.generate_stream(messages, max_tokens, temperature, cancel_check=cancel_ev.is_set)
@app.get("/v1/models")
def list_models():
    st = engine_instance.get_status()
    cls_model = CLUSTER.target_model if CLUSTER.role == "coordinator" and CLUSTER.target_model else None
    current = cls_model or st.get("active_model") or "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    cached_list = sorted(list(get_local_cached_ids()))
    popular = [
        current,
        *cached_list,
        "Qwen/Qwen2.5-Coder-1.5B-Instruct",
        "Qwen/Qwen2.5-Coder-7B-Instruct",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-1.5B",
        "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",
        "meta-llama/Llama-3.2-3B-Instruct",
        "google/gemma-2-9b-it",
        "mistralai/Mistral-7B-Instruct-v0.3"
    ]
    seen = set()
    model_list = []
    for m in popular:
        if m and m.lower() not in seen:
            seen.add(m.lower())
            model_list.append({
                "id": m,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "shard-cluster" if (cls_model and m == cls_model) else "shard-local",
            })
    return {"object": "list", "data": model_list}
@app.get("/v1/ping")
def api_ping():
    cls_ready = CLUSTER.is_ready()
    is_loaded = cls_ready or engine_instance.model is not None or (engine_instance.gguf is not None and engine_instance.gguf.is_running())
    return {
        "status": "ok",
        "version": __version__,
        "latency_ms": 0.3,
        "engine": "shard-distributed-cluster" if cls_ready else "shard-local-engine",
        "active_model": CLUSTER.target_model if cls_ready else engine_instance.active_model_id,
        "is_loaded": is_loaded,
        "cluster_state": CLUSTER.state,
        "cluster_ready": cls_ready,
        "node_id": _safe_node_id(),
    }
@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    data = await req.json()
    messages = data.get("messages", [])
    stream = data.get("stream", False)
    max_tokens = data.get("max_tokens", 512)
    temperature = data.get("temperature", 0.7)
    default_model = CLUSTER.target_model if CLUSTER.is_ready() else (engine_instance.active_model_id or "shard-local-model")
    model = data.get("model") or default_model
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    client_key = _client_rate_key(req)
    factory = _inference_stream_factory(messages, max_tokens, temperature)
    if stream:
        async def event_generator():
            try:
                async for item in SCHEDULER.stream_async(
                    factory,
                    client_key=client_key,
                    model_id=model,
                    is_disconnected=req.is_disconnected,
                ):
                    if item.get("type") == "thought":
                        delta_payload = {
                            "id": cid,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {"reasoning_content": item["token"]}, "finish_reason": None}]
                        }
                        yield f"data: {json.dumps(delta_payload, ensure_ascii=False)}\n\n"
                    elif item.get("type") == "content":
                        delta_payload = {
                            "id": cid,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {"content": item["token"]}, "finish_reason": None}]
                        }
                        yield f"data: {json.dumps(delta_payload, ensure_ascii=False)}\n\n"
                    elif item.get("type") == "error":
                        err_payload = {"error": {"message": item.get("error") or "Inference error", "type": "server_error"}}
                        yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\n"
                    elif item.get("type") == "done":
                        final_payload = {
                            "id": cid,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            "telemetry": item.get("telemetry", {}),
                            "usage": item.get("usage", {})
                        }
                        yield f"data: {json.dumps(final_payload, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
            except RateLimitError as e:
                yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'rate_limit_error'}})}\n\n"
            except QueueFullError as e:
                yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'server_busy'}})}\n\n"
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    try:
        result = await SCHEDULER.run_sync_async(
            factory,
            client_key=client_key,
            model_id=model,
            is_disconnected=req.is_disconnected,
        )
    except RateLimitError as e:
        return JSONResponse(status_code=429, headers={"Retry-After": str(e.retry_after)}, content={"error": {"message": str(e), "type": "rate_limit_error"}})
    except QueueFullError as e:
        return JSONResponse(status_code=503, content={"error": {"message": str(e), "type": "server_busy"}})
    except TimeoutError as e:
        return JSONResponse(status_code=504, content={"error": {"message": str(e), "type": "timeout_error"}})
    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": result["content"],
                    "reasoning_content": result.get("reasoning_content", "")
                },
                "finish_reason": "stop"
            }
        ],
        "telemetry": result.get("telemetry", {}),
        "usage": result.get("usage", {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    }
@app.post("/v1/messages")
async def anthropic_messages(req: Request):
    data = await req.json()
    raw_messages = data.get("messages", [])
    sys_prompt = data.get("system", "")
    default_model = CLUSTER.target_model if CLUSTER.is_ready() else (engine_instance.active_model_id or "Qwen/Qwen2.5-Coder-1.5B-Instruct")
    model = data.get("model") or default_model
    max_tokens = data.get("max_tokens", 1024)
    temperature = data.get("temperature", 0.7)
    stream = data.get("stream", False)
    normalized = []
    if sys_prompt:
        if isinstance(sys_prompt, list):
            sys_txt = " ".join(c.get("text", "") for c in sys_prompt if isinstance(c, dict))
        else:
            sys_txt = str(sys_prompt)
        if sys_txt.strip():
            normalized.append({"role": "system", "content": sys_txt})
    for m in raw_messages:
        content = m.get("content", "")
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            content = " ".join(parts)
        normalized.append({"role": m.get("role", "user"), "content": content})
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    client_key = _client_rate_key(req)
    factory = _inference_stream_factory(normalized, max_tokens, temperature)
    if stream:
        async def anthropic_sse():
            start_ev = {"type": "message_start", "message": {"id": msg_id, "type": "message", "role": "assistant", "content": [], "model": model, "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0}}}
            yield f"event: message_start\ndata: {json.dumps(start_ev, ensure_ascii=False)}\n\n"
            blk_start = {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}
            yield f"event: content_block_start\ndata: {json.dumps(blk_start, ensure_ascii=False)}\n\n"
            out_toks = 0
            try:
                async for item in SCHEDULER.stream_async(
                    factory,
                    client_key=client_key,
                    model_id=model,
                    is_disconnected=req.is_disconnected,
                ):
                    if item.get("type") in ("content", "thought") and item.get("token"):
                        delta_ev = {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": item["token"]}}
                        yield f"event: content_block_delta\ndata: {json.dumps(delta_ev, ensure_ascii=False)}\n\n"
                    elif item.get("type") == "done":
                        out_toks = item.get("usage", {}).get("completion_tokens", 0)
            except (RateLimitError, QueueFullError) as e:
                err_ev = {"type": "error", "error": {"type": "overloaded_error", "message": str(e)}}
                yield f"event: error\ndata: {json.dumps(err_ev, ensure_ascii=False)}\n\n"
            yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
            msg_delta = {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": out_toks}}
            yield f"event: message_delta\ndata: {json.dumps(msg_delta)}\n\n"
            yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
        return StreamingResponse(anthropic_sse(), media_type="text/event-stream")
    try:
        result = await SCHEDULER.run_sync_async(
            factory,
            client_key=client_key,
            model_id=model,
            is_disconnected=req.is_disconnected,
        )
    except RateLimitError as e:
        return JSONResponse(status_code=429, headers={"Retry-After": str(e.retry_after)}, content={"error": {"message": str(e), "type": "rate_limit_error"}})
    except QueueFullError as e:
        return JSONResponse(status_code=503, content={"error": {"message": str(e), "type": "overloaded_error"}})
    except TimeoutError as e:
        return JSONResponse(status_code=504, content={"error": {"message": str(e), "type": "timeout_error"}})
    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": result["content"]}],
        "model": model,
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": result.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": result.get("usage", {}).get("completion_tokens", 0)
        }
    }
@app.post("/v1/messages/count_tokens")
async def anthropic_count_tokens(req: Request):
    data = await req.json()
    raw_messages = data.get("messages", [])
    count = sum(len(str(m.get("content", "")).split()) for m in raw_messages)
    return {"input_tokens": max(1, count)}
@app.get("/i")
def installer_script(req: Request, tool: str = "claude", os: str = "linux", key: str = ""):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    tool_clean = tool.lower().strip()
    os_clean = os.lower().strip()
    if tool_clean not in _VALID_TOOLS:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Неизвестный инструмент"})
    cur_keys = load_keys()
    chosen_key = key.strip() if key else (cur_keys[0] if cur_keys else "sk-shard-local")
    act_model = engine_instance.active_model_id or "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    if not _SAFE_KEY_RE.match(chosen_key) or not _SAFE_MODEL_RE.match(act_model):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid parameters"})
    port = __import__("os").environ.get("SHARD_PORT", "8000")
    base_v1 = f"http://localhost:{port}/v1"
    if os_clean == "windows":
        if tool_clean == "claude":
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + chosen_key + '" }\n' + \
                     '$p = Join-Path $HOME ".claude"\n' + \
                     'if (!(Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }\n' + \
                     '$cfg = @{\n' + \
                     '  hasCompletedOnboarding = $true\n' + \
                     '  env = @{\n' + \
                     '    ANTHROPIC_BASE_URL = "' + base_v1 + '"\n' + \
                     '    ANTHROPIC_AUTH_TOKEN = $k\n' + \
                     '    ANTHROPIC_DEFAULT_OPUS_MODEL = "' + act_model + '"\n' + \
                     '    ANTHROPIC_DEFAULT_SONNET_MODEL = "' + act_model + '"\n' + \
                     '    ANTHROPIC_DEFAULT_HAIKU_MODEL = "' + act_model + '"\n' + \
                     '  }\n' + \
                     '} | ConvertTo-Json -Depth 5\n' + \
                     'Set-Content -Path (Join-Path $p "settings.json") -Value $cfg -Encoding UTF8\n' + \
                     'Write-Host "[OK] Shard: Claude CLI configured successfully on Windows!" -ForegroundColor Green\n'
        elif tool_clean == "opencode":
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + chosen_key + '" }\n' + \
                     '$p = Join-Path $HOME ".config\\opencode"\n' + \
                     'if (!(Test-Path $p)) { New-Item -ItemType Directory -Path $p -Force | Out-Null }\n' + \
                     '$cfg = @{\n' + \
                     '  \'$schema\' = "https://opencode.ai/config.json"\n' + \
                     '  provider = @{\n' + \
                     '    shard = @{\n' + \
                     '      npm = "@ai-sdk/openai-compatible"\n' + \
                     '      name = "Shard"\n' + \
                     '      options = @{\n' + \
                     '        baseURL = "' + base_v1 + '"\n' + \
                     '        apiKey = $k\n' + \
                     '      }\n' + \
                     '      models = @{\n' + \
                     '        "' + act_model + '" = @{ name = "' + act_model.split("/")[-1] + '"; modalities = @{ input = @("text"); output = @("text") }; limit = @{ context = 128000; output = 8192 } }\n' + \
                     '        "Qwen/Qwen2.5-Coder-7B-Instruct" = @{ name = "Qwen2.5 Coder 7B"; modalities = @{ input = @("text"); output = @("text") }; limit = @{ context = 128000; output = 8192 } }\n' + \
                     '        "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B" = @{ name = "DeepSeek R1 Distill 7B"; modalities = @{ input = @("text"); output = @("text") }; limit = @{ context = 128000; output = 8192 } }\n' + \
                     '      }\n' + \
                     '    }\n' + \
                     '  }\n' + \
                     '  model = "shard/' + act_model + '"\n' + \
                     '} | ConvertTo-Json -Depth 6\n' + \
                     'Set-Content -Path (Join-Path $p "config.json") -Value $cfg -Encoding UTF8\n' + \
                     'Write-Host "[OK] Shard: OpenCode configured successfully on Windows!" -ForegroundColor Green\n'
        else:
            script = '$k = if ($env:SHARD_KEY) { $env:SHARD_KEY } else { "' + chosen_key + '" }\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_API_BASE", "' + base_v1 + '", "User")\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_BASE_URL", "' + base_v1 + '", "User")\n' + \
                     '[Environment]::SetEnvironmentVariable("OPENAI_API_KEY", $k, "User")\n' + \
                     'Write-Host "[OK] Shard: Environment variables set for ' + tool_clean + ' on Windows!" -ForegroundColor Green\n'
        return PlainTextResponse(script, media_type="text/plain; charset=utf-8")
    if tool_clean == "claude":
        script = '#!/usr/bin/env bash\n' + \
                 'KEY="${1:-' + chosen_key + '}"\n' + \
                 'mkdir -p "$HOME/.claude"\n' + \
                 'cat << \'EOF\' > "$HOME/.claude/settings.json"\n' + \
                 '{\n' + \
                 '  "hasCompletedOnboarding": true,\n' + \
                 '  "env": {\n' + \
                 '    "ANTHROPIC_BASE_URL": "' + base_v1 + '",\n' + \
                 '    "ANTHROPIC_AUTH_TOKEN": "KEY_PLACEHOLDER",\n' + \
                 '    "ANTHROPIC_DEFAULT_OPUS_MODEL": "' + act_model + '",\n' + \
                 '    "ANTHROPIC_DEFAULT_SONNET_MODEL": "' + act_model + '",\n' + \
                 '    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "' + act_model + '"\n' + \
                 '  }\n' + \
                 '}\n' + \
                 'EOF\n' + \
                 'sed -i "s/KEY_PLACEHOLDER/$KEY/g" "$HOME/.claude/settings.json"\n' + \
                 'echo "[OK] Shard: Claude CLI settings.json configured successfully."\n'
    elif tool_clean == "opencode":
        script = '#!/usr/bin/env bash\n' + \
                 'KEY="${1:-' + chosen_key + '}"\n' + \
                 'mkdir -p "$HOME/.config/opencode"\n' + \
                 'cat << \'EOF\' > "$HOME/.config/opencode/config.json"\n' + \
                 '{\n' + \
                 '  "$schema": "https://opencode.ai/config.json",\n' + \
                 '  "provider": {\n' + \
                 '    "shard": {\n' + \
                 '      "npm": "@ai-sdk/openai-compatible",\n' + \
                 '      "name": "Shard",\n' + \
                 '      "options": {\n' + \
                 '        "baseURL": "' + base_v1 + '",\n' + \
                 '        "apiKey": "KEY_PLACEHOLDER"\n' + \
                 '      },\n' + \
                 '      "models": {\n' + \
                 '        "' + act_model + '": { "name": "' + act_model.split("/")[-1] + '", "modalities": {"input":["text"],"output":["text"]}, "limit": {"context":128000,"output":8192} },\n' + \
                 '        "Qwen/Qwen2.5-Coder-7B-Instruct": { "name": "Qwen2.5 Coder 7B", "modalities": {"input":["text"],"output":["text"]}, "limit": {"context":128000,"output":8192} }\n' + \
                 '      }\n' + \
                 '    }\n' + \
                 '  },\n' + \
                 '  "model": "shard/' + act_model + '"\n' + \
                 '}\n' + \
                 'EOF\n' + \
                 'sed -i "s/KEY_PLACEHOLDER/$KEY/g" "$HOME/.config/opencode/config.json"\n' + \
                 'echo "[OK] Shard: OpenCode config.json configured successfully."\n'
    else:
        script = '#!/usr/bin/env bash\n' + \
                 'KEY="${1:-' + chosen_key + '}"\n' + \
                 'export OPENAI_API_BASE="' + base_v1 + '"\n' + \
                 'export OPENAI_BASE_URL="' + base_v1 + '"\n' + \
                 'export OPENAI_API_KEY="$KEY"\n' + \
                 'echo "[OK] Shard: OPENAI_API_BASE and OPENAI_API_KEY exported for ' + tool_clean + '."\n'
    return PlainTextResponse(script, media_type="text/plain; charset=utf-8")
def _verify_server_model_ready(model_id=None):
    try:
        from .distributed import cluster as dist_cluster
        cm = dist_cluster.get_cluster_manager()
        c_st = cm.status(include_secrets=False)
        if c_st.get("role") == "coordinator" and c_st.get("state") in ("PLANNING", "WAITING_WORKERS", "READY", "RUNNING"):
            t = (model_id or "").strip()
            if not t or t == c_st.get("target_model"):
                return True, ""
    except Exception:
        pass
    if not engine_instance.is_loaded():
        return False, "No model is loaded in memory. Load and verify a model before hosting or publishing."
    target = (model_id or "").strip() or engine_instance.active_model_id or engine_instance.current_loaded_id
    if target and not engine_instance.is_model_active(target):
        return False, f"Model '{target}' is not active in memory (active: '{engine_instance.current_loaded_id or engine_instance.active_model_id}')."
    state = engine_instance.detailed_lifecycle_state(target)
    if state != "READY":
        return False, f"Model lifecycle state is '{state}', expected 'READY'."
    try:
        res = engine_instance.generate_sync([{"role": "user", "content": "ping"}], max_tokens=8, temperature=0.0)
        if not res or ("content" not in res and "reasoning_content" not in res):
            return False, "Model self-test inference returned an invalid payload."
    except Exception as e:
        return False, f"Model self-test inference failed: {str(e)}"
    return True, ""
@app.post("/api/lan/create")
async def api_lan_create(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    data = await req.json()
    model = (data.get("model") or "").strip() or engine_instance.active_model_id or engine_instance.current_loaded_id
    ok, reason = _verify_server_model_ready(model)
    if not ok:
        return JSONResponse(status_code=409, content={"status": "error", "code": "MODEL_NOT_READY", "message": f"Model must be loaded and pass inference verification before hosting or publishing a server: {reason}"})
    st = LAN.create((data.get("name") or "").strip() or None,
                    open_net=bool(data.get("open")),
                    model=model)
    return {"status": "ok", **st}
@app.post("/api/lan/model")
async def api_lan_model(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    data = await req.json()
    model = (data.get("model") or "").strip()
    ok, reason = _verify_server_model_ready(model)
    if not ok:
        return JSONResponse(status_code=409, content={"status": "error", "code": "MODEL_NOT_READY", "message": f"Model must be loaded and pass inference verification before hosting or publishing a server: {reason}"})
    return LAN.set_model(model)
@app.post("/api/lan/open")
async def api_lan_open(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    data = await req.json()
    return LAN.set_open(bool(data.get("open")))
@app.post("/api/lan/join")
async def api_lan_join(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    data = await req.json()
    return LAN.join((data.get("ip") or "").strip(), data.get("code") or "",
                    share=bool(data.get("share")),
                    port=int(data.get("port") or DEFAULT_MASTER_PORT))
@app.post("/api/lan/pair")
async def api_lan_pair(req: Request):
    ip = req.client.host if req.client else "unknown"
    ok_tot, retry_tot = _PAIR_TOTAL_LIMITER.check(ip)
    if not ok_tot:
        return JSONResponse(status_code=429, headers={"Retry-After": str(retry_tot)}, content={"status": "error", "message": "Too many pairing attempts"})
    ok_fail, retry_fail = _PAIR_FAIL_LIMITER.check(ip, record=False)
    if not ok_fail:
        return JSONResponse(status_code=429, headers={"Retry-After": str(retry_fail)}, content={"status": "error", "message": "Pairing temporarily rate-limited"})
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    payload, err = LAN.handle_pair(
        data.get("code") or "",
        ip,
        (data.get("name") or "").strip() or None,
        share=bool(data.get("share")),
        client_id=data.get("client_id"),
    )
    if err:
        _PAIR_FAIL_LIMITER.record(ip)
        return JSONResponse(status_code=403, content={"status": "error", "message": err})
    return {"status": "ok", **payload}
@app.post("/api/lan/heartbeat")
async def api_lan_heartbeat(req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    ip = req.client.host if req.client else ""
    ok = LAN.handle_heartbeat(
        ip,
        data.get("token") or "",
        hw=data.get("hw"),
        rpc_port=data.get("rpc_port"),
        client_id=data.get("client_id"),
    )
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "mode": LAN.mode(), "message": "Участник не найден или токен отклонён"})
    return {"status": "ok", "mode": LAN.mode()}
@app.post("/api/lan/leave")
async def api_lan_leave(req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    tok = str(data.get("token") or "").strip()
    if not tok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется токен участника"})
    ip = req.client.host if req.client else ""
    dropped = LAN.handle_leave(ip, tok, client_id=data.get("client_id"))
    if not dropped:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Недействительный токен участника"})
    return {"status": "ok", "dropped": True}
@app.post("/api/lan/off")
def api_lan_off(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return LAN.off()
@app.post("/api/lan/compute/consent")
async def api_lan_compute_consent(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    return LAN.set_compute_consent(
        opt_in=bool(data.get("opt_in", True)),
        max_vram_gb=data.get("max_vram_gb"),
        max_threads=data.get("max_threads"),
    )
@app.post("/api/lan/compute/opt_out")
def api_lan_compute_opt_out(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return LAN.revoke_compute_consent()
@app.get("/api/lan/compute/status")
def api_lan_compute_status(req: Request):
    return LAN.compute_status()
@app.get("/api/lan/status")
def api_lan_status(req: Request):
    if _is_owner_request(req):
        return LAN.status(include_secrets=True)
    tok = _extract_bearer(req)
    if LAN.is_server() and _token_in_pool(tok, LAN.tokens()):
        return LAN.status(include_secrets=False)
    return JSONResponse(status_code=403, content={"status": "error", "message": "Owner authorization or LAN token required"})
@app.get("/api/lan/invite")
def api_lan_invite(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return LAN.build_invite()
@app.get("/api/lan/diagnose")
def api_lan_diagnose(req: Request, public: bool = False):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    from . import lan as _lan_mod
    return _lan_mod.connectivity_report(probe_public=bool(public))
@app.get("/api/lan/preview")
def api_lan_preview():
    return LAN.preview_info()
@app.post("/api/lan/members/kick")
async def api_lan_member_kick(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    target = str(data.get("target") or data.get("client_id") or data.get("ip") or "").strip()
    if not target:
        return JSONResponse(status_code=400, content={"status": "error", "message": "target required"})
    return LAN.kick_member(target, block=bool(data.get("block", False)))
@app.post("/api/lan/members/unblock")
async def api_lan_member_unblock(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    target = str(data.get("target") or "").strip()
    if not target:
        return JSONResponse(status_code=400, content={"status": "error", "message": "target required"})
    return LAN.unblock_member(target)
def _is_cluster_authorized(req):
    if _is_owner_request(req):
        return True, True
    if net_tunnel.is_tunnel_request(req):
        return True, False
    tok = _extract_bearer(req)
    if tok:
        if _token_in_pool(tok, LAN.tokens() | set(load_keys())):
            return True, False
        worker_toks = {w.get("worker_token") for w in CLUSTER.workers.values() if w.get("worker_token")}
        if _token_in_pool(tok, worker_toks):
            return True, False
    return False, False
@app.post("/api/cluster/create")
async def api_cluster_create(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    model_id = str(data.get("model") or data.get("model_id") or engine_instance.active_model_id or "").strip()
    if not model_id:
        return JSONResponse(status_code=400, content={"status": "error", "message": "model_id is required"})
    try:
        st = await asyncio.to_thread(
            CLUSTER.create_cluster,
            name=data.get("name"),
            model_id=model_id,
            strategy=data.get("strategy", "auto"),
            ctx_tokens=int(data.get("ctx_tokens") or 4096),
            parallel_slots=int(data.get("parallel_slots") or 4),
            access_mode=data.get("access_mode") or "invite_only",
            open_net=bool(data.get("open", False)),
            coordinator_contributes=bool(data.get("coordinator_contributes", True)),
            coordinator_quota=data.get("coordinator_quota"),
            auto_deploy=bool(data.get("auto_deploy", True)),
            fetch_model_meta=bool(data.get("fetch_model_meta", True)),
        )
        return {"status": "ok", "cluster": st}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.post("/api/cluster/stop")
def api_cluster_stop(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return CLUSTER.stop_cluster()
@app.post("/api/cluster/deploy")
async def api_cluster_deploy(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        st = await asyncio.to_thread(CLUSTER.deploy_cluster, verify=bool(data.get("verify", True)))
        return {"status": "ok", "cluster": st}
    except Exception as e:
        return JSONResponse(status_code=409, content={"status": "error", "message": str(e)})
@app.post("/api/cluster/rebalance")
async def api_cluster_rebalance(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    plan = CLUSTER.recompute_plan()
    if not plan.get("feasible"):
        return JSONResponse(status_code=409, content={"status": "error", "message": plan.get("reason"), "plan": plan})
    try:
        st = await asyncio.to_thread(CLUSTER.deploy_cluster, verify=True)
        return {"status": "ok", "cluster": st}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": str(e), "plan": plan})
@app.get("/api/cluster/preview")
def api_cluster_preview():
    return CLUSTER.preview_cluster()
@app.get("/api/cluster/status")
def api_cluster_status(req: Request):
    ok, is_owner = _is_cluster_authorized(req)
    if not ok:
        return JSONResponse(status_code=403, content={"status": "error", "message": "Authorization required"})
    return CLUSTER.status(include_secrets=is_owner)
@app.get("/api/cluster/plan")
def api_cluster_plan(req: Request):
    ok, _ = _is_cluster_authorized(req)
    if not ok:
        return JSONResponse(status_code=403, content={"status": "error", "message": "Authorization required"})
    return {"status": "ok", "plan": CLUSTER.recompute_plan()}
@app.get("/api/cluster/telemetry")
def api_cluster_telemetry(req: Request):
    ok, _ = _is_cluster_authorized(req)
    if not ok:
        return JSONResponse(status_code=403, content={"status": "error", "message": "Authorization required"})
    return {"status": "ok", "telemetry": SCHEDULER.telemetry(), "cluster_state": CLUSTER.state}
@app.post("/api/cluster/workers/register")
async def api_cluster_worker_register(req: Request):
    ip = req.client.host if req.client else "unknown"
    ok_tot, retry_tot = _PAIR_TOTAL_LIMITER.check(ip)
    if not ok_tot:
        return JSONResponse(status_code=429, headers={"Retry-After": str(retry_tot)}, content={"status": "error", "message": "Too many registration attempts"})
    ok_fail, retry_fail = _PAIR_FAIL_LIMITER.check(ip, record=False)
    if not ok_fail:
        return JSONResponse(status_code=429, headers={"Retry-After": str(retry_fail)}, content={"status": "error", "message": "Registration temporarily rate-limited"})
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    tpeer = net_tunnel.tunnel_peer_id(req)
    try:
        res = CLUSTER.register_worker(
            worker_id=data.get("worker_id"),
            name=data.get("name"),
            hw=data.get("hw"),
            backends=data.get("backends"),
            quota=data.get("quota"),
            consented=bool(data.get("consented", False)),
            auth_code=data.get("auth_code") or data.get("code") or "",
            rpc_port=data.get("rpc_port"),
            pipeline_port=data.get("pipeline_port"),
            rpc_host=data.get("rpc_host"),
            pipeline_host=data.get("pipeline_host"),
            transport=data.get("transport") or ("tunnel" if tpeer else "lan"),
            tunnel_peer=tpeer,
            remote_ip=ip,
        )
        return {"status": "ok", **res}
    except PermissionError as e:
        _PAIR_FAIL_LIMITER.record(ip)
        return JSONResponse(status_code=403, content={"status": "error", "message": str(e)})
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.post("/api/cluster/workers/heartbeat")
async def api_cluster_worker_heartbeat(req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    ok, res = CLUSTER.heartbeat_worker(
        worker_id=data.get("worker_id"),
        worker_token=data.get("worker_token") or _extract_bearer(req),
        hw=data.get("hw"),
        quota=data.get("quota"),
        workload=data.get("workload"),
        rtt_ms=data.get("rtt_ms"),
        bandwidth_mbps=data.get("bandwidth_mbps"),
    )
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": str(res)})
    return {"status": "ok", **res}
@app.post("/api/cluster/workers/leave")
async def api_cluster_worker_leave(req: Request):
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    ok = CLUSTER.leave_worker(
        worker_id=data.get("worker_id"),
        worker_token=data.get("worker_token") or _extract_bearer(req),
    )
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Invalid worker credentials"})
    return {"status": "ok", "left": True}
@app.post("/api/cluster/workers/{worker_id}/kick")
async def api_cluster_worker_kick(worker_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    return CLUSTER.kick_worker(worker_id, block=bool(data.get("block", False)))
@app.post("/api/cluster/worker/join")
async def api_cluster_local_worker_join(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    coord_url = str(data.get("coordinator_url") or "").strip()
    if not coord_url:
        return JSONResponse(status_code=400, content={"status": "error", "message": "coordinator_url required"})
    try:
        res = await asyncio.to_thread(
            CLUSTER.join_cluster_as_worker,
            coordinator_url=coord_url,
            auth_code=data.get("auth_code") or data.get("code") or "",
            name=data.get("name"),
            quota=data.get("quota"),
            consented=bool(data.get("consented", False)),
            start_rpc=bool(data.get("start_rpc", True)),
            start_pipeline=bool(data.get("start_pipeline", True)),
            via_tunnel=bool(data.get("via_tunnel", False)),
        )
        return res
    except PermissionError as e:
        return JSONResponse(status_code=403, content={"status": "error", "message": str(e)})
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.post("/api/cluster/worker/leave")
def api_cluster_local_worker_leave(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return CLUSTER.stop_local_worker()
def _extract_bearer(req):
    auth = req.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth.strip()
def _is_owner_request(req):
    if getattr(req, "scope", {}).get("shard_tunnel"):
        return False
    if "x-shard-tunnel-peer" in req.headers or "x-shard-bridge-token" in req.headers:
        return False
    owner_hdr = (req.headers.get("x-shard-owner-token") or "").strip()
    client_ip = req.client.host if req.client else ""
    if owner_hdr:
        allowed, _ = _OWNER_FAIL_LIMITER.check(client_ip, record=False)
        if not allowed:
            return False
        if training.verify_owner_token(owner_hdr):
            return True
        _OWNER_FAIL_LIMITER.record(client_ip)
        return False
    if _has_proxy_headers(req):
        return False
    return is_loopback(client_ip)
def _deny_non_owner(req):
    if _is_owner_request(req):
        return None
    return JSONResponse(status_code=403, content={"status": "error", "message": "Owner authorization required"})
def _contributor_identity(req, payload=None):
    is_owner = _is_owner_request(req)
    if is_owner and isinstance(payload, dict) and payload.get("contributor_id"):
        return str(payload.get("contributor_id"))
    tpeer = net_tunnel.tunnel_peer_id(req)
    if tpeer:
        return f"peer:{tpeer}"
    tok = _extract_bearer(req)
    valid_pool = set(load_keys()) | LAN.tokens()
    if tok and _token_in_pool(tok, valid_pool):
        return f"token:{tok}"
    client_ip = req.client.host if req.client else "unknown"
    return f"ip:{client_ip}"
@app.get("/api/feedback/policy")
def api_feedback_policy(req: Request):
    cfg = feedback.get_privacy_config()
    reg = training.list_model_versions()
    is_owner = _is_owner_request(req)
    cid = feedback.pseudonymize_contributor(_contributor_identity(req))
    return {
        "allow_feedback": bool(cfg.get("allow_feedback", True)),
        "allow_remote_feedback": bool(cfg.get("allow_remote_feedback", False)),
        "require_explicit_consent": bool(cfg.get("require_explicit_consent", True)),
        "require_manual_review": bool(cfg.get("require_manual_review", True)),
        "redact_secrets": bool(cfg.get("redact_secrets", True)),
        "redact_pii": bool(cfg.get("redact_pii", True)),
        "store_raw_conversations": False,
        "active_model": engine_instance.active_model_id,
        "active_version_id": reg.get("active_version_id") or "base",
        "is_owner": is_owner,
        "opted_out": feedback.is_contributor_opted_out(cid),
    }
@app.post("/api/feedback/submit")
async def api_feedback_submit(req: Request):
    ip = req.client.host if req.client else "unknown"
    ok_fb, retry_fb = _FEEDBACK_LIMITER.check(ip)
    if not ok_fb:
        return JSONResponse(status_code=429, headers={"Retry-After": str(retry_fb)}, content={"status": "error", "message": "Too many feedback requests"})
    is_owner = _is_owner_request(req)
    if not is_owner and LAN.is_server() and not net_tunnel.is_tunnel_request(req):
        tok = _extract_bearer(req)
        if not _token_in_pool(tok, LAN.tokens()):
            return JSONResponse(status_code=401, content={"status": "error", "message": "Valid LAN participant token required"})
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    src = "local" if is_owner else "remote"
    hint = _contributor_identity(req, data if is_owner else None)
    if not is_owner and isinstance(data, dict):
        data = dict(data)
        data.pop("contributor_id", None)
    try:
        entry = feedback.submit_feedback(data, source=src, client_hint=hint)
        return {"status": "ok", "id": entry["id"], "entry_status": entry["status"], "redactions": entry["redactions"]}
    except PermissionError as e:
        return JSONResponse(status_code=403, content={"status": "error", "message": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/feedback/my")
def api_feedback_my(req: Request, contributor_id: str = None):
    is_owner = _is_owner_request(req)
    if not is_owner and LAN.is_server() and not net_tunnel.is_tunnel_request(req):
        tok = _extract_bearer(req)
        if not _token_in_pool(tok, LAN.tokens()):
            return JSONResponse(status_code=401, content={"status": "error", "message": "Valid LAN participant token required"})
    cid = contributor_id if (is_owner and contributor_id) else _contributor_identity(req)
    return feedback.list_contributor_submissions(cid)
@app.post("/api/feedback/revoke")
async def api_feedback_revoke(req: Request):
    ip = req.client.host if req.client else "unknown"
    ok_fb, retry_fb = _FEEDBACK_LIMITER.check(ip)
    if not ok_fb:
        return JSONResponse(status_code=429, headers={"Retry-After": str(retry_fb)}, content={"status": "error", "message": "Too many requests"})
    is_owner = _is_owner_request(req)
    if not is_owner and LAN.is_server() and not net_tunnel.is_tunnel_request(req):
        tok = _extract_bearer(req)
        if not _token_in_pool(tok, LAN.tokens()):
            return JSONResponse(status_code=401, content={"status": "error", "message": "Valid LAN participant token required"})
    try:
        data = await req.json()
    except Exception:
        data = {}
    cid = data.get("contributor_id") if (is_owner and data.get("contributor_id")) else _contributor_identity(req)
    res = feedback.revoke_contributor(cid, delete_existing=bool(data.get("delete_existing", True)))
    return {"status": "ok", **res}
@app.get("/api/training/overview")
def api_training_overview(req: Request, check_remote: bool = False):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return training.training_overview(check_remote=bool(check_remote))
@app.get("/api/training/soup")
def api_training_soup(req: Request, check_remote: bool = False):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return soup_adapter.detect_soup(probe_deps=True, check_remote=bool(check_remote))
@app.post("/api/training/soup/install")
async def api_training_soup_install(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    action = str(data.get("action") or ("repair" if data.get("force_recreate") else "install")).strip().lower()
    try:
        st = soup_adapter.install_or_repair_soup(
            action=action,
            custom_python=data.get("python_executable"),
            background=bool(data.get("background", True)),
            include_train=bool(data.get("include_train", True)),
        )
        return {"status": "ok", "installer": st}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/training/soup/install/status")
def api_training_soup_install_status(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {"status": "ok", "installer": soup_adapter.get_soup_install_status(), "soup": soup_adapter.detect_soup(probe_deps=False)}
@app.post("/api/training/soup/install/cancel")
def api_training_soup_install_cancel(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {"status": "ok", "installer": soup_adapter.cancel_soup_install()}
@app.get("/api/training/servers")
def api_training_servers(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    servers = []
    host_st = _net_mgr().host_status()
    lan_st = LAN.status(include_secrets=False)
    active_m = engine_instance.active_model_id or engine_instance.current_loaded_id
    if LAN.is_server() or host_st.get("published") or engine_instance.is_loaded():
        srv_name = LAN.name() or host_st.get("display_name") or "Local Server"
        hf_map = model_browser.map_gguf_to_hf_base(active_m) if active_m else {}
        suggested_hf = hf_map.get("suggested_hf_base_repo")
        trainable = bool(suggested_hf or (active_m and not str(active_m).lower().endswith(".gguf")))
        incompat_reason = None
        if not trainable and active_m:
            incompat_reason = "GGUF quantized weights cannot be trained directly with SOUP. A corresponding Hugging Face 16-bit PyTorch base repository is required."
        servers.append({
            "id": "srv_primary",
            "name": srv_name,
            "mode": "lan" if LAN.is_server() else ("published" if host_st.get("published") else "local"),
            "model_id": active_m,
            "is_loaded": engine_instance.is_loaded(),
            "trainable": trainable,
            "training_base_model": suggested_hf or (active_m if trainable else None),
            "incompatibility_reason": incompat_reason,
            "details": {
                "lan_open": lan_st.get("open", False),
                "published": bool(host_st.get("published")),
                "active_peers": host_st.get("active_peers", 0) + len(lan_st.get("members", [])),
            }
        })
    return {"status": "ok", "servers": servers}
@app.get("/api/training/privacy")
def api_training_privacy_get(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return feedback.get_privacy_config()
@app.post("/api/training/privacy")
async def api_training_privacy_update(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
        return {"status": "ok", "privacy": feedback.update_privacy_config(data)}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/training/feedback")
def api_training_feedback_list(req: Request, status: str = None, limit: int = 200):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {"entries": feedback.list_feedback(status=status, limit=limit), "stats": feedback.compute_stats()}
@app.post("/api/training/feedback/{entry_id}/review")
async def api_training_feedback_review(entry_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
        entry = feedback.review_feedback(
            entry_id,
            action=data.get("action"),
            edited_fields=data.get("edited_fields"),
        )
        return {"status": "ok", "entry": entry}
    except KeyError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/training/datasets")
def api_training_datasets_list(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {"datasets": feedback.list_datasets(), "stats": feedback.compute_stats()}
@app.post("/api/training/datasets/build")
async def api_training_datasets_build(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        manifest = feedback.build_dataset_snapshot(
            task=data.get("task", "sft"),
            base_model=data.get("base_model") or engine_instance.active_model_id,
            name=data.get("name"),
            include_pending=bool(data.get("include_pending", False)),
            validate_with_soup=bool(data.get("validate_with_soup", True)),
        )
        return {"status": "ok", "dataset": manifest}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/training/datasets/{snapshot_id}")
def api_training_dataset_detail(snapshot_id: str, req: Request, preview: int = 10):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        m = feedback.get_dataset_manifest(snapshot_id)
        rows = feedback.preview_dataset_records(snapshot_id, limit=preview)
        return {"dataset": m, "preview": rows}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
@app.get("/api/training/datasets/{snapshot_id}/preview")
def api_training_dataset_preview(snapshot_id: str, req: Request, page: int = 1, page_size: int = 20):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        return feedback.preview_dataset_paginated(snapshot_id, page=page, page_size=page_size)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
@app.post("/api/training/datasets/{snapshot_id}/validate")
async def api_training_dataset_validate(snapshot_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        return feedback.validate_dataset_snapshot(
            snapshot_id,
            task=data.get("task"),
            check_with_soup=bool(data.get("check_with_soup", False)),
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
@app.post("/api/training/preflight")
async def api_training_preflight(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    return training.preflight_training_check(
        base_model=data.get("base_model"),
        dataset_snapshot_id=data.get("dataset_snapshot_id"),
        task=data.get("task", "sft"),
        params=data.get("params"),
    )
@app.get("/api/training/jobs")
def api_training_jobs_list(req: Request, limit: int = 50):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {"jobs": training.list_training_jobs(limit=limit)}
@app.post("/api/training/jobs/start")
async def api_training_jobs_start(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
        job = training.start_training_job(
            base_model=data.get("base_model"),
            dataset_snapshot_id=data.get("dataset_snapshot_id"),
            task=data.get("task", "sft"),
            params=data.get("params"),
            dry_run=bool(data.get("dry_run", False)),
            unload_inference=bool(data.get("unload_inference", True)),
            experiment_name=data.get("experiment_name"),
        )
        return {"status": "ok", "job": job}
    except (ValueError, KeyError, FileNotFoundError) as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=409, content={"status": "error", "message": str(e)})
@app.get("/api/training/jobs/{job_id}")
def api_training_job_detail(job_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        return {"job": training.get_training_job(job_id)}
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except KeyError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
@app.post("/api/training/jobs/{job_id}/cancel")
def api_training_job_cancel(job_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        return {"status": "ok", "job": training.cancel_training_job(job_id)}
    except KeyError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
@app.post("/api/training/jobs/{job_id}/resume")
async def api_training_job_resume(job_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        job = training.resume_training_job(job_id, checkpoint=data.get("checkpoint", "auto"))
        return {"status": "ok", "job": job}
    except KeyError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
    except (RuntimeError, FileNotFoundError) as e:
        return JSONResponse(status_code=409, content={"status": "error", "message": str(e)})
@app.get("/api/training/models")
def api_training_models_list(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return training.list_model_versions()
@app.post("/api/training/models/{version_id}/evaluate")
async def api_training_model_evaluate(version_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        ver = training.evaluate_version(
            version_id,
            tasks_inline=data.get("tasks"),
            min_accuracy=float(data.get("min_accuracy", 0.0)),
        )
        return {"status": "ok", "version": ver}
    except KeyError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
    except (ValueError, FileNotFoundError) as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except RuntimeError as e:
        return JSONResponse(status_code=409, content={"status": "error", "message": str(e)})
@app.post("/api/training/models/{version_id}/prepare")
async def api_training_model_prepare(version_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        ver = training.prepare_version_artifact(
            version_id,
            target=data.get("target", "merged"),
            quant=data.get("quant", "q4_k_m"),
        )
        return {"status": "ok", "version": ver}
    except KeyError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.post("/api/training/models/{version_id}/approve")
async def api_training_model_approve(version_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        ver = training.approve_version(
            version_id,
            approved=bool(data.get("approved", True)),
            notes=data.get("notes"),
        )
        return {"status": "ok", "version": ver}
    except KeyError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
@app.post("/api/training/models/{version_id}/deploy")
async def api_training_model_deploy(version_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        res = training.deploy_version(
            version_id,
            prefer=data.get("prefer", "auto"),
            load_into_engine=bool(data.get("load_into_engine", True)),
            require_approval=bool(data.get("require_approval", True)),
        )
        return {"status": "ok", **res}
    except PermissionError as e:
        return JSONResponse(status_code=403, content={"status": "error", "message": str(e)})
    except KeyError as e:
        return JSONResponse(status_code=404, content={"status": "error", "message": str(e)})
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.post("/api/training/models/rollback")
async def api_training_model_rollback(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        res = training.rollback_deployment(
            target_version_id=data.get("version_id"),
            load_into_engine=bool(data.get("load_into_engine", True)),
        )
        return {"status": "ok", **res}
    except (KeyError, ValueError) as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/training/audit")
def api_training_audit(req: Request, limit: int = 100):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {"events": feedback.read_audit_log(limit=limit)}
@app.post("/api/training/feedback/prune")
def api_training_feedback_prune(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return {"status": "ok", **feedback.prune_expired_feedback()}
@app.post("/api/training/backup")
async def api_training_backup(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    res = training.create_backup(
        include_runs=bool(data.get("include_runs", True)),
        include_datasets=bool(data.get("include_datasets", True)),
    )
    return res
@app.post("/api/training/restore")
async def api_training_restore(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    raw_path = str(data.get("backup_path") or "").strip()
    if not raw_path:
        return JSONResponse(status_code=400, content={"status": "error", "message": "backup_path required"})
    bdir = Path(paths.backups_dir()).resolve()
    cand = Path(raw_path).resolve()
    if not cand.is_relative_to(bdir):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Backup archive must live inside SHARD backups directory"})
    try:
        return training.restore_backup(str(cand), verify_hashes=bool(data.get("verify_hashes", True)))
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/net/status")
def api_net_status(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return _net_mgr().status()
@app.get("/api/net/identity")
def api_net_identity(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return _identity_payload(_net_mgr().identity)
@app.get("/api/net/config")
def api_net_config_get(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return _net_mgr().get_config()
@app.post("/api/net/config")
async def api_net_config_post(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    if not isinstance(data, dict):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Expected JSON object"})
    try:
        res = _net_mgr().configure(data)
        cfg = res.get("config") if isinstance(res, dict) and "config" in res else res
        return {"status": "ok", "config": cfg}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/net/servers")
def api_net_servers(req: Request, q: str = "", model: str = "", region: str = "", access: str = "", online_only: bool = True, sort: str = "activity", limit: int = 100):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return _net_mgr().discover_servers(q=q, model=model, region=region, access=access, online_only=online_only, sort=sort, limit=limit)
@app.get("/api/net/lookup/{node_id}")
def api_net_lookup(req: Request, node_id: str):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    res = _net_mgr().lookup_server(node_id)
    if not res:
        return JSONResponse(status_code=404, content={"status": "error", "message": "Node not found or offline"})
    return res
@app.post("/api/net/publish")
async def api_net_publish(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    model = (data.get("models") or [None])[0] if data.get("models") else (engine_instance.active_model_id or engine_instance.current_loaded_id)
    ok, reason = _verify_server_model_ready(model)
    if not ok:
        return JSONResponse(status_code=409, content={"status": "error", "code": "MODEL_NOT_READY", "message": f"Model must be loaded and pass inference verification before hosting or publishing a server: {reason}"})
    try:
        res = _net_mgr().publish_server(
            name=data.get("name"),
            visibility=data.get("visibility"),
            access_mode=data.get("access_mode") or data.get("access_policy"),
            region=data.get("region"),
            direct_p2p_port=data.get("direct_p2p_port") or data.get("direct_port"),
            models=data.get("models"),
            max_participants=data.get("max_participants") or data.get("max_peers"),
            bandwidth_kbps=data.get("bandwidth_kbps"),
            asgi_app=app,
        )
        return {"status": "ok", "host": res, "discovery_ok": res.get("discovery_ok", True), "warning": res.get("discovery_warning")}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.post("/api/net/unpublish")
def api_net_unpublish(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    res = _net_mgr().unpublish_server()
    return {"status": "ok", "host": res}
@app.get("/api/net/invites")
def api_net_invites_get(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    mgr = _net_mgr()
    invites = mgr.list_invites()
    out = []
    for inv in invites:
        item = dict(inv)
        iid = item.get("invite_id") or item.get("id")
        item["id"] = iid
        if "uri" not in item and "raw_token" in item:
            item["uri"] = mgr.build_invite_uri(invite_token=item["raw_token"])
        out.append(item)
    return {"status": "ok", "invites": out}
@app.post("/api/net/invites")
async def api_net_invites_post(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    try:
        mgr = _net_mgr()
        inv = mgr.create_invite(
            ttl_s=int(data.get("ttl_s", 86400)),
            max_uses=int(data.get("max_uses", 1)),
            label=str(data.get("label", "")),
        )
        inv_out = dict(inv)
        inv_out["id"] = inv_out.get("invite_id") or inv_out.get("id")
        return {"status": "ok", "invite": inv_out, "uri": inv_out.get("uri")}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.post("/api/net/invites/revoke")
async def api_net_invites_revoke(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    iid = str(data.get("invite_id") or "").strip()
    if not iid:
        return JSONResponse(status_code=400, content={"status": "error", "message": "invite_id required"})
    ok = _net_mgr().revoke_invite(iid)
    return {"status": "ok", "revoked": bool(ok)}
@app.delete("/api/net/invites/{invite_id}")
def api_net_invite_delete(req: Request, invite_id: str):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    ok = _net_mgr().revoke_invite(invite_id)
    return {"status": "ok", "revoked": bool(ok)}
@app.post("/api/net/connect")
async def api_net_connect(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    target = str(data.get("target") or "").strip()
    if not target:
        return JSONResponse(status_code=400, content={"status": "error", "message": "target required"})
    mode = str(data.get("mode") or "auto").strip()
    auth_proof = data.get("auth_proof")
    verify_node_id = data.get("verify_node_id")
    try:
        res = _net_mgr().connect(target, mode=mode, auth_proof=auth_proof, verify_node_id=verify_node_id)
        return res
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.post("/api/net/disconnect")
def api_net_disconnect(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    res = _net_mgr().disconnect()
    return {"status": "ok", "client": res}
@app.get("/api/net/peers")
def api_net_peers(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    mgr = _net_mgr()
    return {
        "status": "ok",
        "connected": mgr.list_connected_peers(),
        "pending": mgr.list_pending_peers(),
        "authorized": mgr.list_authorized_peers(),
        "revoked": mgr.list_revoked_peers(),
    }
@app.post("/api/net/peers/{peer_id}/approve")
def api_net_peer_approve(req: Request, peer_id: str):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    ok = _net_mgr().approve_peer(peer_id)
    return {"status": "ok", "approved": bool(ok)}
@app.post("/api/net/peers/{peer_id}/reject")
def api_net_peer_reject(req: Request, peer_id: str):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    ok = _net_mgr().reject_peer(peer_id)
    return {"status": "ok", "rejected": bool(ok)}
@app.post("/api/net/peers/{peer_id}/kick")
def api_net_peer_kick(req: Request, peer_id: str):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    ok = _net_mgr().kick_peer(peer_id)
    return {"status": "ok", "kicked": bool(ok)}
@app.post("/api/net/peers/{peer_id}/block")
def api_net_peer_block(req: Request, peer_id: str):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    ok = _net_mgr().block_peer(peer_id)
    return {"status": "ok", "blocked": bool(ok)}
@app.post("/api/net/peers/{peer_id}/unblock")
def api_net_peer_unblock(req: Request, peer_id: str):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    ok = _net_mgr().unblock_peer(peer_id)
    return {"status": "ok", "unblocked": bool(ok)}
@app.post("/api/net/peers/{peer_id}/quota")
async def api_net_peer_quota(req: Request, peer_id: str):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    rpm = data.get("rpm")
    kbps = data.get("kbps")
    try:
        peer = _net_mgr().set_peer_quota(peer_id, max_requests_per_min=rpm, bandwidth_kbps=kbps)
        return {"status": "ok", "peer": peer}
    except Exception as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
@app.get("/api/net/diagnostics")
def api_net_diagnostics(req: Request, nat: bool = False, port: int = None):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    return _net_mgr().diagnostics(probe_public=bool(nat))
_WORKSPACE_LOCK = threading.RLock()
def _workspace_file():
    return Path(paths.data_dir()) / "server_workspace.json"
def _load_workspace_unlocked():
    p = _workspace_file()
    default = {"shared_chats": [], "community_messages": [], "model_issues": []}
    d = storage.read_json_safe(p, default=default, expected_type=dict, quarantine=True, use_backup=True)
    if not isinstance(d, dict):
        return default
    for k in ("shared_chats", "community_messages", "model_issues"):
        if not isinstance(d.get(k), list):
            d[k] = []
    return d
def _save_workspace_unlocked(data):
    storage.atomic_write_json(_workspace_file(), data, indent=2, fsync=True, backup=True, restrict=True)
def _resolve_server_participant(req):
    session_tok = (req.headers.get("x-session-token") or "").strip()
    if session_tok:
        valid, rec, _ = session_registry.validate_session(session_tok)
        if valid and rec:
            return True, rec.get("role") or "member", rec.get("name") or "Участник", rec.get("participant_id")
        return False, None, None, None
    bearer = _extract_bearer(req)
    if bearer:
        valid, rec, _ = session_registry.validate_session(bearer)
        if valid and rec:
            return True, rec.get("role") or "member", rec.get("name") or "Участник", rec.get("participant_id")
        if _token_in_pool(bearer, LAN.tokens() | set(load_keys())):
            st = LAN.status(include_secrets=False)
            ip = req.client.host if req.client else "unknown"
            clean_tok = hashlib.sha256(f"{bearer}_{ip}".encode("utf-8")).hexdigest()[:16]
            for m in st.get("members") or []:
                if m.get("ip") == ip:
                    return True, m.get("role") or "member", m.get("name") or "Участник", f"member_{clean_tok}"
            return True, "member", "Участник", f"member_{clean_tok}"
    if _is_owner_request(req):
        return True, "owner", LAN.name() or "Owner", "owner"
    if net_tunnel.is_tunnel_request(req):
        pid = net_tunnel.tunnel_peer_id(req) or "peer"
        clean_pid = hashlib.sha256(pid.encode("utf-8")).hexdigest()[:16]
        return True, "tunnel_peer", f"peer:{pid[:10]}", f"peer_{clean_pid}"
    return False, None, None, None
def _resolve_server_actor(req):
    ok, role, name, _pid = _resolve_server_participant(req)
    return ok, role, name
@app.post("/api/server/session")
async def api_server_session_issue(req: Request):
    try:
        data = await req.json()
    except Exception:
        data = {}
    name = str(data.get("name") or "").strip()[:48]
    pairing_tok = str(data.get("pairing_token") or req.headers.get("x-pairing-token") or _extract_bearer(req) or "").strip()
    is_owner = _is_owner_request(req)
    is_tunnel = net_tunnel.is_tunnel_request(req)
    is_lan_token = bool(pairing_tok and _token_in_pool(pairing_tok, LAN.tokens() | set(load_keys())))
    is_open = LAN.is_open()
    if not (is_owner or is_tunnel or is_lan_token or is_open):
        return JSONResponse(status_code=401, content={"status": "error", "code": "PAIRING_REQUIRED", "message": "Требуется код сопряжения LAN или права владельца"})
    if is_owner:
        role = "owner"
        actor_name = name or LAN.name() or "Owner"
        method = "owner"
    elif is_tunnel:
        role = "tunnel_peer"
        pid = net_tunnel.tunnel_peer_id(req) or "peer"
        actor_name = name or f"peer:{pid[:8]}"
        method = "tunnel"
    elif is_lan_token:
        role = "member"
        actor_name = name or "Участник"
        method = "lan_pairing"
    else:
        role = "member"
        actor_name = name or f"Гость_{secrets.token_hex(2)}"
        method = "open_access"
    ttl = float(data.get("ttl_seconds") or 604800.0)
    raw_token, record = session_registry.issue_session(role=role, name=actor_name, auth_method=method, ttl_seconds=ttl)
    return {
        "status": "ok",
        "session_token": raw_token,
        "participant_id": record["participant_id"],
        "role": record["role"],
        "name": record["name"],
        "expires_at": record["expires_at"],
    }
@app.post("/api/server/session/revoke")
async def api_server_session_revoke(req: Request):
    try:
        data = await req.json()
    except Exception:
        data = {}
    token = str(data.get("session_token") or req.headers.get("x-session-token") or _extract_bearer(req) or "").strip()
    if not token:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Missing session token"})
    revoked = session_registry.revoke_session(token)
    return {"status": "ok", "revoked": revoked}
def _private_chats_dir():
    p = paths.data_dir() / "private_chats"
    p.mkdir(parents=True, exist_ok=True)
    return p
def _participant_chats_file(participant_id):
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", str(participant_id or "unknown").strip())[:64]
    return _private_chats_dir() / f"{safe_id}.json"
def _load_participant_chats(participant_id):
    p = _participant_chats_file(participant_id)
    with storage.file_lock(p):
        d = storage.read_json_safe(p, default=[], expected_type=list, quarantine=True, use_backup=True)
    return d if isinstance(d, list) else []
def _save_participant_chats(participant_id, chats):
    p = _participant_chats_file(participant_id)
    storage.atomic_write_json(p, chats, indent=2, fsync=True, backup=True, restrict=True)
def _get_active_private_sessions_count():
    try:
        p = _private_chats_dir()
        if not p.is_dir():
            return 0
        cnt = 0
        for f in p.glob("*.json"):
            if not f.name.endswith((".bak", ".tmp")):
                cnt += 1
        return cnt
    except Exception:
        return 0
@app.post("/api/server/create")
async def api_server_create(req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    name = str(data.get("name") or LAN.name() or socket.gethostname() or "shard").strip()[:64]
    model = str(data.get("model") or engine_instance.active_model_id or "").strip() or None
    open_lan = bool(data.get("open", False))
    enable_lan = bool(data.get("enable_lan", True))
    publish_global = bool(data.get("publish_global", False))
    visibility = str(data.get("visibility") or "private").strip().lower()
    access_mode = str(data.get("access_mode") or "invite_only").strip().lower()
    max_participants = int(data.get("max_participants") or 16)
    region = str(data.get("region") or "").strip()[:32]
    load_now = bool(data.get("load_model", False))
    load_info = None
    if load_now and model and not engine_instance.is_model_active(model):
        try:
            load_info = engine_instance.load_model(model)
        except Exception as e:
            load_info = {"status": "error", "message": str(e)}
    if enable_lan or publish_global:
        chk_model = model or engine_instance.active_model_id or engine_instance.current_loaded_id
        ok, reason = _verify_server_model_ready(chk_model)
        if not ok:
            return JSONResponse(status_code=409, content={"status": "error", "code": "MODEL_NOT_READY", "message": f"Model must be loaded and pass inference verification before hosting or publishing a server: {reason}", "model_load": load_info})
    lan_st = None
    if enable_lan:
        if LAN.is_server():
            with LAN._lock:
                LAN._state["name"] = name
                LAN._state["open"] = open_lan
                if model:
                    LAN._state["target_model"] = model
                LAN._save()
            lan_st = LAN.status(include_secrets=True)
        else:
            lan_st = LAN.create(name=name, open_net=open_lan, model=model)
    host_st = None
    if publish_global:
        try:
            host_st = _net_mgr().publish_server(
                name=name,
                visibility=visibility,
                access_mode=access_mode,
                max_participants=max_participants,
                region=region,
                model=model,
                models=[model] if model else None,
                asgi_app=app,
            )
        except Exception as e:
            return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    else:
        host_st = _net_mgr().host_status()
    return {
        "status": "ok",
        "lan": lan_st or LAN.status(include_secrets=True),
        "host": host_st,
        "discovery_ok": host_st.get("discovery_ok", True) if isinstance(host_st, dict) else True,
        "warning": host_st.get("discovery_warning") if isinstance(host_st, dict) else None,
        "model_load": load_info,
    }
@app.get("/api/server/workspace")
def api_server_workspace(req: Request):
    ok, role, actor_name, pid = _resolve_server_participant(req)
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется авторизация владельца или участника сервера"})
    is_owner = role == "owner"
    lan_st = LAN.status(include_secrets=is_owner)
    host_st = _net_mgr().host_status()
    eng_st = engine_instance.get_status()
    with _WORKSPACE_LOCK, storage.file_lock(_workspace_file()):
        ws = _load_workspace_unlocked()
    return {
        "status": "ok",
        "is_owner": is_owner,
        "participant_role": role,
        "participant_name": actor_name,
        "participant_id": pid,
        "server_active": bool(LAN.is_server() or host_st.get("published") or CLUSTER.role == "coordinator"),
        "lan": lan_st,
        "cluster": CLUSTER.status(include_secrets=is_owner) if CLUSTER.role != "idle" else None,
        "network_host": host_st if is_owner else {
            "published": bool(host_st.get("published")),
            "display_name": host_st.get("display_name"),
            "visibility": host_st.get("visibility"),
            "access_mode": host_st.get("access_mode"),
            "active_peers": host_st.get("active_peers", 0),
            "max_participants": host_st.get("max_participants", 16),
            "discovery_ok": host_st.get("discovery_ok", True),
            "discovery_warning": host_st.get("discovery_warning"),
        },
        "engine": {
            "status": eng_st.get("status"),
            "active_model": eng_st.get("active_model"),
            "current_loaded": eng_st.get("current_loaded"),
            "is_loaded": eng_st.get("is_loaded"),
            "lifecycle_state": eng_st.get("lifecycle_state"),
        },
        "private_session_count": _get_active_private_sessions_count(),
        "privacy_notice": "Private participant sessions are strictly isolated and never stored in workspace or exposed to peers.",
        "community_messages": ws.get("community_messages", [])[-100:],
        "model_issues": ws.get("model_issues", [])[-100:],
    }
@app.get("/api/server/chats")
def api_server_chats_list(req: Request):
    ok, role, actor_name, pid = _resolve_server_participant(req)
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется авторизация участника сервера"})
    chat_file = _participant_chats_file(pid)
    with storage.file_lock(chat_file):
        chats = _load_participant_chats(pid)
        summaries = [{
            "id": c.get("id"),
            "title": c.get("title") or "Новый диалог",
            "created_at": c.get("created_at"),
            "updated_at": c.get("updated_at"),
            "message_count": len(c.get("messages", [])),
        } for c in chats]
        return {"status": "ok", "participant_id": pid, "chats": summaries}
@app.post("/api/server/chats")
async def api_server_chat_send(req: Request):
    ok, role, actor_name, pid = _resolve_server_participant(req)
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется авторизация участника сервера"})
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    create_only = bool(data.get("create_only"))
    title_val = str(data.get("title") or "").strip()[:56]
    prompt = str(data.get("prompt") or "").strip()[:8000]
    now = time.time()
    chat_file = _participant_chats_file(pid)
    if create_only or (not prompt and title_val):
        with storage.file_lock(chat_file):
            chats = _load_participant_chats(pid)
            chat_id = f"pchat-{uuid.uuid4().hex[:10]}"
            new_chat = {
                "id": chat_id,
                "title": title_val or "Новый диалог",
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }
            chats.append(new_chat)
            if len(chats) > 50:
                chats = chats[-50:]
            _save_participant_chats(pid, chats)
            return {"status": "ok", "chat_id": chat_id, "chat": new_chat}
    if not prompt:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Пустой запрос"})
    chat_id = str(data.get("chat_id") or "").strip()
    max_tokens = min(2048, max(32, int(data.get("max_tokens") or 512)))
    temperature = max(0.0, min(2.0, float(data.get("temperature") or 0.7)))
    with storage.file_lock(chat_file):
        chats = _load_participant_chats(pid)
        target_chat = None
        for ch in chats:
            if ch.get("id") == chat_id:
                target_chat = ch
                break
        if target_chat is None:
            chat_id = f"pchat-{uuid.uuid4().hex[:10]}"
            target_chat = {
                "id": chat_id,
                "title": prompt[:56],
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }
            chats.append(target_chat)
            if len(chats) > 50:
                chats = chats[-50:]
        target_chat["messages"].append({
            "id": f"m-{uuid.uuid4().hex[:8]}",
            "role": "user",
            "author": actor_name or "Вы",
            "content": prompt,
            "ts": now,
        })
        history_msgs = [{"role": m["role"], "content": m["content"]} for m in target_chat["messages"][-12:] if m.get("role") in ("user", "assistant")]
        _save_participant_chats(pid, chats)
    cls_ready = CLUSTER.is_ready()
    if not cls_ready and not engine_instance.is_loaded():
        return JSONResponse(status_code=503, content={"status": "error", "message": "Модель не загружена на сервере"})
    try:
        if cls_ready:
            res = await SCHEDULER.run_sync_async(
                lambda cancel_ev: CLUSTER.generate_stream(history_msgs, max_tokens=max_tokens, temperature=temperature, cancel_check=cancel_ev.is_set),
                client_key=f"chat:{pid}",
                model_id=CLUSTER.target_model,
                is_disconnected=req.is_disconnected,
            )
        else:
            res = await asyncio.to_thread(engine_instance.generate_sync, history_msgs, max_tokens=max_tokens, temperature=temperature)
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Ошибка генерации модели: {str(e)}"})
    reply_text = str(res.get("content") or "").strip()
    reasoning = str(res.get("reasoning_content") or "").strip()
    with storage.file_lock(chat_file):
        chats = _load_participant_chats(pid)
        target_chat = None
        for ch in chats:
            if ch.get("id") == chat_id:
                target_chat = ch
                break
        if target_chat is None:
            chat_id = f"pchat-{uuid.uuid4().hex[:10]}"
            target_chat = {
                "id": chat_id,
                "title": prompt[:56],
                "created_at": now,
                "updated_at": now,
                "messages": [],
            }
            chats.append(target_chat)
        target_chat["messages"].append({
            "id": f"m-{uuid.uuid4().hex[:8]}",
            "role": "assistant",
            "author": engine_instance.active_model_id or "AI Host",
            "content": reply_text or "(пустой ответ модели)",
            "reasoning_content": reasoning or None,
            "ts": time.time(),
        })
        target_chat["updated_at"] = time.time()
        _save_participant_chats(pid, chats)
    return {
        "status": "ok",
        "chat_id": chat_id,
        "reply": reply_text,
        "reasoning_content": reasoning,
        "usage": res.get("usage", {}),
        "telemetry": res.get("telemetry", {}),
        "chat": target_chat,
    }
@app.get("/api/server/chats/{chat_id}")
def api_server_chat_get(chat_id: str, req: Request):
    ok, role, actor_name, pid = _resolve_server_participant(req)
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется авторизация участника сервера"})
    chat_file = _participant_chats_file(pid)
    with storage.file_lock(chat_file):
        chats = _load_participant_chats(pid)
        for ch in chats:
            if ch.get("id") == chat_id:
                return {"status": "ok", "chat": ch}
        return JSONResponse(status_code=404, content={"status": "error", "message": "Диалог не найден"})
@app.delete("/api/server/chats/{chat_id}")
def api_server_chat_delete(chat_id: str, req: Request):
    ok, role, actor_name, pid = _resolve_server_participant(req)
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется авторизация участника сервера"})
    chat_file = _participant_chats_file(pid)
    with storage.file_lock(chat_file):
        chats = _load_participant_chats(pid)
        kept = [c for c in chats if c.get("id") != chat_id]
        if len(kept) == len(chats):
            return JSONResponse(status_code=404, content={"status": "error", "message": "Диалог не найден"})
        _save_participant_chats(pid, kept)
        return {"status": "ok", "deleted": chat_id}
@app.post("/api/server/shared-chat")
async def api_server_shared_chat(req: Request):
    return await api_server_chat_send(req)
@app.delete("/api/server/shared-chat/{chat_id}")
def api_server_shared_chat_delete(chat_id: str, req: Request):
    return api_server_chat_delete(chat_id, req)
@app.get("/api/server/community")
def api_server_community_get(req: Request):
    ok, _, _ = _resolve_server_actor(req)
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется авторизация участника"})
    with _WORKSPACE_LOCK, storage.file_lock(_workspace_file()):
        ws = _load_workspace_unlocked()
    return {"status": "ok", "messages": ws.get("community_messages", [])[-100:]}
@app.post("/api/server/community")
async def api_server_community_post(req: Request):
    ok, role, actor_name = _resolve_server_actor(req)
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется авторизация участника"})
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    text = str(data.get("text") or "").strip()[:2000]
    if not text:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Пустое сообщение"})
    author = str(data.get("author") or actor_name or "Участник").strip()[:48]
    msg = {
        "id": f"cmsg-{uuid.uuid4().hex[:8]}",
        "author": author,
        "role": role,
        "text": text,
        "ts": time.time(),
    }
    with _WORKSPACE_LOCK, storage.file_lock(_workspace_file()):
        ws = _load_workspace_unlocked()
        msgs = ws.setdefault("community_messages", [])
        msgs.append(msg)
        if len(msgs) > 200:
            ws["community_messages"] = msgs[-200:]
        _save_workspace_unlocked(ws)
    return {"status": "ok", "message": msg, "messages": ws["community_messages"][-100:]}
@app.get("/api/server/issues")
def api_server_issues_get(req: Request):
    ok, _, _ = _resolve_server_actor(req)
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется авторизация участника"})
    with _WORKSPACE_LOCK, storage.file_lock(_workspace_file()):
        ws = _load_workspace_unlocked()
    return {"status": "ok", "issues": list(reversed(ws.get("model_issues", [])[-100:]))}
@app.post("/api/server/issues")
async def api_server_issues_post(req: Request):
    ok, role, actor_name = _resolve_server_actor(req)
    if not ok:
        return JSONResponse(status_code=401, content={"status": "error", "message": "Требуется авторизация участника"})
    try:
        data = await req.json()
    except Exception:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid JSON body"})
    title = str(data.get("title") or "").strip()[:160]
    category = str(data.get("category") or "hallucination").strip().lower()[:32]
    prompt = str(data.get("prompt") or "").strip()[:8000]
    response = str(data.get("response") or "").strip()[:8000]
    expected = str(data.get("expected") or data.get("expected_output") or "").strip()[:8000]
    author = str(data.get("author") or actor_name or "Участник").strip()[:48]
    if not title and not prompt:
        return JSONResponse(status_code=400, content={"status": "error", "message": "Укажите заголовок или проблемный промпт"})
    now = time.time()
    fb_id = None
    if bool(data.get("convert_to_feedback")) and prompt and (response or expected):
        try:
            fb_entry = feedback.submit_feedback({
                "prompt": prompt,
                "response": response or expected,
                "better_response": expected if expected else None,
                "rating": "correction" if expected else "down",
                "model_id": engine_instance.active_model_id or "",
                "consent_given": True,
            }, source=("local" if role == "owner" else "remote"), client_hint=_contributor_identity(req))
            fb_id = fb_entry.get("id")
        except Exception:
            fb_id = None
    issue = {
        "id": f"iss-{uuid.uuid4().hex[:8]}",
        "title": title or prompt[:80],
        "category": category,
        "prompt": prompt,
        "response": response,
        "expected": expected,
        "author": author,
        "role": role,
        "model_id": str(data.get("model_id") or engine_instance.active_model_id or ""),
        "status": "converted_to_feedback" if fb_id else "open",
        "feedback_id": fb_id,
        "created_at": now,
        "updated_at": now,
    }
    with _WORKSPACE_LOCK, storage.file_lock(_workspace_file()):
        ws = _load_workspace_unlocked()
        issues = ws.setdefault("model_issues", [])
        issues.append(issue)
        if len(issues) > 200:
            ws["model_issues"] = issues[-200:]
        _save_workspace_unlocked(ws)
    return {"status": "ok", "issue": issue, "issues": list(reversed(ws["model_issues"][-100:]))}
@app.post("/api/server/issues/{issue_id}/status")
@app.post("/api/server/issues/{issue_id}/resolve")
async def api_server_issue_status(issue_id: str, req: Request):
    denied = _deny_non_owner(req)
    if denied:
        return denied
    try:
        data = await req.json()
    except Exception:
        data = {}
    new_st = str(data.get("status") or "resolved").strip().lower()
    if new_st not in ("open", "resolved", "converted_to_feedback", "delete"):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Unsupported status"})
    target = None
    with _WORKSPACE_LOCK, storage.file_lock(_workspace_file()):
        ws = _load_workspace_unlocked()
        issues = ws.get("model_issues", [])
        if new_st == "delete":
            ws["model_issues"] = [x for x in issues if x.get("id") != issue_id]
            _save_workspace_unlocked(ws)
            return {"status": "ok", "deleted": True}
        for item in issues:
            if item.get("id") == issue_id:
                item["status"] = new_st
                item["updated_at"] = time.time()
                target = dict(item)
                break
        if target is None:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Issue not found"})
        if new_st == "converted_to_feedback" and not target.get("feedback_id") and target.get("prompt"):
            try:
                fb_entry = feedback.submit_feedback({
                    "prompt": target["prompt"],
                    "response": target.get("response") or target.get("expected") or "error",
                    "better_response": target.get("expected") or None,
                    "rating": "correction" if target.get("expected") else "down",
                    "model_id": target.get("model_id") or engine_instance.active_model_id or "",
                    "consent_given": True,
                }, source="local")
                for item in issues:
                    if item.get("id") == issue_id:
                        item["feedback_id"] = fb_entry.get("id")
                        target = dict(item)
                        break
            except Exception:
                pass
        _save_workspace_unlocked(ws)
    return {"status": "ok", "issue": target}
def _render_dashboard_html(client_mode):
    html = paths.read_package_text("dashboard.html")
    st = LAN.status(include_secrets=False)
    boot = {
        "client_mode": bool(client_mode),
        "is_owner": not bool(client_mode),
        "version": __version__,
        "lan_mode": st.get("mode"),
        "lan_open": bool(st.get("open")),
        "server_name": st.get("name"),
        "active_model": engine_instance.active_model_id,
    }
    boot_json = json.dumps(boot, ensure_ascii=False).replace("</", "<\\/")
    tag = f'<script id="shard-boot" type="application/json">{boot_json}</script>'
    if "</head>" in html:
        return html.replace("</head>", tag + "\n</head>", 1)
    return tag + html
@app.get("/", response_class=HTMLResponse)
def dashboard(req: Request):
    is_owner = _is_owner_request(req)
    return _render_dashboard_html(client_mode=(not is_owner))
@app.get("/client", response_class=HTMLResponse)
def client_dashboard():
    return _render_dashboard_html(client_mode=True)
