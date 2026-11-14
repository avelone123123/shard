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
from . import model_browser
from . import paths
from . import process
from . import soup_adapter
from . import storage
from .engine import engine_instance
_STATE_LOCK = threading.RLock()
_ACTIVE_PROCS = {}
_LOG_HANDLES = {}
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_LIFECYCLE_MAP = {
    "queued": "QUEUED",
    "validating": "PREPARING",
    "running": "RUNNING",
    "completed": "COMPLETED",
    "failed": "FAILED",
    "cancelled": "CANCELLED",
    "interrupted": "FAILED",
}
def _job_lifecycle_state(status):
    return _LIFECYCLE_MAP.get(str(status or "").strip().lower(), "FAILED")
def _enrich_job_fields(job):
    if isinstance(job, dict):
        job["lifecycle_state"] = _job_lifecycle_state(job.get("status"))
        if "command" not in job:
            job["command"] = []
    return job
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
        reg = _load_registry()
        for existing in reg["versions"]:
            if existing.get("job_id") == job["job_id"]:
                return existing
        art = job.get("artifacts") or {}
        if art.get("gguf_files"):
            art_type = "gguf"
            art_path = art["gguf_files"][0]
        elif art.get("merged_dir"):
            art_type = "merged_hf"
            art_path = art["merged_dir"]
        elif art.get("adapter_dir"):
            art_type = "lora_adapter"
            art_path = art["adapter_dir"]
        else:
            art_type = "output_dir"
            art_path = job["output_dir"]
        ver_num = len(reg["versions"]) + 1
        version_id = f"v{ver_num}-{uuid.uuid4().hex[:6]}"
        data_root = paths.data_dir()
        gguf_p = art["gguf_files"][0] if art.get("gguf_files") else None
        ver = {
            "version_id": version_id,
            "created_at": time.time(),
            "parent_version": reg.get("active_version_id") or "base",
            "base_model": job["base_model"],
            "job_id": job["job_id"],
            "dataset_snapshot_id": job["dataset_snapshot_id"],
            "task": job["task"],
            "artifact_type": art_type,
            "artifact_path": art_path,
            "rel_path": storage.normalize_rel_path(art_path, data_root),
            "adapter_dir": art.get("adapter_dir"),
            "adapter_rel_dir": storage.normalize_rel_path(art.get("adapter_dir"), data_root),
            "merged_dir": art.get("merged_dir"),
            "merged_rel_dir": storage.normalize_rel_path(art.get("merged_dir"), data_root),
            "gguf_path": gguf_p,
            "gguf_rel_path": storage.normalize_rel_path(gguf_p, data_root),
            "training_completed": True,
            "eval_status": "not_run",
            "eval_baseline": None,
            "eval_candidate": None,
            "comparison": None,
            "approval_status": "pending",
            "deployed": False,
            "deployed_at": None,
            "notes": "",
        }
        reg["versions"].insert(0, ver)
        _save_registry(reg)
        feedback._append_audit("model_version_registered", {
            "version_id": version_id,
            "job_id": job["job_id"],
            "artifact_type": art_type,
        })
        return ver
