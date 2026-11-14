import json
import os
import re
import secrets
import shutil
import threading
import time
import uuid
from pathlib import Path
from . import feedback
from . import paths
from . import process
from . import soup_adapter
from . import storage
from .engine import engine_instance
_STATE_LOCK = threading.RLock()
_ACTIVE_PROCS = {}
_LOG_HANDLES = {}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
def _validate_safe_id(val, label="id"):
    s = str(val or "").strip()
    if not s or "/" in s or "\\" in s or ".." in s or not _SAFE_ID_RE.match(s):
        raise ValueError(f"Invalid {label}.")
    return s
def _base_dir():
    d = Path(paths.training_dir())
    d.mkdir(parents=True, exist_ok=True)
    return d
def _runs_dir():
    d = Path(paths.training_runs_dir())
    d.mkdir(parents=True, exist_ok=True)
    return d
def _jobs_path():
    return _base_dir() / "jobs.json"
def _registry_path():
    return _base_dir() / "model_registry.json"
def get_owner_token():
    with _STATE_LOCK:
        tf = Path(paths.owner_token_file())
        with storage.file_lock(tf):
            if tf.is_file():
                tok = tf.read_text(encoding="utf-8", errors="replace").strip()
                if len(tok) >= 16:
                    storage.restrict_file_permissions(tf)
                    return tok
            tok = f"shard-owner-{secrets.token_urlsafe(24)}"
            storage.atomic_write_text(tf, tok, fsync=True, backup=False, restrict=True)
            return tok
def verify_owner_token(candidate):
    if not candidate:
        return False
    expected = get_owner_token()
    try:
        return secrets.compare_digest(str(candidate).strip(), expected)
    except Exception:
        return False
def _atomic_write_json(path_obj, data, backup=True):
    storage.atomic_write_json(path_obj, data, indent=2, fsync=True, backup=backup)
def _hydrate_job_paths(j):
    if not isinstance(j, dict):
        return j
    data_root = paths.data_dir()
    for abs_k, rel_k in (
        ("dataset_path", "dataset_rel_path"),
        ("soup_config_path", "soup_config_rel_path"),
        ("output_dir", "output_rel_path"),
        ("log_path", "log_rel_path"),
    ):
        if j.get(abs_k) or j.get(rel_k):
            j[abs_k] = storage.resolve_portable_path(j.get(abs_k), j.get(rel_k), data_root)
    art = j.get("artifacts")
    if isinstance(art, dict) and j.get("output_dir"):
        try:
            j["artifacts"] = soup_adapter.scan_output_artifacts(j["output_dir"])
        except Exception:
            pass
    return j
def _hydrate_version_paths(v):
    if not isinstance(v, dict):
        return v
    data_root = paths.data_dir()
    for abs_k, rel_k in (
        ("artifact_path", "rel_path"),
        ("adapter_dir", "adapter_rel_dir"),
        ("merged_dir", "merged_rel_dir"),
        ("gguf_path", "gguf_rel_path"),
    ):
        if v.get(abs_k) or v.get(rel_k):
            v[abs_k] = storage.resolve_portable_path(v.get(abs_k), v.get(rel_k), data_root)
    return v
def _load_jobs():
    p = _jobs_path()
    with storage.file_lock(p):
        data = storage.read_json_safe(p, default=[], expected_type=list, quarantine=True, use_backup=True)
        out = data if isinstance(data, list) else []
        for j in out:
            _hydrate_job_paths(j)
        return out
def _save_jobs(jobs):
    p = _jobs_path()
    with storage.file_lock(p):
        _atomic_write_json(p, jobs, backup=True)
def _load_registry():
    p = _registry_path()
    default = {
        "active_version_id": None,
        "previous_version_id": None,
        "previous_engine_model_id": None,
        "active_engine_model_id": None,
        "versions": [],
    }
    with storage.file_lock(p):
        data = storage.read_json_safe(p, default=default, expected_type=dict, quarantine=True, use_backup=True)
        if not isinstance(data, dict):
            return default
        for k, v in default.items():
            data.setdefault(k, v)
        if not isinstance(data.get("versions"), list):
            data["versions"] = []
        for item in data["versions"]:
            _hydrate_version_paths(item)
        return data
