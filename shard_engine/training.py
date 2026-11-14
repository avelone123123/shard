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