def _watch_job(job_id, proc, log_fh, custom_cmd=None):
    try:
        rc = proc.wait()
    except Exception:
        rc = -1
    finally:
        try:
            log_fh.close()
        except Exception:
            pass
    with _STATE_LOCK:
        _ACTIVE_PROCS.pop(job_id, None)
        _LOG_HANDLES.pop(job_id, None)
        jobs = _load_jobs()
        target = None
        for j in jobs:
            if j.get("job_id") == job_id:
                target = j
                break
        if target is None:
            return
        if target.get("status") == "cancelled":
            target["returncode"] = rc
            target["finished_at"] = time.time()
            _save_jobs(jobs)
            return
        target["returncode"] = rc
        target["finished_at"] = time.time()
        art = soup_adapter.scan_output_artifacts(target["output_dir"])
        target["artifacts"] = art
        prog = soup_adapter.parse_train_progress(target.get("log_path"))
        target["progress"] = {
            "last_loss": prog.get("last_loss"),
            "last_epoch": prog.get("last_epoch"),
            "current_step": prog.get("current_step"),
            "total_steps": prog.get("total_steps"),
        }
        if rc == 0:
            target["status"] = "completed"
            target["training_completed"] = True
            _save_jobs(jobs)
            ver = _register_version_from_job(target)
            jobs = _load_jobs()
            for j in jobs:
                if j.get("job_id") == job_id:
                    j["model_version_id"] = ver["version_id"]
                    break
            _save_jobs(jobs)
            feedback._append_audit("training_job_completed", {"job_id": job_id, "version_id": ver["version_id"]})
        else:
            target["status"] = "failed"
            target["training_completed"] = False
            tail = prog.get("lines", [])[-5:]
            target["error"] = f"Soup training exited with code {rc}: " + (" | ".join(tail)[:300] if tail else "check train.log")
            _save_jobs(jobs)
            feedback._append_audit("training_job_failed", {"job_id": job_id, "returncode": rc})
def start_training_job(
    base_model=None,
    dataset_snapshot_id=None,
    task="sft",
    params=None,
    dry_run=False,
    unload_inference=True,
    experiment_name=None,
    soup_cmd=None,
):
    soup_info = soup_adapter.detect_soup(custom_cmd=soup_cmd, probe_deps=True)
    if not soup_info.get("available"):
        raise RuntimeError(
            "SOUP CLI is not available. " + (" ".join(soup_info.get("warnings") or []))
        )
    if not soup_info.get("capabilities", {}).get("train"):
        raise RuntimeError(
            "SOUP training dependencies are missing: " + ", ".join(soup_info.get("missing_train_deps") or ["soup-cli[train]"])
        )
    if not dataset_snapshot_id:
        raise ValueError("dataset_snapshot_id is required. Build an immutable dataset snapshot first.")
    manifest = feedback.verify_dataset_integrity(dataset_snapshot_id)
    base = str(base_model or manifest.get("base_model") or engine_instance.active_model_id or "").strip()
    if not base:
        raise ValueError("base_model must be specified or loaded in SHARD.")
    if os.path.isfile(base) and base.lower().endswith(".gguf"):
        raise ValueError("Training requires a Hugging Face model ID or HF directory as base_model, not a quantized .gguf file.")
    task_norm = str(task or manifest.get("task") or "sft").strip().lower()
    if task_norm == "dpo" and manifest.get("format") != "dpo":
        raise ValueError("DPO training requires a 'dpo' preference dataset snapshot (prompt/chosen/rejected).")
    eng_st = engine_instance.get_status()
    if eng_st.get("status") == "generating":
        raise RuntimeError("Inference engine is actively generating; wait or cancel generation before training.")
    if eng_st.get("is_loaded") or eng_st.get("status") == "loading":
        if unload_inference:
            engine_instance.unload_model()
        else:
            raise RuntimeError("An inference model is loaded in memory. Unload it or enable unload_inference to prevent OOM.")
    with _STATE_LOCK:
        jobs = reconcile_jobs()
        active = _active_job_unlocked(jobs)
        if active is not None:
            raise RuntimeError(f"Another training job '{active['job_id']}' is already active ({active['status']}).")
        job_id = f"job-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        run_dir = _runs_dir() / job_id
        output_dir = run_dir / "output"
        config_path = run_dir / "soup.yaml"
        log_path = run_dir / "train.log"
        run_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        merged_params = dict(params or {})
        if not soup_info.get("cuda_available"):
            if merged_params.get("quantization") in (None, "", "4bit", "8bit") and not merged_params.get("force_quantization"):
                merged_params["quantization"] = "none"
        merged_params.pop("force_quantization", None)
        cfg_dict, errors = soup_adapter.build_soup_config(
            base_model=base,
            dataset_path=manifest["path"],
            output_dir=str(output_dir),
            task=task_norm,
            fmt=manifest.get("format"),
            experiment_name=experiment_name or job_id,
            params=merged_params,
        )
        if errors:
            raise ValueError("; ".join(errors))
        soup_adapter.write_soup_config(cfg_dict, config_path)
        data_root = paths.data_dir()
        job = {
            "job_id": job_id,
            "host_pid": os.getpid(),
            "created_at": time.time(),
            "started_at": time.time(),
            "finished_at": None,
            "status": "validating",
            "base_model": base,
            "dataset_snapshot_id": dataset_snapshot_id,
            "dataset_path": manifest["path"],
            "dataset_rel_path": storage.normalize_rel_path(manifest["path"], data_root),
            "task": task_norm,
            "params": cfg_dict,
            "dry_run": bool(dry_run),
            "soup_config_path": str(config_path),
            "soup_config_rel_path": storage.normalize_rel_path(config_path, data_root),
            "output_dir": str(output_dir),
            "output_rel_path": storage.normalize_rel_path(output_dir, data_root),
            "log_path": str(log_path),
            "log_rel_path": storage.normalize_rel_path(log_path, data_root),
            "pid": None,
            "returncode": None,
            "error": None,
            "training_completed": False,
            "artifacts": {"checkpoints": [], "adapter_dir": None, "adapter_files": [], "merged_dir": None, "gguf_files": []},
            "progress": {"last_loss": None, "last_epoch": None, "current_step": None, "total_steps": None},
            "model_version_id": None,
        }
        jobs.insert(0, job)
        _save_jobs(jobs)
    dry_res = soup_adapter.dry_run_train(soup_info, config_path)
    if not dry_res["ok"]:
        with _STATE_LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "failed"
                    j["finished_at"] = time.time()
                    j["returncode"] = dry_res["returncode"]
                    j["error"] = (dry_res["stderr"] or dry_res["stdout"] or "soup train --dry-run failed")[:500]
                    break
            _save_jobs(jobs)
        raise RuntimeError(f"SOUP config validation (--dry-run) failed: {(dry_res['stderr'] or dry_res['stdout'])[:300]}")
    if dry_run:
        with _STATE_LOCK:
            jobs = _load_jobs()
            for j in jobs:
                if j["job_id"] == job_id:
                    j["status"] = "completed"
                    j["finished_at"] = time.time()
                    j["returncode"] = 0
                    j["training_completed"] = False
                    j["dry_run_output"] = (dry_res["stdout"] or dry_res["stderr"])[:2000]
                    result_job = dict(j)
                    break
            _save_jobs(jobs)
        feedback._append_audit("training_dry_run_completed", {"job_id": job_id})
        return result_job
    proc, log_fh = soup_adapter.launch_train_popen(
        soup_info,
        config_path,
        log_path,
        run_name=experiment_name or job_id,
    )
    with _STATE_LOCK:
        _ACTIVE_PROCS[job_id] = proc
        _LOG_HANDLES[job_id] = log_fh
        jobs = _load_jobs()
        for j in jobs:
            if j["job_id"] == job_id:
                j["status"] = "running"
                j["pid"] = proc.pid
                result_job = dict(j)
                break
        _save_jobs(jobs)
    feedback._append_audit("training_job_started", {"job_id": job_id, "pid": proc.pid, "base_model": base, "task": task_norm})
    t = threading.Thread(target=_watch_job, args=(job_id, proc, log_fh, soup_cmd), daemon=True)
    t.start()
    return result_job
def cancel_training_job(job_id):
    _validate_safe_id(job_id, "job_id")
    with _STATE_LOCK:
        jobs = _load_jobs()
        target = None
        for j in jobs:
            if j.get("job_id") == job_id:
                target = j
                break
        if target is None:
            raise KeyError(f"Training job '{job_id}' not found.")
        if target.get("status") in ("completed", "failed", "cancelled", "interrupted"):
            return target
        target["status"] = "cancelled"
        target["finished_at"] = time.time()
        target["error"] = "Cancelled by owner."
        proc = _ACTIVE_PROCS.get(job_id)
        _save_jobs(jobs)
    if proc is not None:
        soup_adapter.terminate_process(proc)
    feedback._append_audit("training_job_cancelled", {"job_id": job_id})
    return get_training_job(job_id)
def resume_training_job(job_id, checkpoint="auto", soup_cmd=None):
    _validate_safe_id(job_id, "job_id")
    soup_info = soup_adapter.detect_soup(custom_cmd=soup_cmd, probe_deps=True)
    if not soup_info.get("available"):
        raise RuntimeError("SOUP CLI is not available.")
    with _STATE_LOCK:
        jobs = reconcile_jobs()
        if _active_job_unlocked(jobs) is not None:
            raise RuntimeError("Another training job is already active.")
        target = None
        for j in jobs:
            if j.get("job_id") == job_id:
                target = j
                break
        if target is None:
            raise KeyError(f"Training job '{job_id}' not found.")
        cfg_path = target.get("soup_config_path")
        if not cfg_path or not os.path.isfile(cfg_path):
            raise FileNotFoundError("Saved soup.yaml for this job is missing.")
        log_path = target["log_path"]
        target["status"] = "running"
        target["host_pid"] = os.getpid()
        target["finished_at"] = None
        target["error"] = None
        _save_jobs(jobs)
    proc, log_fh = soup_adapter.launch_train_popen(
        soup_info,
        cfg_path,
        log_path,
        resume=checkpoint or "auto",
        run_name=job_id,
    )
    with _STATE_LOCK:
        _ACTIVE_PROCS[job_id] = proc
        _LOG_HANDLES[job_id] = log_fh
        jobs = _load_jobs()
        for j in jobs:
            if j.get("job_id") == job_id:
                j["pid"] = proc.pid
                out_job = dict(j)
                break
        _save_jobs(jobs)
    threading.Thread(target=_watch_job, args=(job_id, proc, log_fh, soup_cmd), daemon=True).start()
    feedback._append_audit("training_job_resumed", {"job_id": job_id, "checkpoint": checkpoint})
    return out_job
def list_training_jobs(limit=50):
    with _STATE_LOCK:
        jobs = reconcile_jobs()[:max(1, int(limit))]
        for j in jobs:
            if j.get("status") == "running" and j["job_id"] not in _ACTIVE_PROCS:
                art = soup_adapter.scan_output_artifacts(j.get("output_dir") or "")
                j["artifacts"] = art
            prog = soup_adapter.parse_train_progress(j.get("log_path"))
            j["progress"] = {
                "last_loss": prog.get("last_loss"),
                "last_epoch": prog.get("last_epoch"),
                "current_step": prog.get("current_step"),
                "total_steps": prog.get("total_steps"),
            }
        return jobs
def get_training_job(job_id):
    _validate_safe_id(job_id, "job_id")
    with _STATE_LOCK:
        jobs = reconcile_jobs()
        for j in jobs:
            if j.get("job_id") == job_id:
                prog = soup_adapter.parse_train_progress(j.get("log_path"))
                out = dict(j)
                out["log_tail"] = prog.get("lines", [])
                out["progress"] = {
                    "last_loss": prog.get("last_loss"),
                    "last_epoch": prog.get("last_epoch"),
                    "current_step": prog.get("current_step"),
                    "total_steps": prog.get("total_steps"),
                }
                out["artifacts"] = soup_adapter.scan_output_artifacts(out.get("output_dir") or "")
                return out
    raise KeyError(f"Training job '{job_id}' not found.")
def list_model_versions():
    with _STATE_LOCK:
        return _load_registry()
def get_model_version(version_id):
    _validate_safe_id(version_id, "version_id")
    with _STATE_LOCK:
        reg = _load_registry()
        for v in reg["versions"]:
            if v.get("version_id") == version_id:
                return v
    raise KeyError(f"Model version '{version_id}' not found.")
def _build_eval_tasks_for_version(ver, tasks_inline=None):
    run_dir = _runs_dir() / ver["job_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    eval_path = run_dir / f"eval-tasks-{ver['version_id']}.jsonl"
    tasks = []
    if tasks_inline and isinstance(tasks_inline, list):
        for item in tasks_inline:
            if isinstance(item, dict) and item.get("prompt"):
                tasks.append({
                    "prompt": str(item["prompt"]),
                    "expected": str(item.get("expected") or ""),
                    "category": str(item.get("category") or "custom"),
                    "scoring": str(item.get("scoring") or "contains"),
                })
    else:
        rows = feedback.preview_dataset_records(ver["dataset_snapshot_id"], limit=20)
        for idx, r in enumerate(rows):
            if "messages" in r and len(r["messages"]) >= 2:
                prompt = r["messages"][0].get("content", "")
                expected = r["messages"][-1].get("content", "")
            elif "prompt" in r and "chosen" in r:
                prompt = r.get("prompt", "")
                expected = r.get("chosen", "")
            else:
                continue
            if prompt and expected:
                snippet = expected.strip().split("\n")[0][:160]
                tasks.append({
                    "prompt": prompt,
                    "expected": snippet,
                    "category": "dataset_regression",
                    "scoring": "contains",
                })
    if not tasks:
        raise ValueError("No evaluation tasks available. Supply tasks_inline or ensure dataset snapshot is non-empty.")
    storage.atomic_write_text(
        eval_path,
        "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks),
        fsync=True,
        backup=False,
    )
    return str(eval_path), len(tasks)
def evaluate_version(version_id, tasks_inline=None, min_accuracy=0.0, soup_cmd=None):
    ver = get_model_version(version_id)
    soup_info = soup_adapter.detect_soup(custom_cmd=soup_cmd, probe_deps=True)
    if not soup_info.get("available"):
        raise RuntimeError("SOUP CLI is not available to run evaluation.")
    if not soup_info.get("capabilities", {}).get("eval"):
        raise RuntimeError("SOUP eval dependencies are missing (install soup-cli[train]).")
    tasks_jsonl, task_count = _build_eval_tasks_for_version(ver, tasks_inline=tasks_inline)
    run_dir = _runs_dir() / ver["job_id"]
    base_json = run_dir / f"eval-baseline-{version_id}.json"
    cand_json = run_dir / f"eval-candidate-{version_id}.json"
    cand_model_target = ver.get("merged_dir") or ver.get("artifact_path")
    if not cand_model_target or not os.path.exists(str(cand_model_target)):
        raise FileNotFoundError(f"Candidate artifact not found at '{cand_model_target}'.")
    base_res = soup_adapter.run_custom_eval(soup_info, ver["base_model"], tasks_jsonl, str(base_json))
    if not base_res["ok"] or not base_res.get("metrics"):
        raise RuntimeError(f"Baseline eval failed: {(base_res['stderr'] or base_res['stdout'])[:300]}")
    cand_res = soup_adapter.run_custom_eval(soup_info, cand_model_target, tasks_jsonl, str(cand_json))
    if not cand_res["ok"] or not cand_res.get("metrics"):
        raise RuntimeError(f"Candidate eval failed: {(cand_res['stderr'] or cand_res['stdout'])[:300]}")
    b_metrics = base_res["metrics"]
    c_metrics = cand_res["metrics"]
    b_acc = float(b_metrics.get("accuracy") or 0.0)
    c_acc = float(c_metrics.get("accuracy") or 0.0)
    delta = round(c_acc - b_acc, 4)
    passed = (c_acc >= float(min_accuracy)) and (c_acc >= b_acc)
    with _STATE_LOCK:
        reg = _load_registry()
        for item in reg["versions"]:
            if item.get("version_id") == version_id:
                item["eval_baseline"] = b_metrics
                item["eval_candidate"] = c_metrics
                item["comparison"] = {
                    "task_count": task_count,
                    "baseline_accuracy": b_acc,
                    "candidate_accuracy": c_acc,
                    "delta_accuracy": delta,
                    "improved": c_acc > b_acc,
                    "no_regression": c_acc >= b_acc,
                    "evaluated_at": time.time(),
                }
                item["eval_status"] = "passed" if passed else "failed"
                updated = dict(item)
                break
        _save_registry(reg)
    feedback._append_audit("model_version_evaluated", {
        "version_id": version_id,
        "baseline_accuracy": b_acc,
        "candidate_accuracy": c_acc,
        "delta": delta,
        "eval_status": updated["eval_status"],
    })
    return updated
def prepare_version_artifact(version_id, target="merged", quant="q4_k_m", soup_cmd=None):
    ver = get_model_version(version_id)
    soup_info = soup_adapter.detect_soup(custom_cmd=soup_cmd, probe_deps=True)
    if not soup_info.get("available"):
        raise RuntimeError("SOUP CLI is not available.")
    run_dir = _runs_dir() / ver["job_id"]
    run_dir.mkdir(parents=True, exist_ok=True)
    mode = str(target or "merged").strip().lower()
    adapter_dir = ver.get("adapter_dir") or ver.get("artifact_path")
    merged_dir = ver.get("merged_dir")
    if mode in ("merged", "gguf") and not merged_dir:
        if not adapter_dir or not os.path.exists(str(adapter_dir)):
            raise FileNotFoundError("Trained adapter directory not found.")
        dest_merged = str(run_dir / "merged")
        m_res = soup_adapter.run_merge(
            soup_info,
            adapter_dir,
            dest_merged,
            base_model=ver.get("base_model"),
        )
        if not m_res["ok"] or not m_res.get("output_dir"):
            raise RuntimeError(f"soup merge failed: {(m_res['stderr'] or m_res['stdout'])[:300]}")
        merged_dir = m_res["output_dir"]
    gguf_path = ver.get("gguf_path")
    if mode == "gguf":
        q = str(quant or "q4_k_m").strip().lower()
        dest_gguf = str(run_dir / f"model-{version_id}-{q}.gguf")
        src_for_export = merged_dir or adapter_dir
        e_res = soup_adapter.run_export_gguf(
            soup_info,
            src_for_export,
            dest_gguf,
            quant=q,
            base_model=ver.get("base_model"),
        )
        if not e_res["ok"] or not e_res.get("output_file"):
            raise RuntimeError(f"soup export gguf failed: {(e_res['stderr'] or e_res['stdout'])[:300]}")
        gguf_path = e_res["output_file"]
    data_root = paths.data_dir()
    with _STATE_LOCK:
        reg = _load_registry()
        updated = None
        for item in reg["versions"]:
            if item.get("version_id") == version_id:
                if merged_dir:
                    item["merged_dir"] = merged_dir
                    item["merged_rel_dir"] = storage.normalize_rel_path(merged_dir, data_root)
                if gguf_path:
                    item["gguf_path"] = gguf_path
                    item["gguf_rel_path"] = storage.normalize_rel_path(gguf_path, data_root)
                    item["artifact_type"] = "gguf"
                    item["artifact_path"] = gguf_path
                    item["rel_path"] = storage.normalize_rel_path(gguf_path, data_root)
                elif merged_dir:
                    item["artifact_type"] = "merged_hf"
                    item["artifact_path"] = merged_dir
                    item["rel_path"] = storage.normalize_rel_path(merged_dir, data_root)
                updated = dict(item)
                break
        _save_registry(reg)
    feedback._append_audit("model_artifact_prepared", {"version_id": version_id, "mode": mode})
    return updated
def approve_version(version_id, approved=True, notes=None):
    _validate_safe_id(version_id, "version_id")
    with _STATE_LOCK:
        reg = _load_registry()
        target = None
        for item in reg["versions"]:
            if item.get("version_id") == version_id:
                item["approval_status"] = "approved" if approved else "rejected"
                if notes is not None:
                    item["notes"] = str(notes)[:500]
                target = dict(item)
                break
        if target is None:
            raise KeyError(f"Model version '{version_id}' not found.")
        _save_registry(reg)
    feedback._append_audit("model_version_approval", {"version_id": version_id, "approved": bool(approved)})
    return target
def deploy_version(version_id, prefer="auto", load_into_engine=True, require_approval=True):
    ver = get_model_version(version_id)
    if require_approval and ver.get("approval_status") != "approved":
        raise PermissionError(f"Model version '{version_id}' must be approved by the owner before deployment.")
    pref = str(prefer or "auto").strip().lower()
    target_path = None
    if pref == "gguf" and ver.get("gguf_path"):
        target_path = ver["gguf_path"]
    elif pref == "merged" and ver.get("merged_dir"):
        target_path = ver["merged_dir"]
    elif pref == "adapter" and ver.get("adapter_dir"):
        target_path = ver["adapter_dir"]
    else:
        target_path = ver.get("gguf_path") or ver.get("merged_dir") or ver.get("artifact_path")
    if not target_path or not os.path.exists(str(target_path)):
        raise FileNotFoundError(f"Deployable model artifact not found for version '{version_id}': {target_path}")
    with _STATE_LOCK:
        reg = _load_registry()
        prev_vid = reg.get("active_version_id")
        prev_model = reg.get("active_engine_model_id") or engine_instance.active_model_id
    load_res = None
    if load_into_engine:
        load_res = engine_instance.load_model(str(target_path))
        if isinstance(load_res, dict) and load_res.get("mode") == "veto":
            raise RuntimeError(f"Deployment blocked by SHARD hardware fit check for '{target_path}'.")
    else:
        _sync_active_model_file(target_path)
    with _STATE_LOCK:
        reg = _load_registry()
        reg["previous_version_id"] = prev_vid
        reg["previous_engine_model_id"] = prev_model
        reg["active_version_id"] = version_id
        reg["active_engine_model_id"] = str(target_path)
        now = time.time()
        deployed_ver = None
        for item in reg["versions"]:
            is_target = item.get("version_id") == version_id
            item["deployed"] = is_target
            if is_target:
                item["deployed_at"] = now
                deployed_ver = dict(item)
        _save_registry(reg)
    feedback._append_audit("model_version_deployed", {
        "version_id": version_id,
        "artifact_path": str(target_path),
        "previous_version_id": prev_vid,
    })
    return {
        "status": "deployed",
        "version": deployed_ver,
        "artifact_path": str(target_path),
        "previous_version_id": prev_vid,
        "previous_engine_model_id": prev_model,
        "engine_load": load_res,
    }
def rollback_deployment(target_version_id=None, load_into_engine=True):
    if target_version_id is not None:
        _validate_safe_id(target_version_id, "version_id")
    with _STATE_LOCK:
        reg = _load_registry()
        cur_vid = reg.get("active_version_id")
        prev_vid = target_version_id or reg.get("previous_version_id")
        prev_model = reg.get("previous_engine_model_id")
        if prev_vid:
            chosen_ver = None
            for item in reg["versions"]:
                if item.get("version_id") == prev_vid:
                    chosen_ver = item
                    break
            if chosen_ver is None:
                raise KeyError(f"Rollback target version '{prev_vid}' not found.")
            restore_path = chosen_ver.get("gguf_path") or chosen_ver.get("merged_dir") or chosen_ver.get("artifact_path")
        elif prev_model:
            restore_path = prev_model
        elif reg["versions"]:
            restore_path = reg["versions"][-1].get("base_model")
        else:
            raise ValueError("No previous model version or baseline recorded to roll back to.")
    if not restore_path:
        raise ValueError("Unable to resolve previous model ID or artifact path for rollback.")
    rp_str = str(restore_path)
    is_local_target = bool(prev_vid) or os.path.isabs(rp_str) or rp_str.lower().endswith(".gguf") or "\\" in rp_str
    if is_local_target and not os.path.exists(rp_str):
        raise FileNotFoundError(f"Rollback artifact not found at '{rp_str}'.")
    load_res = None
    if load_into_engine:
        load_res = engine_instance.load_model(rp_str)
    else:
        _sync_active_model_file(rp_str)
    with _STATE_LOCK:
        reg = _load_registry()
        reg["previous_version_id"] = cur_vid
        reg["previous_engine_model_id"] = reg.get("active_engine_model_id")
        reg["active_version_id"] = prev_vid
        reg["active_engine_model_id"] = rp_str
        for item in reg["versions"]:
            item["deployed"] = bool(prev_vid and item.get("version_id") == prev_vid)
        _save_registry(reg)
    feedback._append_audit("model_rollback_executed", {
        "from_version_id": cur_vid,
        "to_version_id": prev_vid,
        "restored_model": rp_str,
    })
    return {
        "status": "rolled_back",
        "from_version_id": cur_vid,
        "restored_version_id": prev_vid,
        "restored_model": rp_str,
        "engine_load": load_res,
    }
def create_backup(output_path=None, include_runs=True, include_datasets=True):
    res = storage.create_state_backup(
        paths.data_dir(),
        paths.config_dir(),
        output_path=output_path,
        include_runs=include_runs,
        include_datasets=include_datasets,
    )
    feedback._append_audit("state_backup_created", {
        "file_count": res.get("file_count"),
        "size_bytes": res.get("size_bytes"),
    })
    return res
def restore_backup(archive_path, verify_hashes=True):
    with _STATE_LOCK:
        jobs = reconcile_jobs()
        if _active_job_unlocked(jobs) is not None:
            raise RuntimeError("Stop active training jobs before restoring a state backup.")
        res = storage.restore_state_backup(
            archive_path,
            paths.data_dir(),
            paths.config_dir(),
            verify_hashes=verify_hashes,
        )
    feedback._append_audit("state_backup_restored", {
        "restored_files": res.get("restored_files"),
    })
    return res
def shutdown_active_jobs():
    procs_to_stop = []
    with _STATE_LOCK:
        for jid, proc in list(_ACTIVE_PROCS.items()):
            if proc is not None and proc.poll() is None:
                procs_to_stop.append((jid, proc))
        if procs_to_stop:
            jobs = _load_jobs()
            stop_ids = {jid for jid, _ in procs_to_stop}
            for j in jobs:
                if j.get("job_id") in stop_ids and j.get("status") in ("queued", "validating", "running"):
                    j["status"] = "interrupted"
                    j["finished_at"] = time.time()
                    j["error"] = "Interrupted by server shutdown."
            _save_jobs(jobs)
    for _, proc in procs_to_stop:
        try:
            soup_adapter.terminate_process(proc)
        except Exception:
            pass
    return len(procs_to_stop)
def training_overview():
    soup_info = soup_adapter.detect_soup(probe_deps=False)
    privacy = feedback.get_privacy_config()
    stats = feedback.compute_stats()
    datasets = feedback.list_datasets()
    jobs = list_training_jobs(limit=20)
    registry = list_model_versions()
    eng = engine_instance.get_status()
    return {
        "soup": soup_info,
        "privacy": privacy,
        "feedback_stats": stats,
        "datasets": datasets,
        "jobs": jobs,
        "registry": registry,
        "engine": {
            "status": eng.get("status"),
            "active_model": eng.get("active_model"),
            "is_loaded": eng.get("is_loaded"),
        },
        "architecture_notes": {
            "distributed_training": "Single-node local SOUP training on host. SHARD ggml-rpc LAN pooling is inference-only.",
            "raw_chat_ingestion": "Disabled. Only explicit user feedback with consent is recorded.",
        },
    }