def _save_registry(reg):
    p = _registry_path()
    with storage.file_lock(p):
        _atomic_write_json(p, reg, backup=True)
def _sync_active_model_file(model_id_or_path):
    val = str(model_id_or_path or "").strip()
    if not val:
        return
    try:
        storage.atomic_write_json(
            engine_instance.active_model_file,
            {"model_id": val, "updated_at": time.time()},
            fsync=True,
            backup=True,
        )
    except Exception:
        pass
def _is_job_alive_unlocked(j):
    if j.get("status") not in ("queued", "validating", "running"):
        return False
    jid = j.get("job_id")
    proc = _ACTIVE_PROCS.get(jid)
    if proc is not None:
        return proc.poll() is None
    if j.get("host_pid") == os.getpid() and j.get("status") in ("queued", "validating"):
        if (time.time() - float(j.get("created_at") or 0)) < 30:
            return True
    pid = j.get("pid")
    if pid and j.get("host_pid") == os.getpid() and process.is_pid_running(pid):
        return True
    return False
def _active_job_unlocked(jobs):
    for j in jobs:
        if _is_job_alive_unlocked(j):
            return j
    return None
def reconcile_jobs():
    recovered_jobs = []
    with _STATE_LOCK:
        p = _jobs_path()
        with storage.file_lock(p):
            jobs = _load_jobs()
            changed = False
            for j in jobs:
                if j.get("status") not in ("queued", "validating", "running"):
                    continue
                if _is_job_alive_unlocked(j):
                    continue
                jid = j.get("job_id")
                if jid in _ACTIVE_PROCS:
                    continue
                pid = j.get("pid")
                if pid and j.get("host_pid") != os.getpid() and process.is_pid_running(pid):
                    continue
                _hydrate_job_paths(j)
                out_dir = j.get("output_dir") or ""
                art = soup_adapter.scan_output_artifacts(out_dir) if out_dir else j.get("artifacts") or {}
                prog = soup_adapter.parse_train_progress(j.get("log_path"))
                j["artifacts"] = art
                j["progress"] = {
                    "last_loss": prog.get("last_loss"),
                    "last_epoch": prog.get("last_epoch"),
                    "current_step": prog.get("current_step"),
                    "total_steps": prog.get("total_steps"),
                }
                j["finished_at"] = j.get("finished_at") or time.time()
                has_art = bool(art.get("adapter_dir") or art.get("merged_dir") or art.get("gguf_files"))
                steps_done = bool(prog.get("total_steps") and prog.get("current_step") and int(prog["current_step"]) >= int(prog["total_steps"]))
                if has_art and (j.get("training_completed") or steps_done):
                    j["status"] = "completed"
                    j["training_completed"] = True
                    recovered_jobs.append(dict(j))
                else:
                    j["status"] = "interrupted"
                    j["training_completed"] = False
                    if not j.get("error"):
                        j["error"] = "Training process interrupted by server shutdown or unexpected exit."
                changed = True
                feedback._append_audit("training_job_reconciled", {"job_id": jid, "status": j["status"]})
            if changed:
                _save_jobs(jobs)
        for rj in recovered_jobs:
            ver = _register_version_from_job(rj)
            with storage.file_lock(p):
                jobs2 = _load_jobs()
                for jj in jobs2:
                    if jj.get("job_id") == rj["job_id"] and not jj.get("model_version_id"):
                        jj["model_version_id"] = ver["version_id"]
                        _save_jobs(jobs2)
                        break
        return _load_jobs()
def _safe_id(val, prefix):
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(val or "").strip()).strip("-")
    if not cleaned:
        cleaned = f"{prefix}-{uuid.uuid4().hex[:8]}"
    return cleaned[:64]
def _register_version_from_job(job):
    with _STATE_LOCK:
